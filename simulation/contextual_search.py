"""Out-of-sample search for a robust contextual scheduling portfolio."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from simulation.benchmark_suite import BenchmarkCase, load_benchmark_suite
from simulation.contextual_policy import (
    ContextualPolicyProfile,
    ContextualPortfolioPolicy,
    PolicyChoice,
    scheduling_context,
)
from simulation.engine import Simulator
from simulation.policies import AdaptivePolicy, AdaptiveWeights, create_policy
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster


OBJECTIVE_WEIGHTS = {
    "mean_makespan_ratio": 0.60,
    "p95_makespan_ratio": 0.25,
    "mean_energy_ratio": 0.10,
    "mean_transfer_ratio": 0.05,
}
MIN_VALIDATION_OBJECTIVE_GAIN = 0.005


@dataclass(frozen=True)
class PolicyCandidate:
    name: str
    algorithm: str
    adaptive_weights: AdaptiveWeights | None = None

    def choice(self) -> PolicyChoice:
        return PolicyChoice(self.algorithm, self.adaptive_weights)


@dataclass(frozen=True)
class EvaluationMetric:
    key: str
    context: str
    makespan_s: float
    energy_j: float
    transfer_mib: float


@dataclass(frozen=True)
class ObjectiveScore:
    objective: float
    components: Mapping[str, float]
    cases: int


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _ratio(value: float, reference: float) -> float:
    if reference > 1e-9:
        return value / reference
    return 1.0 if value <= 1e-9 else 1.0 + value


def score_metrics(metrics: Sequence[EvaluationMetric],
                  reference: Sequence[EvaluationMetric]) -> ObjectiveScore:
    by_key = {metric.key: metric for metric in metrics}
    reference_by_key = {metric.key: metric for metric in reference}
    if not by_key or set(by_key) != set(reference_by_key):
        raise ValueError("candidate and reference metrics must be non-empty and aligned")
    ordered_keys = sorted(by_key)
    makespan_ratios = [
        _ratio(by_key[key].makespan_s, reference_by_key[key].makespan_s)
        for key in ordered_keys
    ]
    energy_ratios = [
        _ratio(by_key[key].energy_j, reference_by_key[key].energy_j)
        for key in ordered_keys
    ]
    transfer_ratios = [
        _ratio(by_key[key].transfer_mib, reference_by_key[key].transfer_mib)
        for key in ordered_keys
    ]
    components = {
        "mean_makespan_ratio": statistics.fmean(makespan_ratios),
        "p95_makespan_ratio": _p95(makespan_ratios),
        "mean_energy_ratio": statistics.fmean(energy_ratios),
        "mean_transfer_ratio": statistics.fmean(transfer_ratios),
    }
    return ObjectiveScore(
        objective=sum(
            OBJECTIVE_WEIGHTS[name] * value
            for name, value in components.items()
        ),
        components=components,
        cases=len(ordered_keys),
    )


def generate_adaptive_candidates(count: int, *, seed: int = 2026
                                 ) -> tuple[PolicyCandidate, ...]:
    """Generate deterministic log-uniform candidates, including safe anchors."""
    if count < 2:
        raise ValueError("adaptive candidate count must be at least 2")
    candidates = [
        PolicyCandidate(
            "adaptive-heft-equivalent",
            "adaptive",
            AdaptiveWeights(0.0, 0.0, 0.0, 0.0),
        ),
        PolicyCandidate("adaptive-default", "adaptive", AdaptiveWeights()),
    ]
    rng = random.Random(seed)

    def sample(low_exponent: float, high_exponent: float,
               zero_probability: float = 0.15) -> float:
        if rng.random() < zero_probability:
            return 0.0
        return 10.0 ** rng.uniform(low_exponent, high_exponent)

    seen = {candidate.adaptive_weights for candidate in candidates}
    while len(candidates) < count:
        weights = AdaptiveWeights(
            energy_s_per_j=sample(-6.0, -2.0),
            transfer_s_per_mib=sample(-5.0, -1.0),
            thermal_penalty_s=sample(-1.0, 2.3),
            failure_penalty_s=sample(0.0, 3.0),
        )
        if weights in seen:
            continue
        seen.add(weights)
        candidates.append(PolicyCandidate(
            f"adaptive-search-{len(candidates) - 1:03d}",
            "adaptive",
            weights,
        ))
    return tuple(candidates)


def _build_policy(candidate: PolicyCandidate, cluster: ClusterSpec,
                  workflow: WorkflowSpec, seed: int):
    if candidate.algorithm.startswith("adaptive"):
        policy = AdaptivePolicy(candidate.adaptive_weights)
        if candidate.algorithm == "adaptive-no-preempt":
            policy.preemptive = False
        return policy
    return create_policy(
        candidate.algorithm,
        cluster=cluster,
        workflow=workflow,
        seed=seed,
    )


def evaluate_candidate(candidate: PolicyCandidate,
                       clusters: Sequence[ClusterSpec],
                       cases: Sequence[BenchmarkCase],
                       seeds: Sequence[int], *,
                       thermal_step_s: float = 60.0,
                       progress: Callable[[str], None] | None = None,
                       ) -> tuple[EvaluationMetric, ...]:
    metrics = []
    for cluster in clusters:
        for case in cases:
            for seed in seeds:
                policy = _build_policy(candidate, cluster, case.workflow, seed)
                result = Simulator(
                    cluster,
                    case.workflow,
                    policy,
                    thermal_step_s=thermal_step_s,
                    seed=seed,
                ).run()
                metrics.append(EvaluationMetric(
                    key=f"{cluster.cluster_id}/{case.case_id}/seed-{seed}",
                    context=scheduling_context(case.workflow).key,
                    makespan_s=result.makespan_s,
                    energy_j=sum(node.energy_j for node in result.nodes.values()),
                    transfer_mib=result.network_transfer_mib,
                ))
    if progress:
        progress(f"evaluated {candidate.name}: {len(metrics)} cases")
    return tuple(metrics)


def _stage(candidates: Sequence[PolicyCandidate],
           clusters: Sequence[ClusterSpec], cases: Sequence[BenchmarkCase],
           seeds: Sequence[int], *, keep: int, thermal_step_s: float,
           progress: Callable[[str], None] | None) -> tuple[
               list[tuple[PolicyCandidate, ObjectiveScore]],
               dict[str, tuple[EvaluationMetric, ...]],
           ]:
    reference_candidate = PolicyCandidate("heft-reference", "heft")
    reference = evaluate_candidate(
        reference_candidate, clusters, cases, seeds,
        thermal_step_s=thermal_step_s,
    )
    metrics_by_candidate = {}
    leaderboard = []
    for candidate in candidates:
        metrics = evaluate_candidate(
            candidate, clusters, cases, seeds,
            thermal_step_s=thermal_step_s,
        )
        metrics_by_candidate[candidate.name] = metrics
        score = score_metrics(metrics, reference)
        leaderboard.append((candidate, score))
        if progress:
            progress(
                f"{candidate.name}: objective={score.objective:.6f}, "
                f"mean={score.components['mean_makespan_ratio']:.4f}, "
                f"p95={score.components['p95_makespan_ratio']:.4f}"
            )
    leaderboard.sort(key=lambda row: (
        row[1].objective,
        row[1].components["p95_makespan_ratio"],
        row[0].name,
    ))
    return leaderboard[:max(1, min(keep, len(leaderboard)))], metrics_by_candidate


def _score_mapping(candidate: PolicyCandidate, score: ObjectiveScore) -> dict:
    return {
        "candidate": candidate.name,
        "algorithm": candidate.algorithm,
        "adaptive_weights": (
            asdict(candidate.adaptive_weights)
            if candidate.adaptive_weights is not None else None
        ),
        "objective": score.objective,
        "components": dict(score.components),
        "cases": score.cases,
    }


def _select_validated_specialist(
        candidates: Sequence[PolicyCandidate],
        scores: Mapping[str, ObjectiveScore],
        baseline_name: str) -> PolicyCandidate:
    baseline = next(
        candidate for candidate in candidates if candidate.name == baseline_name)
    baseline_score = scores[baseline_name]
    eligible = [
        candidate for candidate in candidates
        if candidate.name != baseline_name
        and scores[candidate.name].objective <= (
            baseline_score.objective - MIN_VALIDATION_OBJECTIVE_GAIN)
        and scores[candidate.name].components["mean_makespan_ratio"] <= (
            baseline_score.components["mean_makespan_ratio"] + 1e-12)
        and scores[candidate.name].components["p95_makespan_ratio"] <= (
            baseline_score.components["p95_makespan_ratio"] + 1e-12)
    ]
    return min(eligible or [baseline], key=lambda candidate: (
        scores[candidate.name].objective,
        scores[candidate.name].components["p95_makespan_ratio"],
        candidate.name,
    ))


def _stratified_cases(cases: Sequence[BenchmarkCase]) -> list[BenchmarkCase]:
    selected = {}
    for case in sorted(cases, key=lambda item: item.case_id):
        selected.setdefault(scheduling_context(case.workflow).key, case)
    return list(selected.values())


def search_adaptive_weights(root: Path, cases: Sequence[BenchmarkCase], *,
                            candidate_count: int = 64,
                            search_seed: int = 2026,
                            thermal_step_s: float = 60.0,
                            progress: Callable[[str], None] | None = None,
                            ) -> tuple[PolicyCandidate, dict]:
    clusters_by_name = {
        path.stem: load_cluster(path)
        for path in (root / "scenarios" / "clusters").glob("*.yaml")
    }
    train_cases = [case for case in cases if case.split == "train"]
    validation_cases = [case for case in cases if case.split == "validation"]
    generated = generate_adaptive_candidates(candidate_count, seed=search_seed)

    stages = []
    stage_one, _ = _stage(
        generated,
        [clusters_by_name["balanced-heterogeneous-4"],
         clusters_by_name["asymmetric-edge-5"]],
        _stratified_cases(train_cases),
        [3],
        keep=min(12, candidate_count),
        thermal_step_s=thermal_step_s,
        progress=progress,
    )
    stages.append({
        "name": "screen",
        "leaderboard": [_score_mapping(*row) for row in stage_one],
    })

    stage_two, _ = _stage(
        [candidate for candidate, _ in stage_one],
        [clusters_by_name[name] for name in (
            "balanced-heterogeneous-4", "asymmetric-edge-5",
            "unreliable-thermal-3")],
        train_cases,
        [5],
        keep=min(4, len(stage_one)),
        thermal_step_s=thermal_step_s,
        progress=progress,
    )
    stages.append({
        "name": "train",
        "leaderboard": [_score_mapping(*row) for row in stage_two],
    })

    validation_candidates = [candidate for candidate, _ in stage_two]
    adaptive_default = next(
        candidate for candidate in generated
        if candidate.name == "adaptive-default")
    if adaptive_default not in validation_candidates:
        validation_candidates.append(adaptive_default)
    stage_three, _ = _stage(
        validation_candidates,
        list(clusters_by_name.values()),
        validation_cases,
        [17, 29],
        keep=len(validation_candidates),
        thermal_step_s=thermal_step_s,
        progress=progress,
    )
    stages.append({
        "name": "validation",
        "leaderboard": [_score_mapping(*row) for row in stage_three],
    })
    validation_scores = {
        candidate.name: score for candidate, score in stage_three}
    selected = _select_validated_specialist(
        [candidate for candidate, _ in stage_three],
        validation_scores,
        "adaptive-default",
    )
    return selected, {
        "method": "successive halving with validation guardrails",
        "search_seed": search_seed,
        "candidate_count": candidate_count,
        "thermal_step_s": thermal_step_s,
        "objective_weights": OBJECTIVE_WEIGHTS,
        "minimum_validation_objective_gain": MIN_VALIDATION_OBJECTIVE_GAIN,
        "selected": selected.name,
        "selected_weights": asdict(selected.adaptive_weights),
        "stages": stages,
    }


def _metrics_for_context(metrics: Sequence[EvaluationMetric],
                         context: str) -> tuple[EvaluationMetric, ...]:
    return tuple(metric for metric in metrics if metric.context == context)


def fit_contextual_profile(root: Path, cases: Sequence[BenchmarkCase],
                           tuned_adaptive: PolicyCandidate, *,
                           thermal_step_s: float = 60.0,
                           progress: Callable[[str], None] | None = None,
                           ) -> tuple[ContextualPolicyProfile, dict]:
    cluster_names = (
        "balanced-heterogeneous-4", "asymmetric-edge-5",
        "unreliable-thermal-3",
    )
    clusters = [load_cluster(
        root / "scenarios" / "clusters" / f"{name}.yaml")
        for name in cluster_names
    ]
    validation_clusters = [
        load_cluster(path)
        for path in sorted((root / "scenarios" / "clusters").glob("*.yaml"))
    ]
    train_cases = [case for case in cases if case.split == "train"]
    validation_cases = [case for case in cases if case.split == "validation"]
    candidates = tuple({
        candidate.name: candidate for candidate in (
            PolicyCandidate("eft", "eft"),
            PolicyCandidate("heft", "heft"),
            PolicyCandidate("heft-ra", "heft-ra"),
            PolicyCandidate("legacy", "legacy"),
            PolicyCandidate("adaptive-default", "adaptive", AdaptiveWeights()),
            tuned_adaptive,
        )
    }.values())
    train_metrics = {
        candidate.name: evaluate_candidate(
            candidate, clusters, train_cases, [7],
            thermal_step_s=thermal_step_s,
            progress=progress,
        ) for candidate in candidates
    }
    validation_metrics = {
        candidate.name: evaluate_candidate(
            candidate, validation_clusters, validation_cases, [31, 47],
            thermal_step_s=thermal_step_s,
            progress=progress,
        ) for candidate in candidates
    }
    contexts = sorted({
        scheduling_context(case.workflow).key for case in train_cases
    })
    choices = {}
    report_contexts = {}
    for context in contexts:
        train_reference = _metrics_for_context(train_metrics["heft"], context)
        train_scores = {
            candidate.name: score_metrics(
                _metrics_for_context(train_metrics[candidate.name], context),
                train_reference,
            ) for candidate in candidates
        }
        finalists = sorted(candidates, key=lambda candidate: (
            train_scores[candidate.name].objective,
            train_scores[candidate.name].components["p95_makespan_ratio"],
            candidate.name,
        ))[:2]
        if all(candidate.name != "heft" for candidate in finalists):
            finalists.append(next(
                candidate for candidate in candidates if candidate.name == "heft"))

        validation_reference = _metrics_for_context(
            validation_metrics["heft"], context)
        validation_scores = {
            candidate.name: score_metrics(
                _metrics_for_context(
                    validation_metrics[candidate.name], context),
                validation_reference,
            ) for candidate in finalists
        }
        selected = _select_validated_specialist(
            finalists, validation_scores, "heft")
        choices[context] = selected.choice()
        report_contexts[context] = {
            "selected": selected.name,
            "training": {
                name: _score_mapping(
                    next(candidate for candidate in candidates
                         if candidate.name == name), score)
                for name, score in train_scores.items()
            },
            "validation": {
                name: _score_mapping(
                    next(candidate for candidate in candidates
                         if candidate.name == name), score)
                for name, score in validation_scores.items()
            },
        }
    profile = ContextualPolicyProfile(
        name="contextual-v1",
        default=PolicyChoice("heft"),
        contexts=choices,
        training_suite="generic-v1",
    )
    return profile, {
        "candidate_names": [candidate.name for candidate in candidates],
        "selection_guardrails": {
            "minimum_validation_objective_gain": MIN_VALIDATION_OBJECTIVE_GAIN,
            "mean_makespan": "not worse than baseline",
            "p95_makespan": "not worse than baseline",
        },
        "contexts": report_contexts,
    }


def evaluate_holdout(root: Path, cases: Sequence[BenchmarkCase],
                     profile: ContextualPolicyProfile,
                     tuned_adaptive: PolicyCandidate, *,
                     thermal_step_s: float = 60.0,
                     ) -> dict:
    clusters = [
        load_cluster(path)
        for path in sorted((root / "scenarios" / "clusters").glob("*.yaml"))
    ]
    holdout = [case for case in cases if case.split == "holdout"]
    baselines = tuple({
        candidate.name: candidate for candidate in (
            PolicyCandidate("eft", "eft"),
            PolicyCandidate("heft", "heft"),
            PolicyCandidate("heft-ra", "heft-ra"),
            PolicyCandidate("adaptive-default", "adaptive", AdaptiveWeights()),
            tuned_adaptive,
        )
    }.values())
    metrics = {
        candidate.name: evaluate_candidate(
            candidate, clusters, holdout, [101, 103, 107],
            thermal_step_s=thermal_step_s,
        ) for candidate in baselines
    }
    contextual_metrics = []
    for cluster in clusters:
        for case in holdout:
            for seed in (101, 103, 107):
                result = Simulator(
                    cluster,
                    case.workflow,
                    ContextualPortfolioPolicy(
                        profile, cluster, case.workflow, seed=seed),
                    thermal_step_s=thermal_step_s,
                    seed=seed,
                ).run()
                contextual_metrics.append(EvaluationMetric(
                    key=f"{cluster.cluster_id}/{case.case_id}/seed-{seed}",
                    context=scheduling_context(case.workflow).key,
                    makespan_s=result.makespan_s,
                    energy_j=sum(node.energy_j for node in result.nodes.values()),
                    transfer_mib=result.network_transfer_mib,
                ))
    reference = metrics["heft"]
    overall = {
        name: _score_mapping(
            PolicyCandidate(name, "heft") if name == "heft" else next(
                candidate for candidate in baselines if candidate.name == name),
            score_metrics(candidate_metrics, reference),
        )
        for name, candidate_metrics in metrics.items()
    }
    overall[profile.name] = {
        "candidate": profile.name,
        "algorithm": "contextual-portfolio",
        "adaptive_weights": None,
        "objective": score_metrics(contextual_metrics, reference).objective,
        "components": dict(score_metrics(
            contextual_metrics, reference).components),
        "cases": len(contextual_metrics),
    }
    per_context = {}
    for context in sorted({metric.context for metric in contextual_metrics}):
        contextual_score = score_metrics(
            _metrics_for_context(contextual_metrics, context),
            _metrics_for_context(reference, context),
        )
        per_context[context] = {
            "choice": profile.contexts.get(context, profile.default).to_mapping(),
            "objective": contextual_score.objective,
            "components": dict(contextual_score.components),
            "cases": contextual_score.cases,
        }
    return {
        "seeds": [101, 103, 107],
        "case_count": len(contextual_metrics),
        "overall_relative_to_heft": overall,
        "contextual_by_context": per_context,
    }


def load_default_suite(root: Path) -> tuple[BenchmarkCase, ...]:
    return load_benchmark_suite(root / "scenarios" / "benchmark-suite.yaml")