"""Analyze policy tradeoffs in multi-workflow experiment records."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize(rows, best_by_case):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["policy"]].append(row)
    summary = {}
    for policy, policy_rows in sorted(grouped.items()):
        regrets = [
            row["mean_response_s"] / best_by_case[
                (row["cluster_id"], row["workload_id"], row["seed"])
            ] - 1.0
            for row in policy_rows
        ]
        deadlines = [
            row["deadline_hit_rate"] for row in policy_rows
            if row["deadline_hit_rate"] is not None
        ]
        summary[policy] = {
            "cases": len(policy_rows),
            "median_mean_response_s": statistics.median(
                row["mean_response_s"] for row in policy_rows),
            "mean_mean_response_s": statistics.fmean(
                row["mean_response_s"] for row in policy_rows),
            "median_p95_response_s": statistics.median(
                row["p95_response_s"] for row in policy_rows),
            "median_makespan_s": statistics.median(
                row["makespan_s"] for row in policy_rows),
            "median_energy_j": statistics.median(
                row["energy_j"] for row in policy_rows),
            "deadline_hit_rate": (
                statistics.fmean(deadlines) if deadlines else None),
            "mean_preemptions": statistics.fmean(
                row["task_preemptions"] for row in policy_rows),
            "median_response_regret": statistics.median(regrets),
            "p95_response_regret": percentile(regrets, 0.95),
        }
    return summary


def pareto_policies(summary):
    objectives = (
        ("median_mean_response_s", False),
        ("mean_mean_response_s", False),
        ("p95_response_regret", False),
        ("median_makespan_s", False),
        ("median_energy_j", False),
        ("deadline_hit_rate", True),
    )
    nondominated = []
    for policy, candidate in summary.items():
        dominated = False
        for other_policy, other in summary.items():
            if other_policy == policy:
                continue
            no_worse = True
            strictly_better = False
            for field, maximize in objectives:
                candidate_value = candidate[field]
                other_value = other[field]
                if candidate_value is None or other_value is None:
                    continue
                if maximize:
                    no_worse &= other_value >= candidate_value
                    strictly_better |= other_value > candidate_value
                else:
                    no_worse &= other_value <= candidate_value
                    strictly_better |= other_value < candidate_value
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            nondominated.append(policy)
    return sorted(nondominated)


def analyze(path):
    rows = [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    best_by_case = {}
    for row in rows:
        key = (row["cluster_id"], row["workload_id"], row["seed"])
        best_by_case[key] = min(
            best_by_case.get(key, math.inf), row["mean_response_s"])
    overall = summarize(rows, best_by_case)
    by_workload = {
        name: summarize(
            [row for row in rows if row["workload_id"] == name], best_by_case)
        for name in sorted({row["workload_id"] for row in rows})
    }
    by_cluster_family = {
        name: summarize(
            [row for row in rows if row["cluster_family"] == name], best_by_case)
        for name in sorted({row["cluster_family"] for row in rows})
    }
    return {
        "record_count": len(rows),
        "overall": overall,
        "pareto_policies": pareto_policies(overall),
        "by_workload": by_workload,
        "by_cluster_family": by_cluster_family,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("records")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = analyze(args.records)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()