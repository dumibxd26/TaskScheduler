"""Experiment orchestration, lower bounds, oracle search, and robust analysis."""

from __future__ import annotations

import csv
import itertools
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable, Sequence

from simulation.engine import (
    InvalidPlacementError,
    SimulationDeadlockError,
    SimulationResult,
    Simulator,
)
from simulation.policies import StaticAssignmentPolicy, create_policy
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster, load_workflow
from simulation.workloads import load_workload, submission_results


REFERENCE_POLICIES = frozenset({"oracle-static"})


@dataclass(frozen=True)
class ExperimentRecord:
    cluster_id: str
    cluster_family: str
    workflow_id: str
    workflow_family: str
    policy: str
    seed: int
    makespan_s: float
    lower_bound_s: float
    bound_ratio: float
    energy_j: float
    network_transfer_mib: float
    cold_starts: int
    task_retries: int
    node_failures: int
    peak_temperature_c: float
    deadline_s: float | None
    deadline_met: bool | None
    lateness_s: float
    calibration_status: str = "unspecified"
    uncertainty_sampled: bool = False
    uncertainty_fraction: float = 0.0


@dataclass(frozen=True)
class WorkloadExperimentRecord:
    cluster_id: str
    cluster_family: str
    workload_id: str
    policy: str
    seed: int
    makespan_s: float
    mean_response_s: float
    p95_response_s: float
    deadline_hit_rate: float | None
    deadline_misses: int
    task_preemptions: int
    task_retries: int
    node_failures: int
    energy_j: float
    network_transfer_mib: float
    uncertainty_sampled: bool = False
    uncertainty_fraction: float = 0.0


