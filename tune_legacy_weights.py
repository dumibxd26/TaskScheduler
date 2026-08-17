"""Find and evaluate improved weights for the live Legacy scheduler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulation.weight_search import make_cases, search_weights


ROOT = Path(__file__).parent
CLUSTERS = (
    "asymmetric-edge-5",
    "balanced-heterogeneous-4",
    "homogeneous-4",
    "local-i5-8300h",
    "unreliable-thermal-3",
)
TRAIN_WORKFLOWS = (
    "cache-sensitive",
    "cpu-heavy-8",
    "critical-deadline",
    "data-heavy-pipeline",
    "fanout-1to8",
    "gang-4",
    "io-heavy-6",
    "linear-5",
    "memory-heavy-6",
)
VALIDATION_WORKFLOWS = ("deep-10", "fanin-8to1", "mixed-etl")
HOLDOUT_WORKFLOWS = ("checkpoint-long", "diamond", "wide-12")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tune Legacy's exact eight-factor scorer out of sample.")
    parser.add_argument("--candidates", type=int, default=128)
    parser.add_argument("--finalists", type=int, default=12)
    parser.add_argument("--search-seed", type=int, default=2026)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "legacy-weight-search.json",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    train = make_cases(CLUSTERS, TRAIN_WORKFLOWS, (0, 1))
    validation = make_cases(CLUSTERS, VALIDATION_WORKFLOWS, (17, 29, 43))
    holdout = make_cases(CLUSTERS, HOLDOUT_WORKFLOWS, (101, 103, 107, 109, 113))
    report = search_weights(
        ROOT,
        train,
        validation,
        holdout,
        candidate_count=args.candidates,
        finalist_count=args.finalists,
        search_seed=args.search_seed,
        progress=None if args.quiet else print,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    selected = report["selected_weights"]
    holdout = report["holdout"]["warm_profiles"]["tuned_relative_to_baseline"]
    print(f"Selected weights: {json.dumps(selected, sort_keys=True)}")
    print(f"Warm holdout objective: {holdout['objective']:.6f}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())