"""Tune and holdout-test the contextual simulator scheduling profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from simulation.contextual_search import (
    evaluate_holdout,
    fit_contextual_profile,
    load_default_suite,
    search_adaptive_weights,
)


ROOT = Path(__file__).parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tune Adaptive penalties and a guarded contextual portfolio.")
    parser.add_argument("--candidates", type=int, default=64)
    parser.add_argument("--search-seed", type=int, default=2026)
    parser.add_argument("--thermal-step", type=float, default=60.0)
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=ROOT / "scenarios" / "policy-profiles" / "contextual-v1.yaml",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=ROOT / "results" / "contextual-policy-search.json",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    progress = None if args.quiet else print
    cases = load_default_suite(ROOT)
    tuned, adaptive_report = search_adaptive_weights(
        ROOT,
        cases,
        candidate_count=args.candidates,
        search_seed=args.search_seed,
        thermal_step_s=args.thermal_step,
        progress=progress,
    )
    profile, contextual_report = fit_contextual_profile(
        ROOT,
        cases,
        tuned,
        thermal_step_s=args.thermal_step,
        progress=progress,
    )
    holdout_report = evaluate_holdout(
        ROOT,
        cases,
        profile,
        tuned,
        thermal_step_s=args.thermal_step,
    )
    report = {
        "schema_version": 1,
        "protocol": {
            "training_suite": "generic-v1",
            "holdout_used_for_selection": False,
            "thermal_step_s": args.thermal_step,
        },
        "adaptive_search": adaptive_report,
        "contextual_fit": contextual_report,
        "holdout": holdout_report,
        "profile": profile.to_mapping(),
    }
    args.profile_output.parent.mkdir(parents=True, exist_ok=True)
    args.profile_output.write_text(
        yaml.safe_dump(profile.to_mapping(), sort_keys=False),
        encoding="utf-8",
    )
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    selected = adaptive_report["selected"]
    holdout = holdout_report["overall_relative_to_heft"][profile.name]
    print(f"Selected Adaptive candidate: {selected}")
    print(f"Contextual holdout objective vs HEFT: {holdout['objective']:.6f}")
    print(f"Profile: {args.profile_output}")
    print(f"Report: {args.report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())