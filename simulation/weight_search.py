"""Seeded, out-of-sample search for Legacy's eight placement weights."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from models.profile_store import ProfileStore
from services.scheduler import LegacyWeights
from simulation.engine import SimulationResult, Simulator
from simulation.experiments import sample_cluster_uncertainty
from simulation.legacy_weights import LegacyScoringPolicy, build_warm_profile
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster, load_workflow


WEIGHT_FIELDS = tuple(LegacyWeights.__dataclass_fields__)
BASELINE_WEIGHTS = LegacyWeights()
ABSOLUTE_WEIGHT_SCALE = sum(
    abs(getattr(BASELINE_WEIGHTS, field)) for field in WEIGHT_FIELDS)
OBJECTIVE_COMPONENTS = {
    "mean_makespan_ratio": 0.45,
    "p95_makespan_ratio": 0.20,
    "mean_response_ratio": 0.15,
    "mean_energy_ratio": 0.10,
    "mean_transfer_ratio": 0.05,
    "deadline_lateness_ratio": 0.05,
}


@dataclass(frozen=True)
class SearchCase:
    cluster: str
    workflow: str
    seed: int

    @property
    def key(self) -> str:
        return f"{self.cluster}/{self.workflow}/seed-{self.seed}"


@dataclass(frozen=True)
class RunMetrics:
    case: str
    makespan_s: float
    mean_response_s: float
    energy_j: float
    network_transfer_mib: float
    deadline_s: float | None
    lateness_s: float
    deadline_missed: bool
    task_retries: int
    node_failures: int
    peak_temperature_c: float


@dataclass(frozen=True)
class CandidateScore:
    weights: LegacyWeights
    objective: float
    components: dict[str, float]
    deadline_misses: int
    task_retries: int


@dataclass(frozen=True)
class _PreparedCase:
    case: SearchCase
    cluster: ClusterSpec
    workflow: WorkflowSpec
    warm_profile: ProfileStore


def normalize_weights(values: dict[str, float]) -> LegacyWeights:
    """Fix signs and total scale so only relative factor importance changes."""
    if set(values) != set(WEIGHT_FIELDS):
        raise ValueError(f"weights must contain exactly {WEIGHT_FIELDS}")
    normalized: dict[str, float] = {}
    total = 0.0
    for field in WEIGHT_FIELDS:
        value = float(values[field])
        if not math.isfinite(value):
            raise ValueError(f"weight '{field}' must be finite")
        baseline = getattr(BASELINE_WEIGHTS, field)
        if baseline > 0 and value < 0:
            raise ValueError(f"reward weight '{field}' cannot be negative")
        if baseline < 0 and value > 0:
            raise ValueError(f"penalty weight '{field}' cannot be positive")
        total += abs(value)
    if total <= 0:
        raise ValueError("at least one weight must be non-zero")
    scale = ABSOLUTE_WEIGHT_SCALE / total
    for field in WEIGHT_FIELDS:
        normalized[field] = float(values[field]) * scale
    return LegacyWeights(**normalized)


def generate_candidates(count: int, *, seed: int = 2026) -> list[LegacyWeights]:
    """Generate reproducible local-to-broad log-normal perturbations."""
    if count < 1:
        raise ValueError("count must be at least 1")
    rng = random.Random(seed)
    candidates = [BASELINE_WEIGHTS]
    seen = {tuple(getattr(BASELINE_WEIGHTS, field) for field in WEIGHT_FIELDS)}
    temperatures = (0.20, 0.40, 0.70, 1.10)
    while len(candidates) < count:
        temperature = temperatures[(len(candidates) - 1) % len(temperatures)]
        raw = {}
        for field in WEIGHT_FIELDS:
            baseline = getattr(BASELINE_WEIGHTS, field)
            magnitude = abs(baseline) * math.exp(rng.gauss(0.0, temperature))
            raw[field] = math.copysign(magnitude, baseline)
        candidate = normalize_weights(raw)
        key = tuple(round(getattr(candidate, field), 12) for field in WEIGHT_FIELDS)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


def leave_one_factor_out(weights: LegacyWeights) -> dict[str, LegacyWeights]:
    """Return scale-normalized variants with each factor removed once."""
    variants = {}
    for omitted in WEIGHT_FIELDS:
        values = asdict(weights)
        values[omitted] = 0.0
        variants[f"without-{omitted.replace('_', '-')}"] = normalize_weights(values)
    return variants


def _nearest_rank_p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _ratio(value: float, baseline: float) -> float:
    if baseline > 1e-9:
        return value / baseline
    return 1.0 if value <= 1e-9 else 1.0 + value


def score_metrics(weights: LegacyWeights, metrics: Sequence[RunMetrics],
                  baseline: Sequence[RunMetrics]) -> CandidateScore:
    """Calculate the fixed balanced objective relative to Legacy v1."""
    if not metrics or len(metrics) != len(baseline):
        raise ValueError("candidate and baseline metrics must be non-empty and aligned")
    if [item.case for item in metrics] != [item.case for item in baseline]:
        raise ValueError("candidate and baseline cases are not aligned")
    makespan_ratios = [
        _ratio(item.makespan_s, base.makespan_s)
        for item, base in zip(metrics, baseline)
    ]
    response_ratios = [
        _ratio(item.mean_response_s, base.mean_response_s)
        for item, base in zip(metrics, baseline)
    ]
    energy_ratios = [
        _ratio(item.energy_j, base.energy_j)
        for item, base in zip(metrics, baseline)
    ]
    transfer_ratios = [
        _ratio(item.network_transfer_mib, base.network_transfer_mib)
        for item, base in zip(metrics, baseline)
    ]
    deadline_changes = [
        (item.lateness_s - base.lateness_s)
        / max(item.deadline_s or base.makespan_s, 1.0)
        for item, base in zip(metrics, baseline)
    ]
    components = {
        "mean_makespan_ratio": statistics.fmean(makespan_ratios),
        "p95_makespan_ratio": _nearest_rank_p95(makespan_ratios),
        "mean_response_ratio": statistics.fmean(response_ratios),
        "mean_energy_ratio": statistics.fmean(energy_ratios),
        "mean_transfer_ratio": statistics.fmean(transfer_ratios),
        "deadline_lateness_ratio": max(
            0.0, 1.0 + statistics.fmean(deadline_changes)),
    }
    objective = sum(
        OBJECTIVE_COMPONENTS[name] * value
        for name, value in components.items()
    )
    return CandidateScore(
        weights=weights,
        objective=objective,
        components=components,
        deadline_misses=sum(item.deadline_missed for item in metrics),
        task_retries=sum(item.task_retries for item in metrics),
    )


def _metrics(case: SearchCase, workflow: WorkflowSpec,
             result: SimulationResult) -> RunMetrics:
    responses = [
        task.finished_at_s - task.ready_at_s for task in result.tasks.values()
    ]
    deadline = workflow.deadline_s
    return RunMetrics(
        case=case.key,
        makespan_s=result.makespan_s,
        mean_response_s=statistics.fmean(responses),
        energy_j=sum(node.energy_j for node in result.nodes.values()),
        network_transfer_mib=result.network_transfer_mib,
        deadline_s=deadline,
        lateness_s=max(0.0, result.makespan_s - deadline) if deadline else 0.0,
        deadline_missed=bool(deadline is not None and result.makespan_s > deadline),
        task_retries=sum(task.attempts - 1 for task in result.tasks.values()),
        node_failures=sum(node.failures for node in result.nodes.values()),
        peak_temperature_c=max(
            node.peak_temperature_c for node in result.nodes.values()),
    )


class WeightEvaluator:
    """Load and prewarm a fixed corpus once, then evaluate many candidates."""

    def __init__(self, root: Path, cases: Sequence[SearchCase], *,
                 profile_seed: int = 99173,
                 sample_uncertainty: bool = True):
        cluster_dir = root / "scenarios" / "clusters"
        workflow_dir = root / "scenarios" / "workflows"
        base_clusters: dict[str, ClusterSpec] = {}
        workflows: dict[str, WorkflowSpec] = {}
        profiles: dict[tuple[str, str], ProfileStore] = {}
        prepared = []
        for case in cases:
            base_cluster = base_clusters.setdefault(
                case.cluster, load_cluster(cluster_dir / f"{case.cluster}.yaml"))
            workflow = workflows.setdefault(
                case.workflow, load_workflow(workflow_dir / f"{case.workflow}.yaml"))
            profile_key = (case.cluster, case.workflow)
            if profile_key not in profiles:
                profiles[profile_key] = build_warm_profile(
                    base_cluster, workflow, seed=profile_seed)
            cluster = (
                sample_cluster_uncertainty(base_cluster, case.seed)
                if sample_uncertainty else base_cluster
            )
            prepared.append(_PreparedCase(
                case=case,
                cluster=cluster,
                workflow=workflow,
                warm_profile=profiles[profile_key],
            ))
        self.prepared = tuple(prepared)

    def evaluate(self, weights: LegacyWeights, *, warm: bool = True) -> list[RunMetrics]:
        metrics = []
        for prepared in self.prepared:
            profile = prepared.warm_profile if warm else ProfileStore()
            result = Simulator(
                prepared.cluster,
                prepared.workflow,
                LegacyScoringPolicy(weights, profile),
                seed=prepared.case.seed,
            ).run()
            metrics.append(_metrics(prepared.case, prepared.workflow, result))
        return metrics


def make_cases(clusters: Iterable[str], workflows: Iterable[str],
               seeds: Iterable[int]) -> list[SearchCase]:
    return [
        SearchCase(cluster, workflow, seed)
        for cluster in clusters
        for workflow in workflows
        for seed in seeds
    ]


def _score_dict(score: CandidateScore) -> dict:
    return {
        "weights": asdict(score.weights),
        "objective": score.objective,
        "components": score.components,
        "deadline_misses": score.deadline_misses,
        "task_retries": score.task_retries,
    }


def _metric_summary(metrics: Sequence[RunMetrics]) -> dict[str, float | int]:
    return {
        "runs": len(metrics),
        "mean_makespan_s": statistics.fmean(item.makespan_s for item in metrics),
        "p95_makespan_s": _nearest_rank_p95([item.makespan_s for item in metrics]),
        "mean_response_s": statistics.fmean(item.mean_response_s for item in metrics),
        "mean_energy_j": statistics.fmean(item.energy_j for item in metrics),
        "mean_transfer_mib": statistics.fmean(
            item.network_transfer_mib for item in metrics),
        "deadline_misses": sum(item.deadline_missed for item in metrics),
        "task_retries": sum(item.task_retries for item in metrics),
        "node_failures": sum(item.node_failures for item in metrics),
        "peak_temperature_c": max(item.peak_temperature_c for item in metrics),
    }


def _comparison(evaluator: WeightEvaluator, tuned: LegacyWeights,
                *, warm: bool) -> dict:
    baseline_metrics = evaluator.evaluate(BASELINE_WEIGHTS, warm=warm)
    tuned_metrics = evaluator.evaluate(tuned, warm=warm)
    return {
        "baseline": _metric_summary(baseline_metrics),
        "tuned": _metric_summary(tuned_metrics),
        "tuned_relative_to_baseline": _score_dict(
            score_metrics(tuned, tuned_metrics, baseline_metrics)),
        "per_case": [
            {
                "case": tuned_item.case,
                "baseline_makespan_s": baseline_item.makespan_s,
                "tuned_makespan_s": tuned_item.makespan_s,
                "makespan_ratio": _ratio(
                    tuned_item.makespan_s, baseline_item.makespan_s),
            }
            for tuned_item, baseline_item in zip(tuned_metrics, baseline_metrics)
        ],
    }


def search_weights(root: Path, train_cases: Sequence[SearchCase],
                   validation_cases: Sequence[SearchCase],
                   holdout_cases: Sequence[SearchCase], *,
                   candidate_count: int = 128,
                   finalist_count: int = 12,
                   search_seed: int = 2026,
                   progress: Callable[[str], None] | None = None) -> dict:
    """Search on train, select on validation, then report untouched holdout."""
    if finalist_count < 1:
        raise ValueError("finalist_count must be at least 1")
    train = WeightEvaluator(root, train_cases)
    train_baseline = train.evaluate(BASELINE_WEIGHTS)
    training_scores = []
    for index, weights in enumerate(
            generate_candidates(candidate_count, seed=search_seed), start=1):
        score = score_metrics(weights, train.evaluate(weights), train_baseline)
        training_scores.append(score)
        if progress:
            progress(
                f"train {index}/{candidate_count}: objective={score.objective:.6f}")
    training_scores.sort(key=lambda item: item.objective)

    finalists = training_scores[:min(finalist_count, len(training_scores))]
    if not any(item.weights == BASELINE_WEIGHTS for item in finalists):
        baseline_score = next(
            item for item in training_scores if item.weights == BASELINE_WEIGHTS)
        finalists.append(baseline_score)

    validation = WeightEvaluator(root, validation_cases)
    validation_baseline = validation.evaluate(BASELINE_WEIGHTS)
    validation_scores = []
    for index, finalist in enumerate(finalists, start=1):
        score = score_metrics(
            finalist.weights,
            validation.evaluate(finalist.weights),
            validation_baseline,
        )
        validation_scores.append(score)
        if progress:
            progress(
                f"validation {index}/{len(finalists)}: "
                f"objective={score.objective:.6f}")
    validation_scores.sort(key=lambda item: item.objective)
    baseline_validation = next(
        item for item in validation_scores if item.weights == BASELINE_WEIGHTS)
    eligible = [
        item for item in validation_scores
        if item.objective <= baseline_validation.objective
        and item.components["p95_makespan_ratio"] <= 1.05
        and item.deadline_misses <= baseline_validation.deadline_misses
        and item.task_retries <= baseline_validation.task_retries
    ]
    selected = min(eligible or [baseline_validation], key=lambda item: item.objective)

    holdout = WeightEvaluator(root, holdout_cases)
    report = {
        "schema_version": 1,
        "method": {
            "search": "seeded log-normal random search",
            "search_seed": search_seed,
            "candidate_count": candidate_count,
            "finalist_count": len(finalists),
            "absolute_weight_scale": ABSOLUTE_WEIGHT_SCALE,
            "objective": OBJECTIVE_COMPONENTS,
            "selection_guardrails": {
                "validation_objective": "not worse than baseline",
                "validation_p95_makespan_ratio_max": 1.05,
                "deadline_misses": "not worse than baseline",
                "task_retries": "not worse than baseline",
            },
            "profiles": "12 seeded calibration observations per compatible task/node",
            "calibration_uncertainty": "sampled per evaluation seed",
        },
        "splits": {
            "train": [asdict(case) for case in train_cases],
            "validation": [asdict(case) for case in validation_cases],
            "holdout": [asdict(case) for case in holdout_cases],
        },
        "baseline_weights": asdict(BASELINE_WEIGHTS),
        "selected_weights": asdict(selected.weights),
        "training_leaderboard": [
            _score_dict(item) for item in training_scores],
        "validation_leaderboard": [
            _score_dict(item) for item in validation_scores],
        "holdout": {
            "warm_profiles": _comparison(holdout, selected.weights, warm=True),
            "cold_profiles": _comparison(holdout, selected.weights, warm=False),
        },
    }
    return report