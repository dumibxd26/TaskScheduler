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


def _rank_stability(earlier: dict, later: dict) -> dict:
    earlier_rank = {
        row["policy"]: index
        for index, row in enumerate(earlier["ranking"], start=1)
    }
    later_rank = {
        row["policy"]: index
        for index, row in enumerate(later["ranking"], start=1)
    }
    common = sorted(set(earlier_rank) & set(later_rank))
    return {
        policy: {
            "earlier_rank": earlier_rank[policy],
            "later_rank": later_rank[policy],
            "change": earlier_rank[policy] - later_rank[policy],
        }
        for policy in common
    }


def _distinct_outcomes(rows: list[dict], case_fields: tuple[str, ...],
                       metric: str) -> dict:
    """How many seeds give genuinely different outcomes per case and cluster.

    Seeds only change a run through sampled uncertainty, jitter, and
    stochastic failures, so identical repeats must not be counted as
    independent evidence.
    """
    outcomes = defaultdict(set)
    for row in rows:
        if row["policy"] in REFERENCE_POLICIES:
            continue
        key = tuple(row[field] for field in case_fields) + (row["policy"],)
        outcomes[key].add(round(row[metric], 9))
    by_cluster = defaultdict(list)
    for key, values in outcomes.items():
        by_cluster[key[0]].append(len(values))
    return {
        cluster: {"mean_distinct": statistics.fmean(counts),
                  "min_distinct": min(counts), "max_distinct": max(counts)}
        for cluster, counts in sorted(by_cluster.items())
    }


def _cross_leaders(rows: list[dict], case_fields: tuple[str, ...], metric: str,
                   row_field: str, column_field: str) -> dict:
    """Lowest-mean-regret deployable policy for every (row, column) cell.

    Regret is relative to the best deployable policy of each case, exactly as
    in the phase rankings, and exact ties are reported together.
    """
    deployable = [row for row in rows if row["policy"] not in REFERENCE_POLICIES]
    best = {}
    for row in deployable:
        key = tuple(row[field] for field in case_fields)
        best[key] = min(best.get(key, math.inf), row[metric])
    regrets = defaultdict(lambda: defaultdict(list))
    for row in deployable:
        key = tuple(row[field] for field in case_fields)
        regrets[(row[row_field], row[column_field])][row["policy"]].append(
            row[metric] / best[key] - 1)
    table: dict = defaultdict(dict)
    for (row_value, column_value), by_policy in sorted(regrets.items()):
        means = {
            policy: statistics.fmean(values)
            for policy, values in by_policy.items()
        }
        target = min(means.values())
        leaders = sorted(
            policy for policy, value in means.items()
            if math.isclose(value, target, rel_tol=1e-12, abs_tol=1e-12))
        table[row_value][column_value] = {
            "policies": leaders,
            "mean_regret": target,
            "runner_up_mean_regret": min(
                (value for policy, value in means.items()
                 if policy not in leaders), default=None),
            "heft_ra_mean_regret": means.get("heft-ra"),
            "cases": max(len(values) for values in by_policy.values()),
        }
    return dict(table)


CLOSED_CONTRASTS = (
    ("heft-ra", "heft"), ("heft-ra-cap", "heft-ra"), ("heft-ra-cap", "heft"),
    ("heft", "eft"), ("heft", "k8s-default"),
    ("legacy-tuned", "legacy-v1"), ("adaptive", "heft"), ("heft-ra", "adaptive"),
)
WORKLOAD_CONTRASTS = (
    ("heft-ra", "heft"), ("heft-ra-cap", "heft-ra"), ("heft-ra-cap", "heft"),
    ("adaptive", "adaptive-no-preempt"),
    ("adaptive", "heft"), ("heft", "k8s-default"), ("legacy-tuned", "legacy-v1"),
)
# Later holdouts are reported only when their records exist.
HOLDOUT_PHASES = ("holdout", "holdout2")


def _contrasts(rows: list[dict], pairs, metric: str,
               key_fields: tuple[str, ...]) -> list[dict]:
    available = _deployable_policies(rows)
    return [
        _paired(rows, metric, left, right, key_fields)
        for left, right in pairs
        if left in available and right in available
    ]