def sample_cluster_uncertainty(cluster: ClusterSpec, seed: int,
                               floor: float = 0.0) -> ClusterSpec:
    """Sample parameter uncertainty without changing cluster structure.

    Each factor is uniform in [1 - u, 1 + u] where u is the larger of the
    cluster's declared uncertainty and ``floor``. A positive floor turns every
    seed into a distinct perturbed instance of a synthetic cluster.
    """
    uncertainty = max(cluster.calibration.uncertainty_fraction, floor)
    if uncertainty <= 0:
        return cluster
    digest = sha256(f"{cluster.cluster_id}:{seed}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))

    def factor() -> float:
        return rng.uniform(max(0.05, 1.0 - uncertainty), 1.0 + uncertainty)

    nodes = []
    for node in cluster.nodes:
        idle_power_w = node.cpu.idle_power_w * factor()
        # Sample the dynamic range separately so max power stays >= idle.
        max_power_w = idle_power_w + (
            node.cpu.max_power_w - node.cpu.idle_power_w) * factor()
        nodes.append(replace(
            node,
            cpu=replace(
                node.cpu,
                calibrated_gflops_per_core=(
                    node.cpu.calibrated_gflops_per_core * factor()),
                idle_power_w=idle_power_w,
                max_power_w=max_power_w,
            ),
            memory=replace(
                node.memory,
                calibrated_bandwidth_mib_s=(
                    node.memory.calibrated_bandwidth_mib_s * factor()),
                latency_ns=node.memory.latency_ns * factor(),
            ),
            storage=replace(
                node.storage,
                sequential_read_mib_s=node.storage.sequential_read_mib_s * factor(),
                sequential_write_mib_s=node.storage.sequential_write_mib_s * factor(),
                random_read_iops=node.storage.random_read_iops * factor(),
                random_write_iops=node.storage.random_write_iops * factor(),
            ),
            thermal=replace(
                node.thermal,
                thermal_resistance_c_per_w=(
                    node.thermal.thermal_resistance_c_per_w * factor()),
                thermal_capacitance_j_per_c=(
                    node.thermal.thermal_capacitance_j_per_c * factor()),
            ),
            failure_rate_per_hour=node.failure_rate_per_hour * factor(),
            mean_repair_s=node.mean_repair_s * factor(),
        ))
    links = tuple(replace(
        link,
        bandwidth_mib_s=link.bandwidth_mib_s * factor(),
        latency_ms=link.latency_ms * factor(),
        jitter_ms=link.jitter_ms * factor(),
    ) for link in cluster.links)
    return replace(cluster, nodes=tuple(nodes), links=links)


def workflow_lower_bound_s(cluster: ClusterSpec, workflow: WorkflowSpec) -> float:
    """Optimistic bound from critical path and aggregate resource capacities.

    Every term uses the most favourable conditions the engine can produce:
    unthrottled CPUs, a task's best compatible node, a private L3 cache for the
    DRAM-traffic estimate, no startup, no transfers, and no contention. The
    result is therefore a valid lower bound on any simulated makespan.
    """
    from simulation.estimation import isolated_execution_s
    from simulation.resources import cache_miss_fraction, node_cpu_capacity_gflops

    minimum_execution = {}
    minimum_dram_mib = {}
    for task in workflow.tasks:
        compatible = [
            node for node in cluster.nodes
            if node.cpu.architecture in task.supported_architectures
            and node.cpu.logical_cpus >= task.cpu_cores
            and node.memory.capacity_mib - node.background_memory_mib >= task.memory_mib
        ]
        minimum_execution[task.task_id] = min(
            isolated_execution_s(
                task, node,
                temperature_c=min(node.thermal.initial_c, node.thermal.throttle_c))
            for node in compatible
        )
        memory_mib = task.memory_read_mib + task.memory_write_mib
        minimum_dram_mib[task.task_id] = min(
            memory_mib * cache_miss_fraction(
                task.working_set_mib, task.cache_reuse_fraction,
                node.cpu.l3_cache_mib)
            for node in compatible
        )

    parents = {task.task_id: [] for task in workflow.tasks}
    for dependency in workflow.dependencies:
        parents[dependency.child].append(dependency.parent)
    critical_finishes: dict[str, float] = {}

    def critical_finish(task_id: str) -> float:
        if task_id not in critical_finishes:
            critical_finishes[task_id] = minimum_execution[task_id] + max(
                (critical_finish(parent) for parent in parents[task_id]),
                default=0.0,
            )
        return critical_finishes[task_id]

    critical_path = max(critical_finish(task.task_id) for task in workflow.tasks)
    total_compute = sum(task.compute_gflop for task in workflow.tasks)
    total_memory = sum(minimum_dram_mib.values())
    total_disk_read = sum(task.disk_read_mib for task in workflow.tasks)
    total_disk_write = sum(task.disk_write_mib for task in workflow.tasks)
    cpu_capacity = sum(node_cpu_capacity_gflops(node) for node in cluster.nodes)
    memory_capacity = sum(
        node.memory.calibrated_bandwidth_mib_s for node in cluster.nodes)
    read_capacity = sum(
        node.storage.sequential_read_mib_s for node in cluster.nodes)
    write_capacity = sum(
        node.storage.sequential_write_mib_s for node in cluster.nodes)
    return max(
        critical_path,
        total_compute / cpu_capacity if total_compute else 0.0,
        total_memory / memory_capacity if total_memory else 0.0,
        total_disk_read / read_capacity if total_disk_read else 0.0,
        total_disk_write / write_capacity if total_disk_write else 0.0,
    )


def _record(cluster: ClusterSpec, workflow: WorkflowSpec, policy: str,
            seed: int, result: SimulationResult,
            lower_bound_s: float, *, uncertainty_sampled: bool = False,
            uncertainty_fraction: float = 0.0) -> ExperimentRecord:
    deadline_met = (
        result.makespan_s <= workflow.deadline_s
        if workflow.deadline_s is not None else None
    )
    return ExperimentRecord(
        cluster_id=cluster.cluster_id,
        cluster_family=cluster.family,
        workflow_id=workflow.workflow_id,
        workflow_family=workflow.family,
        policy=policy,
        seed=seed,
        makespan_s=result.makespan_s,
        lower_bound_s=lower_bound_s,
        bound_ratio=(result.makespan_s / lower_bound_s
                     if lower_bound_s > 0 else 1.0),
        energy_j=sum(node.energy_j for node in result.nodes.values()),
        network_transfer_mib=result.network_transfer_mib,
        cold_starts=result.cold_starts,
        task_retries=sum(task.attempts - 1 for task in result.tasks.values()),
        node_failures=sum(node.failures for node in result.nodes.values()),
        peak_temperature_c=max(
            node.peak_temperature_c for node in result.nodes.values()),
        deadline_s=workflow.deadline_s,
        deadline_met=deadline_met,
        lateness_s=(max(0.0, result.makespan_s - workflow.deadline_s)
                    if workflow.deadline_s is not None else 0.0),
        calibration_status=cluster.calibration.status,
        uncertainty_sampled=uncertainty_sampled,
        uncertainty_fraction=uncertainty_fraction,
    )


def static_assignment_oracle(
    cluster: ClusterSpec,
    workflow: WorkflowSpec,
    *,
    seed: int = 0,
    max_assignments: int = 5_000,
    thermal_step_s: float = 0.5,
    max_virtual_time_s: float = 365 * 24 * 3600,
) -> SimulationResult | None:
    """Find the best static task-to-node mapping when the search is tractable."""
    candidates: list[list[str]] = []
    for task in workflow.tasks:
        candidates.append([
            node.node_id for node in cluster.nodes
            if node.cpu.architecture in task.supported_architectures
            and node.cpu.logical_cpus >= task.cpu_cores
            and node.memory.capacity_mib - node.background_memory_mib >= task.memory_mib
        ])
    if math.prod(len(options) for options in candidates) > max_assignments:
        return None

    best: SimulationResult | None = None
    task_ids = [task.task_id for task in workflow.tasks]
    for node_ids in itertools.product(*candidates):
        assignments = dict(zip(task_ids, node_ids))
        try:
            result = Simulator(
                cluster,
                workflow,
                StaticAssignmentPolicy(assignments),
                thermal_step_s=thermal_step_s,
                seed=seed,
            ).run(max_virtual_time_s=max_virtual_time_s)
        except (InvalidPlacementError, SimulationDeadlockError, TimeoutError):
            continue
        if best is None or result.makespan_s < best.makespan_s:
            best = result
    return best


def run_experiment_matrix(
    cluster_paths: Sequence[str | Path],
    workflow_paths: Sequence[str | Path],
    policy_names: Sequence[str],
    seeds: Sequence[int],
    *,
    thermal_step_s: float = 0.5,
    max_virtual_time_s: float = 365 * 24 * 3600,
    oracle_max_tasks: int = 0,
    oracle_max_assignments: int = 5_000,
    sample_uncertainty: bool = False,
    uncertainty_floor: float = 0.0,
    progress: Callable[[int, int, ExperimentRecord], None] | None = None,
) -> list[ExperimentRecord]:
    clusters = [load_cluster(path) for path in cluster_paths]
    workflows = [load_workflow(path) for path in workflow_paths]
    oracle_cases = sum(
        len(workflow.tasks) <= oracle_max_tasks
        for workflow in workflows
    ) * len(clusters) * len(seeds)
    total = len(clusters) * len(workflows) * len(policy_names) * len(seeds)
    if oracle_max_tasks:
        total += oracle_cases
    completed = 0
    records = []

    for cluster in clusters:
        for workflow in workflows:
            for seed in seeds:
                run_cluster = (
                    sample_cluster_uncertainty(cluster, seed, uncertainty_floor)
                    if sample_uncertainty else cluster
                )
                applied = (
                    max(cluster.calibration.uncertainty_fraction, uncertainty_floor)
                    if sample_uncertainty else 0.0
                )
                lower_bound = workflow_lower_bound_s(run_cluster, workflow)
                for policy_name in policy_names:
                    # Every policy, including the profile-driven exact Legacy
                    # variants, plans against the cluster that actually runs.
                    result = Simulator(
                        run_cluster,
                        workflow,
                        create_policy(
                            policy_name,
                            seed=seed,
                            cluster=run_cluster,
                            workflow=workflow,
                        ),
                        thermal_step_s=thermal_step_s,
                        seed=seed,
                    ).run(max_virtual_time_s=max_virtual_time_s)
                    record = _record(
                        run_cluster, workflow, policy_name, seed, result, lower_bound,
                        uncertainty_sampled=sample_uncertainty,
                        uncertainty_fraction=applied,
                    )
                    records.append(record)
                    completed += 1
                    if progress:
                        progress(completed, total, record)
                if oracle_max_tasks and len(workflow.tasks) <= oracle_max_tasks:
                    result = static_assignment_oracle(
                        run_cluster,
                        workflow,
                        seed=seed,
                        max_assignments=oracle_max_assignments,
                        thermal_step_s=thermal_step_s,
                        max_virtual_time_s=max_virtual_time_s,
                    )
                    if result is not None:
                        record = _record(
                            run_cluster, workflow, "oracle-static", seed,
                            result, lower_bound,
                            uncertainty_sampled=sample_uncertainty,
                            uncertainty_fraction=applied,
                        )
                        records.append(record)
                        completed += 1
                        if progress:
                            progress(completed, total, record)
    return records


def run_workload_matrix(
    cluster_paths: Sequence[str | Path],
    workload_paths: Sequence[str | Path],
    policy_names: Sequence[str],
    seeds: Sequence[int],
    *,
    thermal_step_s: float = 0.5,
    max_virtual_time_s: float = 365 * 24 * 3600,
    sample_uncertainty: bool = False,
    uncertainty_floor: float = 0.0,
    progress: Callable[[int, int, WorkloadExperimentRecord], None] | None = None,
) -> list[WorkloadExperimentRecord]:
    clusters = [load_cluster(path) for path in cluster_paths]
    workloads = [load_workload(path) for path in workload_paths]
    total = len(clusters) * len(workloads) * len(policy_names) * len(seeds)
    completed = 0
    records = []
    for base_cluster in clusters:
        for workload in workloads:
            compiled = workload.compile()
            for seed in seeds:
                cluster = (
                    sample_cluster_uncertainty(base_cluster, seed, uncertainty_floor)
                    if sample_uncertainty else base_cluster
                )
                applied = (
                    max(base_cluster.calibration.uncertainty_fraction, uncertainty_floor)
                    if sample_uncertainty else 0.0
                )
                for policy_name in policy_names:
                    result = Simulator(
                        cluster,
                        compiled,
                        create_policy(
                            policy_name,
                            seed=seed,
                            cluster=cluster,
                            workflow=compiled,
                        ),
                        thermal_step_s=thermal_step_s,
                        seed=seed,
                    ).run(max_virtual_time_s=max_virtual_time_s)
                    submissions = list(submission_results(workload, result).values())
                    responses = [submission.response_s for submission in submissions]
                    deadline_rows = [
                        submission for submission in submissions
                        if submission.deadline_met is not None
                    ]
                    record = WorkloadExperimentRecord(
                        cluster_id=cluster.cluster_id,
                        cluster_family=cluster.family,
                        workload_id=workload.workload_id,
                        policy=policy_name,
                        seed=seed,
                        makespan_s=result.makespan_s,
                        mean_response_s=statistics.fmean(responses),
                        p95_response_s=_percentile(responses, 0.95),
                        deadline_hit_rate=(
                            sum(row.deadline_met for row in deadline_rows)
                            / len(deadline_rows) if deadline_rows else None
                        ),
                        deadline_misses=sum(
                            row.deadline_met is False for row in deadline_rows),
                        task_preemptions=sum(
                            task.preemptions for task in result.tasks.values()),
                        task_retries=sum(
                            task.attempts - 1 for task in result.tasks.values()),
                        node_failures=sum(
                            node.failures for node in result.nodes.values()),
                        energy_j=sum(node.energy_j for node in result.nodes.values()),
                        network_transfer_mib=result.network_transfer_mib,
                        uncertainty_sampled=sample_uncertainty,
                        uncertainty_fraction=applied,
                    )
                    records.append(record)
                    completed += 1
                    if progress:
                        progress(completed, total, record)
    return records


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile (the smallest value with >= fraction below)."""
    if not values:
        raise ValueError("cannot take a percentile of an empty sample")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"percentile fraction must be in [0, 1], got {fraction}")
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _bootstrap_mean_ci(values: Sequence[float], *, seed: int = 2026,
                       samples: int = 2_000) -> tuple[float, float]:
    if not values:
        raise ValueError("cannot bootstrap an empty sample")
    if len(values) < 2:
        return values[0], values[0]
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    return _percentile(means, 0.025), _percentile(means, 0.975)


def analyze_records(records: Sequence[ExperimentRecord]) -> dict:
    if not records:
        raise ValueError("cannot analyze an empty experiment set")
    deployable_records = [
        record for record in records if record.policy not in REFERENCE_POLICIES]
    reference_records = [
        record for record in records if record.policy in REFERENCE_POLICIES]
    best_by_case: dict[tuple[str, str, int], float] = {}
    for record in deployable_records:
        key = (record.cluster_id, record.workflow_id, record.seed)
        best_by_case[key] = min(best_by_case.get(key, math.inf), record.makespan_s)

    policy_values: dict[str, list[tuple[ExperimentRecord, float]]] = {}
    for record in deployable_records:
        key = (record.cluster_id, record.workflow_id, record.seed)
        regret = record.makespan_s / best_by_case[key] - 1.0
        policy_values.setdefault(record.policy, []).append((record, regret))

    def ranking_row(policy: str,
                    values: list[tuple[ExperimentRecord, float]]) -> dict:
        regrets = [regret for _, regret in values]
        ci_low, ci_high = _bootstrap_mean_ci(regrets)
        deadline_rows = [record for record, _ in values if record.deadline_met is not None]
        return {
            "policy": policy,
            "cases": len(values),
            "wins": sum(regret <= 1e-9 for _, regret in values),
            "win_rate": sum(regret <= 1e-9 for _, regret in values) / len(values),
            "mean_regret": statistics.fmean(regrets),
            "mean_regret_ci95": [ci_low, ci_high],
            "median_regret": statistics.median(regrets),
            "p95_regret": _percentile(regrets, 0.95),
            "median_bound_ratio": statistics.median(
                record.bound_ratio for record, _ in values),
            "deadline_hit_rate": (
                sum(record.deadline_met for record in deadline_rows) / len(deadline_rows)
                if deadline_rows else None
            ),
        }

    ranking = [
        ranking_row(policy, values) for policy, values in policy_values.items()]
    ranking.sort(key=lambda row: (
        row["median_regret"], row["p95_regret"], row["mean_regret"], row["policy"]))
    references = [
        ranking_row(policy, [(record, 0.0) for record in reference_records
                             if record.policy == policy])
        for policy in sorted({record.policy for record in reference_records})
    ]

    def specialists(attribute: str) -> dict[str, dict]:
        grouped: dict[str, dict[str, list[float]]] = {}
        for policy, values in policy_values.items():
            if policy in REFERENCE_POLICIES:
                continue
            for record, regret in values:
                family = getattr(record, attribute)
                grouped.setdefault(family, {}).setdefault(policy, []).append(regret)
        recommendations = {}
        for family, policies in sorted(grouped.items()):
            candidates = [
                {
                    "policy": policy,
                    "median_regret": statistics.median(regrets),
                    "p95_regret": _percentile(regrets, 0.95),
                }
                for policy, regrets in policies.items()
            ]
            candidates.sort(key=lambda row: (
                row["median_regret"], row["p95_regret"], row["policy"]))
            best = candidates[0]
            tied = [
                row["policy"] for row in candidates
                if math.isclose(
                    row["median_regret"], best["median_regret"], abs_tol=1e-12)
                and math.isclose(
                    row["p95_regret"], best["p95_regret"], abs_tol=1e-12)
            ]
            recommendations[family] = {
                "policies": tied,
                "tie": len(tied) > 1,
                "median_regret": best["median_regret"],
                "p95_regret": best["p95_regret"],
            }
        return recommendations

    return {
        "record_count": len(records),
        "case_count": len(best_by_case),
        "ranking": ranking,
        "reference_results": references,
        "cluster_family_recommendations": specialists("cluster_family"),
        "workflow_family_recommendations": specialists("workflow_family"),
    }


def analyze_workload_records(records: Sequence[WorkloadExperimentRecord]) -> dict:
    if not records:
        raise ValueError("cannot analyze an empty workload experiment set")
    best_response: dict[tuple[str, str, int], float] = {}
    for record in records:
        key = (record.cluster_id, record.workload_id, record.seed)
        best_response[key] = min(
            best_response.get(key, math.inf), record.mean_response_s)
    grouped: dict[str, list[tuple[WorkloadExperimentRecord, float]]] = {}
    for record in records:
        key = (record.cluster_id, record.workload_id, record.seed)
        regret = record.mean_response_s / best_response[key] - 1.0
        grouped.setdefault(record.policy, []).append((record, regret))
    ranking = []
    for policy, values in grouped.items():
        regrets = [regret for _, regret in values]
        ci_low, ci_high = _bootstrap_mean_ci(regrets)
        deadline_rows = [row for row, _ in values if row.deadline_hit_rate is not None]
        ranking.append({
            "policy": policy,
            "cases": len(values),
            "mean_response_regret": statistics.fmean(regrets),
            "mean_response_regret_ci95": [ci_low, ci_high],
            "median_response_regret": statistics.median(regrets),
            "p95_response_regret": _percentile(regrets, 0.95),
            "deadline_hit_rate": (
                statistics.fmean(row.deadline_hit_rate for row in deadline_rows)
                if deadline_rows else None
            ),
            "mean_preemptions": statistics.fmean(
                row.task_preemptions for row, _ in values),
            "median_makespan_s": statistics.median(
                row.makespan_s for row, _ in values),
            "median_energy_j": statistics.median(
                row.energy_j for row, _ in values),
        })
    # Response regret is the primary ordering; deadline, makespan, and energy
    # are reported as separate objective leaders instead of being mixed into a
    # lexicographic key.
    ranking.sort(key=lambda row: (
        row["median_response_regret"],
        row["p95_response_regret"],
        row["mean_response_regret"],
        row["policy"],
    ))

    def leaders(metric: str, *, maximize: bool = False) -> dict:
        rows = [row for row in ranking if row[metric] is not None]
        if not rows:
            return {"policies": [], "value": None}
        best = (max if maximize else min)(row[metric] for row in rows)
        return {
            "policies": sorted(
                row["policy"] for row in rows
                if math.isclose(row[metric], best, rel_tol=1e-12, abs_tol=1e-12)),
            "value": best,
        }

    return {
        "record_count": len(records),
        "case_count": len(best_response),
        "ranking": ranking,
        "objective_leaders": {
            "median_response_regret": leaders("median_response_regret"),
            "p95_response_regret": leaders("p95_response_regret"),
            "deadline_hit_rate": leaders("deadline_hit_rate", maximize=True),
            "median_makespan_s": leaders("median_makespan_s"),
            "median_energy_j": leaders("median_energy_j"),
        },
    }


def summary_markdown(summary: dict) -> str:
    lines = [
        "# Experiment Summary",
        "",
        f"Records: {summary['record_count']}  ",
        f"Comparable cases: {summary['case_count']}",
        "",
        "## Overall Ranking",
        "",
        "| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(summary["ranking"], start=1):
        lines.append(
            f"| {index} | {row['policy']} | {row['median_regret']:.2%} | "
            f"{row['p95_regret']:.2%} | {row['win_rate']:.2%} | "
            f"{row['median_bound_ratio']:.3f} |"
        )
    if summary.get("reference_results"):
        lines.extend([
            "",
            "## Reference Oracles",
            "",
            "These are non-deployable references evaluated only where exhaustive search was tractable.",
            "",
            "| Reference | Cases | Median regret | P95 regret | Median / lower bound |",
            "|---|---:|---:|---:|---:|",
        ])
        for row in summary["reference_results"]:
            lines.append(
                f"| {row['policy']} | {row['cases']} | "
                f"{row['median_regret']:.2%} | {row['p95_regret']:.2%} | "
                f"{row['median_bound_ratio']:.3f} |"
            )
    for title, key in (
        ("Cluster Family Specialists", "cluster_family_recommendations"),
        ("Workflow Family Specialists", "workflow_family_recommendations"),
    ):
        lines.extend(["", f"## {title}", "", "| Family | Policy | Median regret | P95 regret |",
                      "|---|---|---:|---:|"])
        for family, row in summary[key].items():
            lines.append(
                f"| {family} | {', '.join(row['policies'])} | "
                f"{row['median_regret']:.2%} | "
                f"{row['p95_regret']:.2%} |"
            )
    return "\n".join(lines) + "\n"


def workload_summary_markdown(summary: dict) -> str:
    lines = [
        "# Multi-Workflow Experiment Summary",
        "",
        f"Records: {summary['record_count']}  ",
        f"Comparable cases: {summary['case_count']}",
        "",
        "Ranked by median per-case response regret; other objectives are listed",
        "as separate leaders below.",
        "",
        "| Rank | Policy | Median response regret | Mean response regret | P95 response regret | Deadline hit rate | Mean preemptions | Median makespan (s) | Median energy (J) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(summary["ranking"], start=1):
        deadline = (
            f"{row['deadline_hit_rate']:.2%}"
            if row["deadline_hit_rate"] is not None else "n/a"
        )
        lines.append(
            f"| {index} | {row['policy']} | "
            f"{row['median_response_regret']:.2%} | "
            f"{row['mean_response_regret']:.2%} | "
            f"{row['p95_response_regret']:.2%} | {deadline} | "
            f"{row['mean_preemptions']:.2f} | {row['median_makespan_s']:.3f} | "
            f"{row['median_energy_j']:.1f} |"
        )
    leaders = summary.get("objective_leaders", {})
    if leaders:
        lines.extend(["", "## Objective leaders", "", "| Objective | Policies | Value |",
                      "|---|---|---:|"])
        for objective, row in leaders.items():
            value = row["value"]
            rendered = "n/a" if value is None else f"{value:.6g}"
            lines.append(
                f"| {objective} | {', '.join(row['policies']) or 'n/a'} | {rendered} |")
    return "\n".join(lines) + "\n"


def write_experiment_outputs(output_dir: str | Path,
                             records: Sequence[ExperimentRecord]) -> dict[str, Path]:
    if not records:
        raise ValueError("no experiment records to write; check seeds and policies")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = [asdict(record) for record in records]
    jsonl_path = output / "records.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    csv_path = output / "records.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = analyze_records(records)
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = output / "summary.md"
    markdown_path.write_text(summary_markdown(summary), encoding="utf-8")
    return {
        "jsonl": jsonl_path,
        "csv": csv_path,
        "summary": summary_path,
        "markdown": markdown_path,
    }


def write_workload_outputs(
    output_dir: str | Path,
    records: Sequence[WorkloadExperimentRecord],
) -> dict[str, Path]:
    if not records:
        raise ValueError("no workload records to write; check seeds and policies")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = [asdict(record) for record in records]
    jsonl_path = output / "workload-records.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    csv_path = output / "workload-records.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = analyze_workload_records(records)
    summary_path = output / "workload-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = output / "workload-summary.md"
    markdown_path.write_text(workload_summary_markdown(summary), encoding="utf-8")
    return {
        "jsonl": jsonl_path,
        "csv": csv_path,
        "summary": summary_path,
        "markdown": markdown_path,
    }


def read_records(path: str | Path) -> list[ExperimentRecord]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return [ExperimentRecord(**json.loads(line)) for line in stream if line.strip()]