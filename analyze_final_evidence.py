"""Synthesize exploratory, confirmatory, ablation, and live evidence."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from simulation.experiments import REFERENCE_POLICIES


ROOT = Path(__file__).parent


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _bootstrap_mean_ci(values: list[float], *, seed: int = 2026,
                       samples: int = 2_000) -> list[float]:
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    return [_percentile(means, 0.025), _percentile(means, 0.975)]


def _metric_leaders(ranking: list[dict],
                    metrics: dict[str, bool]) -> dict[str, dict]:
    leaders = {}
    for metric, maximize in metrics.items():
        eligible = [row for row in ranking if row.get(metric) is not None]
        if not eligible:
            continue
        target = (max if maximize else min)(row[metric] for row in eligible)
        leaders[metric] = {
            "policies": sorted(
                row["policy"] for row in eligible
                if math.isclose(row[metric], target, rel_tol=1e-12, abs_tol=1e-12)
            ),
            "value": target,
        }
    return leaders


def _reference_comparison(deployable_rows: list[dict],
                          reference_rows: list[dict]) -> list[dict]:
    reference_by_case = {}
    for row in reference_rows:
        key = (row["cluster_id"], row["workflow_id"], row["seed"])
        reference_by_case[key] = min(
            reference_by_case.get(key, math.inf), row["makespan_s"])
    grouped = defaultdict(list)
    for row in deployable_rows:
        key = (row["cluster_id"], row["workflow_id"], row["seed"])
        if key in reference_by_case:
            grouped[row["policy"]].append(
                row["makespan_s"] / reference_by_case[key] - 1)
    ranking = []
    for policy, regrets in grouped.items():
        ranking.append({
            "policy": policy,
            "cases": len(regrets),
            "mean_regret": statistics.fmean(regrets),
            "mean_regret_ci95": _bootstrap_mean_ci(regrets),
            "median_regret": statistics.median(regrets),
            "p95_regret": _percentile(regrets, 0.95),
        })
    ranking.sort(key=lambda row: (
        row["mean_regret"], row["p95_regret"], row["policy"]))
    return ranking


def _closed_summary(rows: list[dict]) -> dict:
    reference_rows = [
        row for row in rows if row["policy"] in REFERENCE_POLICIES]
    deployable_rows = [
        row for row in rows if row["policy"] not in REFERENCE_POLICIES]
    best = {}
    grouped = defaultdict(list)
    for row in deployable_rows:
        key = (row["cluster_id"], row["workflow_id"], row["seed"])
        best[key] = min(best.get(key, math.inf), row["makespan_s"])
    for row in deployable_rows:
        key = (row["cluster_id"], row["workflow_id"], row["seed"])
        grouped[row["policy"]].append((row, row["makespan_s"] / best[key] - 1))
    ranking = []
    for policy, values in grouped.items():
        regrets = [regret for _, regret in values]
        deadline_rows = [
            row for row, _ in values if row["deadline_met"] is not None]
        ranking.append({
            "policy": policy,
            "cases": len(values),
            "win_rate": sum(regret <= 1e-9 for regret in regrets) / len(regrets),
            "mean_regret": statistics.fmean(regrets),
            "mean_regret_ci95": _bootstrap_mean_ci(regrets),
            "median_regret": statistics.median(regrets),
            "p95_regret": _percentile(regrets, 0.95),
            "median_makespan_s": statistics.median(
                row["makespan_s"] for row, _ in values),
            "median_energy_j": statistics.median(
                row["energy_j"] for row, _ in values),
            "median_transfer_mib": statistics.median(
                row["network_transfer_mib"] for row, _ in values),
            "deadline_hit_rate": (
                statistics.fmean(row["deadline_met"] for row in deadline_rows)
                if deadline_rows else None
            ),
        })
    ranking.sort(key=lambda row: (
        row["median_regret"], row["p95_regret"], row["mean_regret"],
        row["policy"],
    ))
    return {
        "records": len(rows),
        "deployable_records": len(deployable_rows),
        "reference_records": len(reference_rows),
        "cases": len(best),
        "ranking": ranking,
        "leaders": _metric_leaders(ranking, {
            "mean_regret": False,
            "p95_regret": False,
            "median_makespan_s": False,
            "median_energy_j": False,
            "median_transfer_mib": False,
            "deadline_hit_rate": True,
        }),
        "reference_comparison": _reference_comparison(
            deployable_rows, reference_rows),
    }


def _workload_summary(rows: list[dict]) -> dict:
    best = {}
    grouped = defaultdict(list)
    for row in rows:
        key = (row["cluster_id"], row["workload_id"], row["seed"])
        best[key] = min(best.get(key, math.inf), row["mean_response_s"])
    for row in rows:
        key = (row["cluster_id"], row["workload_id"], row["seed"])
        grouped[row["policy"]].append(
            (row, row["mean_response_s"] / best[key] - 1))
    ranking = []
    for policy, values in grouped.items():
        regrets = [regret for _, regret in values]
        deadline_rows = [
            row for row, _ in values if row["deadline_hit_rate"] is not None]
        ranking.append({
            "policy": policy,
            "cases": len(values),
            "median_response_regret": statistics.median(regrets),
            "p95_response_regret": _percentile(regrets, 0.95),
            "mean_response_regret": statistics.fmean(regrets),
            "mean_response_regret_ci95": _bootstrap_mean_ci(regrets),
            "median_mean_response_s": statistics.median(
                row["mean_response_s"] for row, _ in values),
            "median_p95_response_s": statistics.median(
                row["p95_response_s"] for row, _ in values),
            "median_makespan_s": statistics.median(
                row["makespan_s"] for row, _ in values),
            "median_energy_j": statistics.median(
                row["energy_j"] for row, _ in values),
            "deadline_hit_rate": (
                statistics.fmean(row["deadline_hit_rate"] for row in deadline_rows)
                if deadline_rows else None
            ),
            "mean_preemptions": statistics.fmean(
                row["task_preemptions"] for row, _ in values),
        })
    ranking.sort(key=lambda row: (
        row["median_response_regret"], row["p95_response_regret"],
        row["policy"],
    ))
    return {
        "records": len(rows),
        "cases": len(best),
        "ranking": ranking,
        "leaders": _metric_leaders(ranking, {
            "mean_response_regret": False,
            "p95_response_regret": False,
            "median_mean_response_s": False,
            "median_p95_response_s": False,
            "median_makespan_s": False,
            "median_energy_j": False,
            "deadline_hit_rate": True,
            "mean_preemptions": False,
        }),
    }


def _grouped_summaries(rows: list[dict], field: str, summarizer) -> dict:
    return {
        value: summarizer([row for row in rows if row[field] == value])
        for value in sorted({row[field] for row in rows})
    }


def _deployable_policies(rows: list[dict]) -> set[str]:
    return {
        row["policy"] for row in rows
        if row["policy"] not in REFERENCE_POLICIES
    }


def _paired(rows: list[dict], metric: str, left: str,
            right: str, key_fields: tuple[str, ...]) -> dict:
    by_policy = defaultdict(dict)
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        by_policy[row["policy"]][key] = row
    common = sorted(set(by_policy[left]) & set(by_policy[right]))
    ratios = [
        by_policy[left][key][metric] / by_policy[right][key][metric]
        for key in common
    ]
    differences = [
        by_policy[left][key][metric] - by_policy[right][key][metric]
        for key in common
    ]
    return {
        "left": left,
        "right": right,
        "metric": metric,
        "cases": len(common),
        "mean_ratio": statistics.fmean(ratios),
        "mean_ratio_ci95": _bootstrap_mean_ci(ratios),
        "median_ratio": statistics.median(ratios),
        "p95_ratio": _percentile(ratios, 0.95),
        "left_wins": sum(value < -1e-9 for value in differences),
        "ties": sum(abs(value) <= 1e-9 for value in differences),
        "left_losses": sum(value > 1e-9 for value in differences),
    }


def _rank_stability(exploratory: dict, confirmatory: dict) -> dict:
    exploratory_rank = {
        row["policy"]: index
        for index, row in enumerate(exploratory["ranking"], start=1)
    }
    confirmatory_rank = {
        row["policy"]: index
        for index, row in enumerate(confirmatory["ranking"], start=1)
    }
    common = sorted(set(exploratory_rank) & set(confirmatory_rank))
    return {
        policy: {
            "exploratory_rank": exploratory_rank[policy],
            "confirmatory_rank": confirmatory_rank[policy],
            "change": exploratory_rank[policy] - confirmatory_rank[policy],
        }
        for policy in common
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Final Evidence Synthesis",
        "",
        "This file is generated from retained JSONL artifacts. Confirmatory "
        "seeds were not used to tune Legacy weights.",
        "",
    ]
    for surface, title in (
        ("closed", "Closed DAGs"),
        ("workload", "Overlapping Workloads"),
    ):
        lines.extend([f"## {title}", ""])
        for phase in ("exploratory", "exploratory_common", "confirmatory"):
            summary = report[surface][phase]
            phase_title = {
                "exploratory": "Exploratory (all policies)",
                "exploratory_common": "Exploratory (confirmation policy set)",
                "confirmatory": "Confirmatory",
            }[phase]
            lines.extend([
                f"### {phase_title}",
                "",
                f"Records: {summary['records']}; cases: {summary['cases']}.",
                "",
            ])
            if surface == "closed":
                lines.extend([
                    "| Rank | Policy | Mean regret | 95% CI | P95 regret | Win rate |",
                    "|---:|---|---:|---:|---:|---:|",
                ])
                for rank, row in enumerate(summary["ranking"], start=1):
                    low, high = row["mean_regret_ci95"]
                    lines.append(
                        f"| {rank} | {row['policy']} | {row['mean_regret']:.2%} | "
                        f"[{low:.2%}, {high:.2%}] | {row['p95_regret']:.2%} | "
                        f"{row['win_rate']:.2%} |")
            else:
                lines.extend([
                    "| Rank | Policy | Median response regret | P95 response "
                    "regret | Deadline hit | Median makespan (s) |",
                    "|---:|---|---:|---:|---:|---:|",
                ])
                for rank, row in enumerate(summary["ranking"], start=1):
                    deadline = row["deadline_hit_rate"]
                    deadline_text = f"{deadline:.2%}" if deadline is not None else "n/a"
                    lines.append(
                        f"| {rank} | {row['policy']} | "
                        f"{row['median_response_regret']:.2%} | "
                        f"{row['p95_response_regret']:.2%} | {deadline_text} | "
                        f"{row['median_makespan_s']:.3f} |")
            lines.append("")

        family_key = (
            "confirmatory_by_workflow_family"
            if surface == "closed" else "confirmatory_by_workload"
        )
        lines.extend(["### Confirmatory family leaders", ""])
        if surface == "closed":
            lines.extend([
                "| Family | Lowest mean regret | Mean regret | Lowest P95 regret |",
                "|---|---|---:|---|",
            ])
            for family, summary in report[surface][family_key].items():
                mean = summary["leaders"]["mean_regret"]
                tail = summary["leaders"]["p95_regret"]
                lines.append(
                    f"| {family} | {', '.join(mean['policies'])} | "
                    f"{mean['value']:.2%} | {', '.join(tail['policies'])} |")
        else:
            lines.extend([
                "| Workload | Lowest mean response regret | Best deadline hit | "
                "Lowest median makespan |",
                "|---|---|---|---|",
            ])
            for family, summary in report[surface][family_key].items():
                response = summary["leaders"]["mean_response_regret"]
                deadline = summary["leaders"].get("deadline_hit_rate")
                makespan = summary["leaders"]["median_makespan_s"]
                lines.append(
                    f"| {family} | {', '.join(response['policies'])} | "
                    f"{', '.join(deadline['policies']) if deadline else 'n/a'} | "
                    f"{', '.join(makespan['policies'])} |")
        lines.append("")

    reference_rows = report["closed"]["exploratory"]["reference_comparison"]
    if reference_rows:
        lines.extend([
            "## Static Oracle Subset",
            "",
            "The oracle is excluded from deployable-policy regret. This separate "
            "table reports the 300 tractable exploratory cases where it exists.",
            "",
            "| Policy | Cases | Mean gap | 95% CI | Median gap | P95 gap |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for row in reference_rows:
            low, high = row["mean_regret_ci95"]
            lines.append(
                f"| {row['policy']} | {row['cases']} | "
                f"{row['mean_regret']:.2%} | [{low:.2%}, {high:.2%}] | "
                f"{row['median_regret']:.2%} | {row['p95_regret']:.2%} |")
        lines.append("")

    lines.extend(["## Paired Tuned Legacy Checks", ""])
    for name, paired in report["paired_legacy"].items():
        low, high = paired["mean_ratio_ci95"]
        lines.extend([
            f"- **{name}:** tuned/v1 mean ratio {paired['mean_ratio']:.4f} "
            f"(95% bootstrap CI [{low:.4f}, {high:.4f}]); "
            f"wins/ties/losses {paired['left_wins']}/{paired['ties']}/"
            f"{paired['left_losses']}.",
        ])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--closed-exploratory",
        type=Path,
        default=ROOT / "results/final-all-legacy-20seed/records.jsonl",
    )
    parser.add_argument(
        "--closed-confirmatory",
        type=Path,
        default=ROOT / "results/confirmatory-single-seeds200-249/records.jsonl",
    )
    parser.add_argument(
        "--workload-exploratory",
        type=Path,
        default=ROOT / "results/final-all-workload-20seed/workload-records.jsonl",
    )
    parser.add_argument(
        "--workload-confirmatory",
        type=Path,
        default=ROOT / "results/confirmatory-workload-seeds200-249/workload-records.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results/final-evidence-synthesis.json",
    )
    args = parser.parse_args()

    closed_exploratory_rows = _read_jsonl(args.closed_exploratory)
    closed_confirmatory_rows = _read_jsonl(args.closed_confirmatory)
    workload_exploratory_rows = _read_jsonl(args.workload_exploratory)
    workload_confirmatory_rows = _read_jsonl(args.workload_confirmatory)
    closed_common_policies = sorted(
        _deployable_policies(closed_exploratory_rows)
        & _deployable_policies(closed_confirmatory_rows)
    )
    workload_common_policies = sorted(
        _deployable_policies(workload_exploratory_rows)
        & _deployable_policies(workload_confirmatory_rows)
    )
    closed_exploratory_common_rows = [
        row for row in closed_exploratory_rows
        if row["policy"] in closed_common_policies
    ]
    workload_exploratory_common_rows = [
        row for row in workload_exploratory_rows
        if row["policy"] in workload_common_policies
    ]
    closed_exploratory = _closed_summary(closed_exploratory_rows)
    closed_exploratory_common = _closed_summary(
        closed_exploratory_common_rows)
    closed_confirmatory = _closed_summary(closed_confirmatory_rows)
    workload_exploratory = _workload_summary(workload_exploratory_rows)
    workload_exploratory_common = _workload_summary(
        workload_exploratory_common_rows)
    workload_confirmatory = _workload_summary(workload_confirmatory_rows)
    report = {
        "schema_version": 1,
        "closed": {
            "exploratory": closed_exploratory,
            "exploratory_common": closed_exploratory_common,
            "confirmatory": closed_confirmatory,
            "common_policies": closed_common_policies,
            "rank_stability": _rank_stability(
                closed_exploratory_common, closed_confirmatory),
            "confirmatory_by_workflow_family": _grouped_summaries(
                closed_confirmatory_rows, "workflow_family", _closed_summary),
            "confirmatory_by_cluster_family": _grouped_summaries(
                closed_confirmatory_rows, "cluster_family", _closed_summary),
        },
        "workload": {
            "exploratory": workload_exploratory,
            "exploratory_common": workload_exploratory_common,
            "confirmatory": workload_confirmatory,
            "common_policies": workload_common_policies,
            "rank_stability": _rank_stability(
                workload_exploratory_common, workload_confirmatory),
            "confirmatory_by_workload": _grouped_summaries(
                workload_confirmatory_rows, "workload_id", _workload_summary),
            "confirmatory_by_cluster_family": _grouped_summaries(
                workload_confirmatory_rows, "cluster_family", _workload_summary),
        },
        "paired_legacy": {
            "closed_exploratory": _paired(
                closed_exploratory_rows,
                "makespan_s",
                "legacy-tuned",
                "legacy-v1",
                ("cluster_id", "workflow_id", "seed"),
            ),
            "closed_confirmatory": _paired(
                closed_confirmatory_rows,
                "makespan_s",
                "legacy-tuned",
                "legacy-v1",
                ("cluster_id", "workflow_id", "seed"),
            ),
            "workload_exploratory": _paired(
                workload_exploratory_rows,
                "mean_response_s",
                "legacy-tuned",
                "legacy-v1",
                ("cluster_id", "workload_id", "seed"),
            ),
            "workload_confirmatory": _paired(
                workload_confirmatory_rows,
                "mean_response_s",
                "legacy-tuned",
                "legacy-v1",
                ("cluster_id", "workload_id", "seed"),
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
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