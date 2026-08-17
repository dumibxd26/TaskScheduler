"""Evaluate tuned Legacy factor necessity on a fresh scenario partition."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from services.scheduler import LEGACY_TUNED_WEIGHTS, LEGACY_V1_WEIGHTS
from simulation.weight_search import (
    WeightEvaluator,
    leave_one_factor_out,
    make_cases,
    score_metrics,
)


ROOT = Path(__file__).parent


def _comparison(metrics, reference):
    wins = sum(
        candidate.makespan_s < baseline.makespan_s - 1e-9
        for candidate, baseline in zip(metrics, reference)
    )
    losses = sum(
        candidate.makespan_s > baseline.makespan_s + 1e-9
        for candidate, baseline in zip(metrics, reference)
    )
    return {
        "makespan_wins": wins,
        "makespan_ties": len(metrics) - wins - losses,
        "makespan_losses": losses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a fresh-seed leave-one-factor-out Legacy study.")
    parser.add_argument("--seeds", default="300:310")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "legacy-factor-ablation.json",
    )
    args = parser.parse_args()
    start, stop = (int(value) for value in args.seeds.split(":", 1))
    clusters = sorted(path.stem for path in (
        ROOT / "scenarios" / "clusters").glob("*.yaml"))
    workflows = sorted(path.stem for path in (
        ROOT / "scenarios" / "workflows").glob("*.yaml"))
    cases = make_cases(clusters, workflows, range(start, stop))
    evaluator = WeightEvaluator(ROOT, cases)
    baseline_metrics = evaluator.evaluate(LEGACY_V1_WEIGHTS)
    tuned_metrics = evaluator.evaluate(LEGACY_TUNED_WEIGHTS)
    tuned_score = score_metrics(
        LEGACY_TUNED_WEIGHTS, tuned_metrics, baseline_metrics)

    variants = {
        "legacy-v1": LEGACY_V1_WEIGHTS,
        "legacy-tuned": LEGACY_TUNED_WEIGHTS,
        **leave_one_factor_out(LEGACY_TUNED_WEIGHTS),
    }
    rows = []
    for name, weights in variants.items():
        metrics = (
            baseline_metrics if name == "legacy-v1"
            else tuned_metrics if name == "legacy-tuned"
            else evaluator.evaluate(weights)
        )
        score = score_metrics(weights, metrics, baseline_metrics)
        rows.append({
            "variant": name,
            "weights": asdict(weights),
            "objective_vs_v1": score.objective,
            "objective_delta_vs_tuned": score.objective - tuned_score.objective,
            "components_vs_v1": score.components,
            "deadline_misses": score.deadline_misses,
            "task_retries": score.task_retries,
            **_comparison(metrics, tuned_metrics),
        })
    rows.sort(key=lambda row: row["objective_vs_v1"])
    report = {
        "schema_version": 1,
        "method": {
            "study": "leave-one-factor-out",
            "seed_range": [start, stop - 1],
            "clusters": clusters,
            "workflows": workflows,
            "cases": len(cases),
            "profiles": "12 seeded calibration observations per task/node",
            "calibration_uncertainty": "sampled per case seed",
            "normalization": "absolute weight scale fixed at 125",
        },
        "ranking": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for rank, row in enumerate(rows, start=1):
        print(
            f"{rank:>2}. {row['variant']:<30} "
            f"objective={row['objective_vs_v1']:.6f} "
            f"delta_vs_tuned={row['objective_delta_vs_tuned']:+.6f}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())