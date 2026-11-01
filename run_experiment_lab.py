"""Command-line entry point for the deterministic scheduling experiment lab."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulation.experiments import (
    analyze_records,
    analyze_workload_records,
    read_records,
    run_experiment_matrix,
    run_workload_matrix,
    summary_markdown,
    workload_summary_markdown,
    write_experiment_outputs,
    write_workload_outputs,
)


ROOT = Path(__file__).parent
DEFAULT_POLICIES = "random,fcfs,eft,heft,legacy,adaptive"


def _selection(directory: Path, value: str) -> list[Path]:
    available = {path.stem: path for path in directory.glob("*.yaml")}
    if value.lower() == "all":
        return [available[name] for name in sorted(available)]
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise ValueError(
            f"unknown scenarios {unknown}; available: {sorted(available)}")
    return [available[name] for name in names]


def _seeds(value: str) -> list[int]:
    """Parse ``a,b,c`` or a half-open ``start:stop`` range into seeds."""
    if ":" in value:
        start, stop = (int(part) for part in value.split(":", 1))
        seeds = list(range(start, stop))
    else:
        seeds = [int(item) for item in value.split(",") if item.strip()]
    if not seeds:
        raise ValueError(
            f"seed selection {value!r} is empty; ranges are half-open, "
            "so use 0:10 for seeds 0 through 9")
    return seeds


def _policy_list(value: str) -> list[str]:
    policies = [item.strip() for item in value.split(",") if item.strip()]
    if not policies:
        raise ValueError("at least one policy is required")
    return policies


def _run(args) -> int:
    clusters = _selection(ROOT / "scenarios" / "clusters", args.clusters)
    workflows = _selection(ROOT / "scenarios" / "workflows", args.workflows)
    policies = _policy_list(args.policies)
    seeds = _seeds(args.seeds)

    def progress(completed, total, record):
        if not args.quiet:
            print(
                f"[{completed:>4}/{total}] {record.cluster_id} / "
                f"{record.workflow_id} / {record.policy} / seed={record.seed}: "
                f"{record.makespan_s:.3f}s"
            )

    records = run_experiment_matrix(
        clusters,
        workflows,
        policies,
        seeds,
        thermal_step_s=args.thermal_step,
        max_virtual_time_s=args.max_virtual_time,
        oracle_max_tasks=args.oracle_max_tasks,
        oracle_max_assignments=args.oracle_max_assignments,
        sample_uncertainty=args.sample_uncertainty,
        uncertainty_floor=args.uncertainty_floor,
        progress=progress,
    )
    paths = write_experiment_outputs(args.output, records)
    print(summary_markdown(analyze_records(records)))
    print("Outputs:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


def _analyze(args) -> int:
    summary = analyze_records(read_records(args.records))
    print(summary_markdown(summary))
    if args.output:
        Path(args.output).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


def _workload(args) -> int:
    clusters = _selection(ROOT / "scenarios" / "clusters", args.clusters)
    workloads = _selection(ROOT / "scenarios" / "workloads", args.workloads)
    policies = _policy_list(args.policies)
    seeds = _seeds(args.seeds)

    def progress(completed, total, record):
        if not args.quiet:
            print(
                f"[{completed:>4}/{total}] {record.cluster_id} / "
                f"{record.workload_id} / {record.policy} / seed={record.seed}: "
                f"mean_response={record.mean_response_s:.3f}s"
            )

    records = run_workload_matrix(
        clusters,
        workloads,
        policies,
        seeds,
        thermal_step_s=args.thermal_step,
        max_virtual_time_s=args.max_virtual_time,
        sample_uncertainty=args.sample_uncertainty,
        uncertainty_floor=args.uncertainty_floor,
        progress=progress,
    )
    paths = write_workload_outputs(args.output, records)
    print(workload_summary_markdown(analyze_workload_records(records)))
    print("Outputs:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run reproducible heterogeneous scheduling experiments.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a scenario matrix")
    run.add_argument("--clusters", default="all",
                     help="comma-separated scenario stems or 'all'")
    run.add_argument("--workflows", default="all",
                     help="comma-separated scenario stems or 'all'")
    run.add_argument("--policies", default=DEFAULT_POLICIES)
    run.add_argument("--seeds", default="0:10",
                     help="comma list or half-open range, such as 0:10")
    run.add_argument("--output", default=str(ROOT / "results" / "lab"))
    run.add_argument("--thermal-step", type=float, default=0.5)
    run.add_argument("--max-virtual-time", type=float, default=365 * 24 * 3600)
    run.add_argument("--oracle-max-tasks", type=int, default=0)
    run.add_argument("--oracle-max-assignments", type=int, default=5000)
    run.add_argument(
        "--sample-uncertainty",
        action="store_true",
        help="sample each cluster's declared calibration uncertainty per seed",
    )
    run.add_argument(
        "--uncertainty-floor", type=float, default=0.0,
        help="minimum per-parameter perturbation when sampling (e.g. 0.1)",
    )
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_run)

    analyze = commands.add_parser("analyze", help="reanalyze a records.jsonl file")
    analyze.add_argument("records")
    analyze.add_argument("--output")
    analyze.set_defaults(func=_analyze)

    workload = commands.add_parser(
        "workload", help="run overlapping multi-workflow arrival scenarios")
    workload.add_argument("--clusters", default="all")
    workload.add_argument("--workloads", default="all")
    workload.add_argument(
        "--policies",
        default="fcfs,eft,heft,adaptive-no-preempt,adaptive",
    )
    workload.add_argument("--seeds", default="0:10")
    workload.add_argument(
        "--output", default=str(ROOT / "results" / "workload-lab"))
    workload.add_argument("--thermal-step", type=float, default=0.5)
    workload.add_argument(
        "--max-virtual-time", type=float, default=365 * 24 * 3600)
    workload.add_argument("--sample-uncertainty", action="store_true")
    workload.add_argument("--uncertainty-floor", type=float, default=0.0)
    workload.add_argument("--quiet", action="store_true")
    workload.set_defaults(func=_workload)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())