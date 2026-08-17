"""Aggregate and compare retained multi-topology Kind experiments."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict
from pathlib import Path

from services.scheduler import legacy_weights_for_policy


EXPECTED_POLICIES = frozenset({"legacy-v1", "legacy-tuned", "adaptive"})
EXPECTED_SHAPES = frozenset({
    "pressure", "critical", "pipeline", "fanout", "parallel"})
EXPECTED_TOPOLOGIES = frozenset({"small", "homogeneous", "skewed"})
COMPARISON_FIELDS = (
    "median_workflow_response_s",
    "p95_workflow_response_s",
    "median_critical_response_s",
    "median_trial_makespan_s",
)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _policy_metrics(trials: list[dict]) -> dict:
    workflows = [workflow for trial in trials for workflow in trial["workflows"]]
    successful = [
        workflow for workflow in workflows if workflow["state"] == "FINISHED"]
    responses = [
        workflow["response_seconds"] for workflow in successful
        if workflow["response_seconds"] is not None
    ]
    critical = [
        workflow["response_seconds"] for workflow in successful
        if workflow["shape"] == "critical"
        and workflow["response_seconds"] is not None
    ]
    responses_by_shape = {
        shape: statistics.median(
            workflow["response_seconds"]
            for workflow in successful
            if workflow["shape"] == shape
            and workflow["response_seconds"] is not None
        )
        for shape in sorted({workflow["shape"] for workflow in successful})
    }
    makespans = [
        trial["makespan_seconds"] for trial in trials
        if trial["makespan_seconds"] is not None
    ]
    mean_responses = [
        trial["mean_response_seconds"] for trial in trials
        if trial["mean_response_seconds"] is not None
    ]
    tasks = [task for workflow in successful for task in workflow["tasks"]]
    by_repetition = {}
    repetitions = sorted({
        trial.get("repetition") for trial in trials
        if isinstance(trial.get("repetition"), int)
    })
    for repetition in repetitions:
        repetition_trials = [
            trial for trial in trials if trial.get("repetition") == repetition]
        by_repetition[str(repetition)] = {
            "trials": len(repetition_trials),
            "median_trial_makespan_s": statistics.median(
                trial["makespan_seconds"] for trial in repetition_trials
                if trial["makespan_seconds"] is not None
            ),
            "median_trial_mean_response_s": statistics.median(
                trial["mean_response_seconds"] for trial in repetition_trials
                if trial["mean_response_seconds"] is not None
            ),
        }
    return {
        "trials": len(trials),
        "workflows": len(workflows),
        "tasks": len(tasks),
        "completion_rate": len(successful) / len(workflows),
        "task_completion_rate": sum(
            task.get("state") == "FINISHED" for task in tasks) / len(tasks),
        "median_workflow_response_s": statistics.median(responses),
        "p95_workflow_response_s": _percentile(responses, 0.95),
        "median_critical_response_s": statistics.median(critical),
        "median_response_by_shape_s": responses_by_shape,
        "median_trial_makespan_s": statistics.median(makespans),
        "median_trial_mean_response_s": statistics.median(mean_responses),
        "by_repetition": by_repetition,
        "preemptions": sum(trial["preemptions"] for trial in trials),
        "task_failures": sum(
            workflow.get("tasks_failed", 0) for workflow in workflows),
        "scheduler_error_lines": sum(
            len(trial["scheduler_events"]["error_lines"]) for trial in trials),
        "container_restarts": sum(
            task.get("restart_count") or 0 for task in tasks),
        "collection_errors": sum(
            "collection_error" in task for task in tasks),
        "remote_fetches": sum(
            task.get("transfer_mode") == "remote" for task in tasks),
        "local_reads": sum(
            task.get("transfer_mode") == "local" for task in tasks),
        "transfer_seconds": sum(
            (task.get("metrics") or {}).get("transfer_seconds", 0.0)
            for task in tasks
        ),
    }


def _validate_trial(path: Path, policy: str, repetition: int,
                    trial: dict) -> None:
    prefix = f"{path}: {policy} repetition {repetition}"
    if trial.get("policy") != policy or trial.get("repetition") != repetition:
        raise ValueError(f"{prefix} has inconsistent policy/repetition metadata")
    workflows = trial.get("workflows", [])
    if len(workflows) != 5:
        raise ValueError(f"{prefix} has {len(workflows)} workflows; expected 5")
    shapes = {workflow.get("shape") for workflow in workflows}
    if shapes != EXPECTED_SHAPES:
        raise ValueError(f"{prefix} has unexpected workflow shapes: {shapes}")
    if any(workflow.get("state") != "FINISHED" for workflow in workflows):
        raise ValueError(f"{prefix} contains a non-finished workflow")
    if any(workflow.get("tasks_failed", 0) for workflow in workflows):
        raise ValueError(f"{prefix} reports failed tasks")
    tasks = [task for workflow in workflows for task in workflow.get("tasks", [])]
    if len(tasks) != 20:
        raise ValueError(f"{prefix} has {len(tasks)} final tasks; expected 20")
    for task in tasks:
        if task.get("state") != "FINISHED" or task.get("pod_phase") != "Succeeded":
            raise ValueError(f"{prefix} contains a non-successful final task")
        if task.get("restart_count") != 0:
            raise ValueError(f"{prefix} contains a container restart")
        if task.get("collection_error"):
            raise ValueError(f"{prefix} contains a task collection error")
    events = trial.get("scheduler_events", {})
    if events.get("error_lines"):
        raise ValueError(f"{prefix} contains scheduler error lines")
    if len(events.get("binding_lines", [])) < 20:
        raise ValueError(f"{prefix} contains fewer than 20 binding records")


def _validate_document(path: Path, document: dict) -> None:
    if document.get("error"):
        raise ValueError(f"{path} contains experiment error: {document['error']}")
    if not document.get("completed_at"):
        raise ValueError(f"{path} is incomplete")
    policies = document.get("policies", {})
    if set(policies) != EXPECTED_POLICIES:
        raise ValueError(
            f"{path} has policies {sorted(policies)}; "
            f"expected {sorted(EXPECTED_POLICIES)}")
    repetitions = document.get("repetitions")
    if not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError(f"{path} has invalid repetition count {repetitions}")
    workers = [
        node for node in document.get("cluster", {}).get("nodes", [])
        if node.get("node_type") is not None
    ]
    if len(workers) != 3:
        raise ValueError(f"{path} has {len(workers)} worker records; expected 3")

    profile_paths = set()
    for policy, policy_data in policies.items():
        trials = policy_data.get("trials", [])
        scheduler_runs = policy_data.get("scheduler_runs", [])
        if len(trials) != repetitions or len(scheduler_runs) != repetitions:
            raise ValueError(
                f"{path}: {policy} does not contain {repetitions} complete runs")
        expected_repetitions = list(range(1, repetitions + 1))
        if sorted(trial.get("repetition") for trial in trials) != expected_repetitions:
            raise ValueError(f"{path}: {policy} has invalid trial repetitions")
        policy_profile_paths = {run.get("profile_path") for run in scheduler_runs}
        if None in policy_profile_paths or len(policy_profile_paths) != 1:
            raise ValueError(f"{path}: {policy} does not share one profile path")
        profile_paths.update(policy_profile_paths)
        if policy.startswith("legacy"):
            expected_weights = asdict(legacy_weights_for_policy(policy))
            for run in scheduler_runs:
                if run.get("legacy_weights") != expected_weights:
                    raise ValueError(f"{path}: {policy} records incorrect weights")
        for trial in trials:
            _validate_trial(path, policy, trial["repetition"], trial)
    if len(profile_paths) != len(EXPECTED_POLICIES):
        raise ValueError(f"{path} reuses a profile path across policies")


def _ratio(left: dict, right: dict, field: str) -> float:
    return left[field] / right[field]


def _comparison(left: dict, right: dict) -> dict[str, float]:
    return {field: _ratio(left, right, field) for field in COMPARISON_FIELDS}


def analyze(directory: Path, *,
            expected_topologies: frozenset[str] | None = None) -> dict:
    documents = []
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("experiment") != "kind-policy-comparison":
            continue
        documents.append((path, document))
    if not documents:
        raise ValueError(f"no Kind topology artifacts found in {directory}")

    topologies = {}
    aggregate_trials: dict[str, list[dict]] = {}
    for path, document in documents:
        _validate_document(path, document)
        topology = document.get("topology_name") or path.stem
        if topology in topologies:
            raise ValueError(f"duplicate topology name: {topology}")
        policies = {}
        for policy, policy_data in document["policies"].items():
            trials = policy_data["trials"]
            policies[policy] = _policy_metrics(trials)
            aggregate_trials.setdefault(policy, []).extend(trials)
        topologies[topology] = {
            "artifact": str(path.as_posix()),
            "cluster": document["cluster"],
            "policies": policies,
            "comparisons": {
                "tuned_vs_v1": _comparison(
                    policies["legacy-tuned"], policies["legacy-v1"]),
                "adaptive_vs_tuned": _comparison(
                    policies["adaptive"], policies["legacy-tuned"]),
            },
        }

    if expected_topologies is not None and set(topologies) != expected_topologies:
        raise ValueError(
            f"found topologies {sorted(topologies)}; "
            f"expected {sorted(expected_topologies)}")

    aggregate = {
        policy: _policy_metrics(trials)
        for policy, trials in sorted(aggregate_trials.items())
    }
    tuned_improvements = {
        field: sum(
            topology["comparisons"]["tuned_vs_v1"][field] < 1.0
            for topology in topologies.values()
        )
        for field in COMPARISON_FIELDS
    }
    return {
        "schema_version": 1,
        "topology_count": len(topologies),
        "topologies": topologies,
        "aggregate": aggregate,
        "aggregate_comparisons": {
            "tuned_vs_v1": _comparison(
                aggregate["legacy-tuned"], aggregate["legacy-v1"]),
            "adaptive_vs_tuned": _comparison(
                aggregate["adaptive"], aggregate["legacy-tuned"]),
        },
        "direction_checks": {
            "tuned_better_than_v1_topology_count": tuned_improvements,
            "simulation_prediction": (
                "legacy-tuned improves aggregate exact Legacy v1 outcomes"
            ),
        },
        "evidence_scope": (
            "Single-PC Kind integration and descriptive performance evidence; "
            "not independent physical-node validation."
        ),
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Multi-Topology Kind Summary",
        "",
        report["evidence_scope"],
        "",
        "## Per Topology",
        "",
        "| Topology | Policy | Completion | Median workflow (s) | P95 workflow "
        "(s) | Critical (s) | Makespan (s) | Preemptions | Restarts |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for topology, topology_data in report["topologies"].items():
        for policy, row in topology_data["policies"].items():
            lines.append(
                f"| {topology} | {policy} | {row['completion_rate']:.2%} | "
                f"{row['median_workflow_response_s']:.3f} | "
                f"{row['p95_workflow_response_s']:.3f} | "
                f"{row['median_critical_response_s']:.3f} | "
                f"{row['median_trial_makespan_s']:.3f} | "
                f"{row['preemptions']} | {row['container_restarts']} |")
    lines.extend([
        "",
        "## Per-Topology Workflow Shapes",
        "",
        "| Topology | Policy | Shape | Median response (s) |",
        "|---|---|---|---:|",
    ])
    for topology, topology_data in report["topologies"].items():
        for policy, row in topology_data["policies"].items():
            for shape, response in row["median_response_by_shape_s"].items():
                lines.append(
                    f"| {topology} | {policy} | {shape} | {response:.3f} |")
    lines.extend([
        "",
        "## Aggregate",
        "",
        "| Policy | Trials | Completion | Median workflow (s) | P95 workflow "
        "(s) | Critical (s) | Makespan (s) | Preemptions | Restarts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for policy, row in report["aggregate"].items():
        lines.append(
            f"| {policy} | {row['trials']} | {row['completion_rate']:.2%} | "
            f"{row['median_workflow_response_s']:.3f} | "
            f"{row['p95_workflow_response_s']:.3f} | "
            f"{row['median_critical_response_s']:.3f} | "
            f"{row['median_trial_makespan_s']:.3f} | "
            f"{row['preemptions']} | {row['container_restarts']} |")
    lines.extend([
        "",
        "## Aggregate Workflow Shapes",
        "",
        "| Policy | Shape | Median response (s) |",
        "|---|---|---:|",
    ])
    for policy, row in report["aggregate"].items():
        for shape, response in row["median_response_by_shape_s"].items():
            lines.append(f"| {policy} | {shape} | {response:.3f} |")
    lines.extend([
        "",
        "## Profile-Learning Sequence",
        "",
        "Profiles are reused only within a policy and topology. Sequential "
        "values are descriptive and also include host/order effects.",
        "",
        "| Scope | Policy | Repetition | Trials | Makespan (s) | Mean "
        "response (s) |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for topology, topology_data in report["topologies"].items():
        for policy, row in topology_data["policies"].items():
            for repetition, metrics in row["by_repetition"].items():
                lines.append(
                    f"| {topology} | {policy} | {repetition} | "
                    f"{metrics['trials']} | "
                    f"{metrics['median_trial_makespan_s']:.3f} | "
                    f"{metrics['median_trial_mean_response_s']:.3f} |")
    for policy, row in report["aggregate"].items():
        for repetition, metrics in row["by_repetition"].items():
            lines.append(
                f"| all-topology median | {policy} | {repetition} | "
                f"{metrics['trials']} | "
                f"{metrics['median_trial_makespan_s']:.3f} | "
                f"{metrics['median_trial_mean_response_s']:.3f} |")
    lines.extend([
        "",
        "## Timing Comparisons",
        "",
        "Negative values favor the policy left of `vs`; positive values "
        "favor the policy on the right.",
        "",
        "| Scope | Comparison | Median workflow | P95 workflow | Critical | "
        "Makespan |",
        "|---|---|---:|---:|---:|---:|",
    ])
    comparison_labels = {
        "tuned_vs_v1": "tuned vs v1",
        "adaptive_vs_tuned": "Adaptive vs tuned",
    }
    comparison_scopes = [
        (topology, data["comparisons"])
        for topology, data in report["topologies"].items()
    ] + [("aggregate", report["aggregate_comparisons"])]
    for scope, comparisons in comparison_scopes:
        for name, ratios in comparisons.items():
            changes = {
                field: 100.0 * (ratios[field] - 1.0)
                for field in COMPARISON_FIELDS
            }
            lines.append(
                f"| {scope} | {comparison_labels[name]} | "
                f"{changes['median_workflow_response_s']:+.2f}% | "
                f"{changes['p95_workflow_response_s']:+.2f}% | "
                f"{changes['median_critical_response_s']:+.2f}% | "
                f"{changes['median_trial_makespan_s']:+.2f}% |")
    lines.extend(["", "## Tuned Legacy Direction Check", ""])
    total = report["topology_count"]
    for field, improvements in report["direction_checks"][
            "tuned_better_than_v1_topology_count"].items():
        lines.append(
            f"- `{field}`: tuned Legacy is lower in {improvements}/{total} topologies.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("results/k8s-kind-topologies"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/k8s-kind-topologies/summary.json"),
    )
    args = parser.parse_args()
    report = analyze(
        args.directory, expected_topologies=EXPECTED_TOPOLOGIES)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(_markdown(report) + "\n", encoding="utf-8")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())