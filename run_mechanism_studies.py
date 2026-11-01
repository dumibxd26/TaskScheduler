"""Targeted simulation studies for two mechanism questions.

Cold starts: how much of a workflow's makespan and time-to-running is image
pulling, and how much does image-aware placement recover? HEFT-RA is run
(1) as is, (2) with image-blind estimates (every image looks warm to the
policy but pulls still happen), and (3) on a cluster where every image is
pre-pulled everywhere, which bounds what prefetching could save. The stock
Kubernetes scoring, whose only startup signal is ImageLocality, is included
for reference.

Priorities: when a CRITICAL workflow arrives into a busy cluster, how much
faster does it finish under priority ordering and preemption, and what does
that cost every other workflow? The three workloads that contain an urgent
CRITICAL submission are run under each policy and the urgent and
non-urgent response times are reported separately.

Both studies use the holdout protocol (seeds from 400, +/-10% uncertainty
floor) and paired comparisons on identical cluster instances.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence

from analyze_final_evidence import _bootstrap_mean_ci, _percentile
from simulation.engine import SchedulerSnapshot, Simulator
from simulation.experiments import sample_cluster_uncertainty
from simulation.policies import ResourceAwareHEFTPolicy, create_policy
from simulation.schema import (
    ClusterSpec,
    PriorityClass,
    WorkflowSpec,
    load_cluster,
    load_workflow,
)
from simulation.workloads import WorkloadSpec, load_workload, submission_results


ROOT = Path(__file__).resolve().parent
COLD_START_VARIANTS = ("heft-ra", "heft-ra-image-blind", "heft-ra-prewarmed",
                       "k8s-default")
PRIORITY_POLICIES = ("adaptive", "adaptive-no-preempt", "heft-ra", "heft",
                     "eft", "k8s-default")


class ImageBlindHEFTRA(ResourceAwareHEFTPolicy):
    """HEFT-RA whose estimates believe every image is already present."""

    name = "heft-ra-image-blind"

    def schedule(self, snapshot: SchedulerSnapshot):
        return super().schedule(image_blind_snapshot(snapshot))


def workflow_images(workflow: WorkflowSpec) -> frozenset[str]:
    return frozenset(task.image for task in workflow.tasks)


def image_blind_snapshot(snapshot: SchedulerSnapshot) -> SchedulerSnapshot:
    images = workflow_images(snapshot.workflow)
    nodes = {
        node_id: replace(view, warm_images=frozenset(view.warm_images) | images)
        for node_id, view in snapshot.nodes.items()
    }
    return replace(snapshot, nodes=nodes)


def prewarmed_cluster(cluster: ClusterSpec,
                      workflow: WorkflowSpec) -> ClusterSpec:
    images = workflow_images(workflow)
    return replace(cluster, nodes=tuple(
        replace(node, initial_warm_images=node.initial_warm_images | images)
        for node in cluster.nodes))


def _time_to_running(result) -> list[float]:
    return [
        task.started_at_s - task.ready_at_s
        for task in result.tasks.values()
        if task.started_at_s is not None and task.ready_at_s is not None
    ]


def cold_start_records(clusters: Sequence[ClusterSpec],
                       workflows: Sequence[WorkflowSpec],
                       seeds: Iterable[int], floor: float) -> list[dict]:
    records = []
    for base in clusters:
        for workflow in workflows:
            for seed in seeds:
                cluster = sample_cluster_uncertainty(base, seed, floor)
                for variant in COLD_START_VARIANTS:
                    run_cluster = cluster
                    if variant == "heft-ra-prewarmed":
                        run_cluster = prewarmed_cluster(cluster, workflow)
                        policy = create_policy("heft-ra", seed=seed,
                                               cluster=run_cluster,
                                               workflow=workflow)
                    elif variant == "heft-ra-image-blind":
                        policy = ImageBlindHEFTRA()
                    else:
                        policy = create_policy(variant, seed=seed,
                                               cluster=run_cluster,
                                               workflow=workflow)
                    result = Simulator(run_cluster, workflow, policy,
                                       seed=seed).run()
                    waits = _time_to_running(result)
                    records.append({
                        "cluster_id": base.cluster_id,
                        "cluster_family": base.family,
                        "workflow_id": workflow.workflow_id,
                        "seed": seed,
                        "variant": variant,
                        "makespan_s": result.makespan_s,
                        "mean_time_to_running_s": statistics.fmean(waits),
                        "p95_time_to_running_s": _percentile(waits, 0.95),
                        "cold_starts": result.cold_starts,
                        "network_transfer_mib": result.network_transfer_mib,
                    })
    return records


def _submission_priority(submission) -> PriorityClass:
    if submission.priority is not None:
        return submission.priority
    return submission.workflow.priority


def priority_records(clusters: Sequence[ClusterSpec],
                     workloads: Sequence[WorkloadSpec],
                     seeds: Iterable[int], floor: float,
                     policies: Sequence[str] = PRIORITY_POLICIES) -> list[dict]:
    records = []
    for base in clusters:
        for workload in workloads:
            critical = {
                submission.submission_id for submission in workload.submissions
                if _submission_priority(submission) == PriorityClass.CRITICAL
            }
            if not critical or len(critical) == len(workload.submissions):
                continue
            compiled = workload.compile()
            for seed in seeds:
                cluster = sample_cluster_uncertainty(base, seed, floor)
                for policy_name in policies:
                    result = Simulator(
                        cluster, compiled,
                        create_policy(policy_name, seed=seed, cluster=cluster,
                                      workflow=compiled),
                        thermal_step_s=0.5, seed=seed,
                    ).run()
                    outcomes = submission_results(workload, result)
                    urgent = [outcomes[name] for name in sorted(critical)]
                    others = [row for name, row in outcomes.items()
                              if name not in critical]
                    met = [row.deadline_met for row in urgent
                           if row.deadline_met is not None]
                    records.append({
                        "cluster_id": base.cluster_id,
                        "cluster_family": base.family,
                        "workload_id": workload.workload_id,
                        "seed": seed,
                        "policy": policy_name,
                        "urgent_response_s": statistics.fmean(
                            row.response_s for row in urgent),
                        "other_response_s": statistics.fmean(
                            row.response_s for row in others),
                        "urgent_deadline_met": (all(met) if met else None),
                        "makespan_s": result.makespan_s,
                        "preemptions": sum(
                            task.preemptions for task in result.tasks.values()),
                    })
    return records


def _paired_ratio(records: list[dict], key_fields: tuple[str, ...],
                  label_field: str, left: str, right: str,
                  metric: str) -> dict:
    by_label: dict[str, dict] = {left: {}, right: {}}
    for row in records:
        if row[label_field] in by_label:
            key = tuple(row[field] for field in key_fields)
            by_label[row[label_field]][key] = row[metric]
    common = sorted(set(by_label[left]) & set(by_label[right]))
    ratios = [by_label[left][key] / by_label[right][key] for key in common
              if by_label[right][key] > 0]
    if not ratios:
        return {"left": left, "right": right, "metric": metric, "cases": 0}
    return {
        "left": left, "right": right, "metric": metric, "cases": len(ratios),
        "mean_ratio": statistics.fmean(ratios),
        "mean_ratio_ci95": _bootstrap_mean_ci(ratios),
        "wins": sum(ratio < 1 - 1e-9 for ratio in ratios),
        "ties": sum(abs(ratio - 1) <= 1e-9 for ratio in ratios),
        "losses": sum(ratio > 1 + 1e-9 for ratio in ratios),
    }


def summarize_cold_start(records: list[dict]) -> dict:
    variants = {}
    for variant in COLD_START_VARIANTS:
        rows = [row for row in records if row["variant"] == variant]
        if not rows:
            continue
        variants[variant] = {
            "runs": len(rows),
            "mean_makespan_s": statistics.fmean(r["makespan_s"] for r in rows),
            "mean_time_to_running_s": statistics.fmean(
                r["mean_time_to_running_s"] for r in rows),
            "p95_time_to_running_s": _percentile(
                [r["p95_time_to_running_s"] for r in rows], 0.95),
            "mean_cold_starts": statistics.fmean(r["cold_starts"] for r in rows),
        }
    keys = ("cluster_id", "workflow_id", "seed")
    contrasts = [
        _paired_ratio(records, keys, "variant", left, right, metric)
        for left, right in (("heft-ra", "heft-ra-image-blind"),
                            ("heft-ra-prewarmed", "heft-ra"),
                            ("heft-ra", "k8s-default"))
        for metric in ("makespan_s", "mean_time_to_running_s")
    ]
    by_cluster = {}
    for cluster in sorted({row["cluster_family"] for row in records}):
        rows = [row for row in records if row["cluster_family"] == cluster]
        by_cluster[cluster] = {
            "prewarm_vs_cold_makespan": _paired_ratio(
                rows, keys, "variant", "heft-ra-prewarmed", "heft-ra",
                "makespan_s"),
            "aware_vs_blind_makespan": _paired_ratio(
                rows, keys, "variant", "heft-ra", "heft-ra-image-blind",
                "makespan_s"),
        }
    return {"variants": variants, "contrasts": contrasts,
            "by_cluster_family": by_cluster}


def summarize_priority(records: list[dict]) -> dict:
    policies = {}
    for policy in PRIORITY_POLICIES:
        rows = [row for row in records if row["policy"] == policy]
        if not rows:
            continue
        met = [row["urgent_deadline_met"] for row in rows
               if row["urgent_deadline_met"] is not None]
        policies[policy] = {
            "runs": len(rows),
            "mean_urgent_response_s": statistics.fmean(
                r["urgent_response_s"] for r in rows),
            "mean_other_response_s": statistics.fmean(
                r["other_response_s"] for r in rows),
            "urgent_deadline_hit_rate": (
                statistics.fmean(met) if met else None),
            "mean_makespan_s": statistics.fmean(r["makespan_s"] for r in rows),
            "mean_preemptions": statistics.fmean(r["preemptions"] for r in rows),
        }
    keys = ("cluster_id", "workload_id", "seed")
    contrasts = [
        _paired_ratio(records, keys, "policy", left, right, metric)
        for left, right in (("adaptive", "adaptive-no-preempt"),
                            ("adaptive", "heft-ra"),
                            ("heft-ra", "k8s-default"))
        for metric in ("urgent_response_s", "other_response_s")
    ]
    by_workload = {}
    for workload in sorted({row["workload_id"] for row in records}):
        rows = [row for row in records if row["workload_id"] == workload]
        by_workload[workload] = {
            policy: {
                "mean_urgent_response_s": statistics.fmean(
                    r["urgent_response_s"] for r in rows if r["policy"] == policy),
                "mean_other_response_s": statistics.fmean(
                    r["other_response_s"] for r in rows if r["policy"] == policy),
            }
            for policy in PRIORITY_POLICIES
            if any(r["policy"] == policy for r in rows)
        }
    return {"policies": policies, "contrasts": contrasts,
            "by_workload": by_workload}


def _contrast_rows(lines: list[str], contrasts: list[dict]) -> None:
    lines.extend(["| Comparison | Metric | Cases | Mean ratio | 95% CI | "
                  "Wins / ties / losses |", "|---|---|---:|---:|---|---:|"])
    for row in contrasts:
        if not row.get("cases"):
            continue
        low, high = row["mean_ratio_ci95"]
        lines.append(
            f"| {row['left']} vs {row['right']} | {row['metric']} | "
            f"{row['cases']} | {row['mean_ratio']:.4f} | "
            f"[{low:.4f}, {high:.4f}] | {row['wins']} / {row['ties']} / "
            f"{row['losses']} |")
    lines.append("")


def markdown(report: dict) -> str:
    cold = report["cold_start"]
    priority = report["priority"]
    lines = [
        "# Mechanism Studies", "",
        f"Seeds {report['seeds'][0]}-{report['seeds'][-1]} with a "
        f"+/-{report['uncertainty_floor']:.0%} uncertainty floor; ratios are "
        "paired on identical cluster instances (below 1 favours the left "
        "policy).", "",
        "## Cold starts (closed workflows)", "",
        "| Variant | Runs | Mean makespan (s) | Mean time-to-running (s) | "
        "P95 time-to-running (s) | Mean cold starts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, row in cold["variants"].items():
        lines.append(
            f"| {variant} | {row['runs']} | {row['mean_makespan_s']:.3f} | "
            f"{row['mean_time_to_running_s']:.3f} | "
            f"{row['p95_time_to_running_s']:.3f} | "
            f"{row['mean_cold_starts']:.2f} |")
    lines.append("")
    _contrast_rows(lines, cold["contrasts"])
    lines.extend(["| Cluster family | Pre-warmed / cold makespan | "
                  "Image-aware / image-blind makespan |", "|---|---:|---:|"])
    for family, row in cold["by_cluster_family"].items():
        lines.append(
            f"| {family} | {row['prewarm_vs_cold_makespan'].get('mean_ratio', 1):.4f} | "
            f"{row['aware_vs_blind_makespan'].get('mean_ratio', 1):.4f} |")
    lines.extend(["", "## Priorities and preemption (workloads with an urgent "
                  "CRITICAL workflow)", "",
                  "| Policy | Runs | Urgent response (s) | Other response (s) | "
                  "Urgent deadline hit | Mean makespan (s) | Preemptions |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for policy, row in priority["policies"].items():
        hit = row["urgent_deadline_hit_rate"]
        lines.append(
            f"| {policy} | {row['runs']} | {row['mean_urgent_response_s']:.3f} | "
            f"{row['mean_other_response_s']:.3f} | "
            f"{f'{hit:.2%}' if hit is not None else 'n/a'} | "
            f"{row['mean_makespan_s']:.3f} | {row['mean_preemptions']:.2f} |")
    lines.append("")
    _contrast_rows(lines, priority["contrasts"])
    for workload, rows in priority["by_workload"].items():
        lines.extend([f"### {workload}", "",
                      "| Policy | Urgent response (s) | Other response (s) |",
                      "|---|---:|---:|"])
        for policy, row in rows.items():
            lines.append(f"| {policy} | {row['mean_urgent_response_s']:.3f} | "
                         f"{row['mean_other_response_s']:.3f} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed-start", type=int, default=400)
    parser.add_argument("--cold-start-seeds", type=int, default=10)
    parser.add_argument("--priority-seeds", type=int, default=50)
    parser.add_argument("--uncertainty-floor", type=float, default=0.1)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "mechanism-studies.json")
    args = parser.parse_args()
    if args.cold_start_seeds < 1 or args.priority_seeds < 1:
        parser.error("seed counts must be positive")

    scenarios = ROOT / "scenarios"
    clusters = [load_cluster(path)
                for path in sorted((scenarios / "clusters").glob("*.yaml"))]
    workflows = [load_workflow(path)
                 for path in sorted((scenarios / "workflows").glob("*.yaml"))]
    workloads = [load_workload(path)
                 for path in sorted((scenarios / "workloads").glob("*.yaml"))]
    cold_seeds = list(range(args.seed_start,
                            args.seed_start + args.cold_start_seeds))
    priority_seeds = list(range(args.seed_start,
                                args.seed_start + args.priority_seeds))

    cold = cold_start_records(clusters, workflows, cold_seeds,
                              args.uncertainty_floor)
    priority = priority_records(clusters, workloads, priority_seeds,
                                args.uncertainty_floor)
    report = {
        "schema_version": 1,
        "seeds": priority_seeds,
        "cold_start_seeds": cold_seeds,
        "uncertainty_floor": args.uncertainty_floor,
        "cold_start": summarize_cold_start(cold),
        "priority": summarize_priority(priority),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    records_path = args.output.with_name(args.output.stem + "-records.jsonl")
    with records_path.open("w", encoding="utf-8") as handle:
        for row in cold:
            handle.write(json.dumps({"study": "cold-start", **row}) + "\n")
        for row in priority:
            handle.write(json.dumps({"study": "priority", **row}) + "\n")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(markdown(report) + "\n", encoding="utf-8")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