def _phase_table(lines: list[str], surface: str, summary: dict) -> None:
    lines.extend([f"Records: {summary['records']}; cases: {summary['cases']}.", ""])
    if surface == "closed":
        lines.extend([
            "Ranked by median regret, then P95 regret, then mean regret.", "",
            "| Rank | Policy | Median regret | P95 regret | Mean regret | "
            "95% CI | Win/tie rate |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ])
        for rank, row in enumerate(summary["ranking"], start=1):
            low, high = row["mean_regret_ci95"]
            lines.append(
                f"| {rank} | {row['policy']} | {row['median_regret']:.2%} | "
                f"{row['p95_regret']:.2%} | {row['mean_regret']:.2%} | "
                f"[{low:.2%}, {high:.2%}] | {row['win_rate']:.2%} |")
    else:
        lines.extend([
            "Ranked by median response regret, then P95 response regret.", "",
            "| Rank | Policy | Median response regret | P95 response regret | "
            "Deadline hit | Median makespan (s) |",
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


def _holdout_sections(lines: list[str], section: dict, surface: str,
                      phase: str, phase_title: str) -> None:
    """Paired contrasts, family leaders and the cross table for one holdout."""
    lines.extend([f"### {phase_title}: paired contrasts", "",
                  "| Policy vs baseline | Metric | Cases | Mean ratio | 95% CI | "
                  "Wins / ties / losses |",
                  "|---|---|---:|---:|---|---:|"])
    for row in section[f"{phase}_contrasts"]:
        low, high = row["mean_ratio_ci95"]
        lines.append(
            f"| {row['left']} vs {row['right']} | {row['metric']} | {row['cases']} | "
            f"{row['mean_ratio']:.4f} | [{low:.4f}, {high:.4f}] | "
            f"{row['left_wins']} / {row['ties']} / {row['left_losses']} |")
    lines.append("")
    family_sections = (
        ((f"{phase}_by_workflow_family", "Workflow family"),
         (f"{phase}_by_cluster_family", "Cluster family"))
        if surface == "closed" else
        ((f"{phase}_by_workload", "Workload"),
         (f"{phase}_by_cluster_family", "Cluster family")))
    for key, label in family_sections:
        lines.extend([f"### {phase_title}: leaders by {label.lower()}", ""])
        if surface == "closed":
            lines.extend([f"| {label} | Lowest mean regret | Mean regret | "
                          "Lowest P95 regret |", "|---|---|---:|---|"])
            for family, summary in section[key].items():
                mean = summary["leaders"]["mean_regret"]
                tail = summary["leaders"]["p95_regret"]
                lines.append(
                    f"| {family} | {', '.join(mean['policies'])} | "
                    f"{mean['value']:.2%} | {', '.join(tail['policies'])} |")
        else:
            lines.extend([f"| {label} | Lowest mean response regret | "
                          "Best deadline hit | Lowest median makespan |",
                          "|---|---|---|---|"])
            for family, summary in section[key].items():
                response = summary["leaders"]["mean_response_regret"]
                deadline = summary["leaders"].get("deadline_hit_rate")
                makespan = summary["leaders"]["median_makespan_s"]
                lines.append(
                    f"| {family} | {', '.join(response['policies'])} | "
                    f"{', '.join(deadline['policies']) if deadline else 'n/a'} | "
                    f"{', '.join(makespan['policies'])} |")
        lines.append("")
    cross = section.get(f"{phase}_cross_leaders")
    if cross:
        row_label = "Workflow family" if surface == "closed" else "Workload"
        columns = sorted({column for cells in cross.values() for column in cells})
        lines.extend([
            f"### {phase_title}: leaders by {row_label.lower()} and cluster family",
            "",
            "Each cell names the policy (or exact tie) with the lowest mean "
            "regret in that cell, followed by that regret and, in brackets, "
            "HEFT-RA's own mean regret.",
            "",
            f"| {row_label} | " + " | ".join(columns) + " |",
            "|---|" + "---|" * len(columns),
        ])
        for row_value, cells in cross.items():
            rendered = []
            for column in columns:
                cell = cells.get(column)
                if cell is None:
                    rendered.append("n/a")
                    continue
                heft_ra = cell["heft_ra_mean_regret"]
                suffix = f" [{heft_ra:.2%}]" if heft_ra is not None else ""
                rendered.append(
                    f"{', '.join(cell['policies'])} "
                    f"({cell['mean_regret']:.2%}){suffix}")
            lines.append(f"| {row_value} | " + " | ".join(rendered) + " |")
        lines.append("")


def _markdown(report: dict) -> str:
    lines = [
        "# Final Evidence Synthesis",
        "",
        "Generated from retained JSONL artifacts with bootstrap seed 2026.",
        "Exploratory seeds 0-19 use each cluster's declared uncertainty and were",
        "used for development (including the design of HEFT-RA). Confirmatory",
        "seeds 200-249 and untouched holdout seeds 400-449 multiply every",
        "node's performance, power, thermal and reliability parameters and every",
        "link's bandwidth, latency and jitter by seeded factors drawn uniformly",
        "from [0.9, 1.1] (or the cluster's wider declared range), so every seed",
        "is a distinct cluster instance. Holdout data was never used for any",
        "choice. Seeds 600-649 form a second, fresh holdout with the same",
        "perturbation, run once after HEFT-RA-cap (capacity-aware lookahead) was",
        "added following the live validation.",
        "",
    ]
    titles = {
        "exploratory": "Exploratory, all policies (development)",
        "exploratory_common": "Exploratory, confirmation policy set",
        "confirmatory": "Confirmatory (+/-10% perturbation)",
        "holdout": "Holdout (+/-10% perturbation, untouched)",
        "holdout2": "Fresh holdout (seeds 600-649, run once for the final policy set)",
    }
    for surface, title in (("closed", "Closed DAGs"),
                           ("workload", "Overlapping Workloads")):
        lines.extend([f"## {title}", ""])
        for phase, phase_title in titles.items():
            if phase not in report[surface]:
                continue
            lines.extend([f"### {phase_title}", ""])
            _phase_table(lines, surface, report[surface][phase])
        lines.extend(["### Independence of seeds", "",
                      "Distinct outcomes per (cluster, case, policy): 20 "
                      "exploratory seeds use the declared uncertainty, 50 "
                      "holdout seeds use the uncertainty floor.", "",
                      "| Cluster | Exploratory mean (of 20) | Min | Max | "
                      "Holdout mean (of 50) | Min | Max |",
                      "|---|---:|---:|---:|---:|---:|---:|"])
        holdout_distinct = report[surface].get("distinct_outcomes_holdout", {})
        for cluster, row in report[surface]["distinct_outcomes_exploratory"].items():
            later = holdout_distinct.get(cluster)
            later_text = (
                f"{later['mean_distinct']:.1f} | {later['min_distinct']} | "
                f"{later['max_distinct']}" if later else "n/a | n/a | n/a")
            lines.append(f"| {cluster} | {row['mean_distinct']:.1f} | "
                         f"{row['min_distinct']} | {row['max_distinct']} | "
                         f"{later_text} |")
        lines.append("")
        for phase in HOLDOUT_PHASES:
            if f"{phase}_contrasts" in report[surface]:
                _holdout_sections(lines, report[surface], surface, phase,
                                  titles[phase])

    reference_rows = report["closed"]["exploratory"]["reference_comparison"]
    if reference_rows:
        lines.extend([
            "## Static Oracle Subset (development phase)",
            "",
            "The oracle is excluded from deployable-policy regret. This table",
            "reports the tractable exploratory cases where it exists.",
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
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    sim = ROOT / "results" / "sim"
    for name, default in (
        ("closed-exploratory", sim / "closed-exploratory-s0-19/records.jsonl"),
        ("closed-confirmatory", sim / "closed-confirmatory-s200-249/records.jsonl"),
        ("closed-holdout", sim / "closed-holdout-s400-449/records.jsonl"),
        ("workload-exploratory",
         sim / "workload-exploratory-s0-19/workload-records.jsonl"),
        ("workload-confirmatory",
         sim / "workload-confirmatory-s200-249/workload-records.jsonl"),
        ("workload-holdout", sim / "workload-holdout-s400-449/workload-records.jsonl"),
        ("closed-holdout2", sim / "closed-holdout2-s600-649/records.jsonl"),
        ("workload-holdout2",
         sim / "workload-holdout2-s600-649/workload-records.jsonl"),
    ):
        parser.add_argument(f"--{name}", type=Path, default=default)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "results/final-evidence-synthesis.json")
    args = parser.parse_args()

    rows = {
        ("closed", "exploratory"): _read_jsonl(args.closed_exploratory),
        ("closed", "confirmatory"): _read_jsonl(args.closed_confirmatory),
        ("closed", "holdout"): _read_jsonl(args.closed_holdout),
        ("workload", "exploratory"): _read_jsonl(args.workload_exploratory),
        ("workload", "confirmatory"): _read_jsonl(args.workload_confirmatory),
        ("workload", "holdout"): _read_jsonl(args.workload_holdout),
    }
    for surface, path in (("closed", args.closed_holdout2),
                          ("workload", args.workload_holdout2)):
        if path.exists():
            rows[(surface, "holdout2")] = _read_jsonl(path)
    report: dict = {"schema_version": 2}
    for surface, summarizer, case_fields, metric, family_fields, pairs in (
        ("closed", _closed_summary, ("cluster_id", "workflow_id", "seed"),
         "makespan_s", ("workflow_family", "cluster_family"), CLOSED_CONTRASTS),
        ("workload", _workload_summary, ("cluster_id", "workload_id", "seed"),
         "mean_response_s", ("workload_id", "cluster_family"), WORKLOAD_CONTRASTS),
    ):
        exploratory = rows[(surface, "exploratory")]
        confirmatory = rows[(surface, "confirmatory")]
        holdout = rows[(surface, "holdout")]
        holdouts = {phase: rows[(surface, phase)] for phase in HOLDOUT_PHASES
                    if (surface, phase) in rows}
        common_set = (_deployable_policies(exploratory)
                      & _deployable_policies(confirmatory))
        for phase_rows in holdouts.values():
            common_set &= _deployable_policies(phase_rows)
        common = sorted(common_set)
        exploratory_common = [row for row in exploratory if row["policy"] in common]
        summaries = {
            "exploratory": summarizer(exploratory),
            "exploratory_common": summarizer(exploratory_common),
            "confirmatory": summarizer(confirmatory),
            **{phase: summarizer(phase_rows)
               for phase, phase_rows in holdouts.items()},
        }
        section = {
            **summaries,
            "common_policies": common,
            "rank_stability_exploratory_to_confirmatory": _rank_stability(
                summaries["exploratory_common"], summaries["confirmatory"]),
            "rank_stability_confirmatory_to_holdout": _rank_stability(
                summaries["confirmatory"], summaries["holdout"]),
            "distinct_outcomes_exploratory": _distinct_outcomes(
                exploratory, case_fields[:2], metric),
            "distinct_outcomes_holdout": _distinct_outcomes(
                holdout, case_fields[:2], metric),
            "confirmatory_contrasts": _contrasts(confirmatory, pairs, metric, case_fields),
        }
        if "holdout2" in holdouts:
            section["rank_stability_holdout_to_holdout2"] = _rank_stability(
                summaries["holdout"], summaries["holdout2"])
            section["distinct_outcomes_holdout2"] = _distinct_outcomes(
                holdouts["holdout2"], case_fields[:2], metric)
        primary, secondary = family_fields
        for phase, phase_rows in holdouts.items():
            section[f"{phase}_contrasts"] = _contrasts(
                phase_rows, pairs, metric, case_fields)
            first = (f"{phase}_by_workflow_family" if surface == "closed"
                     else f"{phase}_by_workload")
            section[first] = _grouped_summaries(phase_rows, primary, summarizer)
            section[f"{phase}_by_cluster_family"] = _grouped_summaries(
                phase_rows, secondary, summarizer)
            section[f"{phase}_cross_leaders"] = _cross_leaders(
                phase_rows, case_fields, metric, primary, secondary)
        report[surface] = section

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(_markdown(report) + "\n", encoding="utf-8")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
