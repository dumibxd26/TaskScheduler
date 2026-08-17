"""Write the controlled Legacy factor-activation report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulation.legacy_factor_stress import run_factor_stresses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/legacy-factor-stress.json"),
    )
    args = parser.parse_args()
    report = run_factor_stresses()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, row in report["stresses"].items():
        print(
            f"{name}: full={row['full_choice']}, "
            f"without-factor={row['ablated_choice']}")
    print(f"All factors change choice: {report['all_factors_change_choice']}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())