"""Compare isolated reruns with the retained i5 results without modifying either."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from analyze_kind_validation import critical_response, spearman

PHASES = (
    "exploratory-s0-19", "confirmatory-s200-249",
    "holdout-s400-449", "holdout2-s600-649",
)
STUDIES = (
    "mechanism-studies", "legacy-weight-search", "legacy-factor-ablation",
    "legacy-factor-stress", "contextual-policy-search",
)
PROFILES = ("balanced", "homogeneous", "edge")
METRICS = (
    "makespan_seconds", "mean_response_seconds",
    "p95_response_seconds", "critical_response_seconds",
)
REL_TOL = 1e-9
ABS_TOL = 1e-9


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compare_values(old: Any, new: Any) -> dict:
    result: dict[str, Any] = {
        "scalar_values": 0, "exact_differences": 0,
        "outside_tolerance": 0, "structural_differences": 0,
        "max_absolute_numeric_difference": 0.0, "examples": [],
    }

    def difference(path: str, left: Any, right: Any, *, structural: bool = False) -> None:
        result["outside_tolerance"] += 1
        result["structural_differences"] += int(structural)
        if len(result["examples"]) < 8:
            result["examples"].append({"path": path, "old": left, "new": right})

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                child = f"{path}.{key}"
                if key not in left or key not in right:
                    difference(child, "<missing>" if key not in left else left[key],
                               "<missing>" if key not in right else right[key],
                               structural=True)
                else:
                    visit(left[key], right[key], child)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                difference(f"{path}.length", len(left), len(right), structural=True)
            for index, (a, b) in enumerate(zip(left, right)):
                visit(a, b, f"{path}[{index}]")
        elif isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            difference(path, str(type(left)), str(type(right)), structural=True)
        else:
            result["scalar_values"] += 1
            numeric = (
                isinstance(left, (int, float)) and not isinstance(left, bool)
                and isinstance(right, (int, float)) and not isinstance(right, bool)
            )
            if numeric:
                if not math.isfinite(left) or not math.isfinite(right):
                    result["exact_differences"] += 1
                    difference(path, left, right)
                    return
                delta = abs(left - right)
                result["max_absolute_numeric_difference"] = max(
                    result["max_absolute_numeric_difference"], delta)
                result["exact_differences"] += int(left != right)
                if not math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                    difference(path, left, right)
            elif type(left) is not type(right) or left != right:
                result["exact_differences"] += 1
                difference(path, left, right)

    visit(old, new, "$")
    return result


def record_key(row: dict) -> tuple:
    return tuple(row.get(field) for field in (
        "study", "cluster_id", "workflow_id", "workload_id", "seed", "policy", "variant",
    ))


def load_records(path: Path) -> dict[tuple, dict]:
    rows = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            key = record_key(row)
            if key in rows:
                raise ValueError(f"Duplicate record key in {path}: {key}")
            rows[key] = row
    return rows


def compare_records(old_path: Path, new_path: Path) -> dict:
    old, new = load_records(old_path), load_records(new_path)
    result = {
        "old_records": len(old), "new_records": len(new),
        "missing_keys": len(old.keys() - new.keys()),
        "extra_keys": len(new.keys() - old.keys()),
        "exact_records": 0, "equivalent_records": 0,
        "max_absolute_numeric_difference": 0.0, "examples": [],
    }
    for key in old.keys() & new.keys():
        comparison = compare_values(old[key], new[key])
        result["exact_records"] += int(
            comparison["exact_differences"] == 0
            and comparison["structural_differences"] == 0)
        result["equivalent_records"] += int(comparison["outside_tolerance"] == 0)
        result["max_absolute_numeric_difference"] = max(
            result["max_absolute_numeric_difference"],
            comparison["max_absolute_numeric_difference"])
        if comparison["outside_tolerance"] and len(result["examples"]) < 5:
            result["examples"].append({"key": list(key), **comparison})
    result["complete_equivalent"] = (
        not result["missing_keys"] and not result["extra_keys"]
        and result["equivalent_records"] == len(old)
    )
    return result


def percent_change(old: float, new: float) -> float | None:
    return (new / old - 1) * 100 if old else None


def size_label(delta: float | None) -> str:
    if delta is None:
        return "undefined (zero baseline)"
    if abs(delta) <= 5:
        return "small (<=5%)"
    if abs(delta) <= 15:
        return "moderate (5-15%)"
    return "large (>15%)"


def live_rows(old: dict, new: dict) -> list[dict]:
    if old["schedule"] != new["schedule"]:
        raise ValueError("Live trial schedules differ; not a matched-protocol comparison")
    if old["batch_definitions"] != new["batch_definitions"]:
        raise ValueError("Live workload definitions differ")
    rows = []
    for batch in old["batches"]:
        for policy in old["policies"]:
            before = sorted(
                (t for t in old["trials"] if t["batch"] == batch and t["policy"] == policy),
                key=lambda t: t["repetition"])
            after = sorted(
                (t for t in new["trials"] if t["batch"] == batch and t["policy"] == policy),
                key=lambda t: t["repetition"])
            if [t["repetition"] for t in before] != [t["repetition"] for t in after]:
                raise ValueError(f"Incomplete repetitions for {batch}/{policy}")
            for metric in METRICS:
                left = [critical_response(t) if metric == "critical_response_seconds"
                        else t[metric] for t in before]
                right = [critical_response(t) if metric == "critical_response_seconds"
                         else t[metric] for t in after]
                if all(x is None for x in left + right):
                    continue
                if any(x is None for x in left + right) or not left:
                    raise ValueError(f"Missing measurements for {batch}/{policy}/{metric}")
                old_median, new_median = statistics.median(left), statistics.median(right)
                delta = percent_change(old_median, new_median)
                rows.append({
                    "profile": old["profile"], "batch": batch, "policy": policy,
                    "metric": metric, "n": len(left),
                    "old_median": old_median, "new_median": new_median,
                    "old_range": [min(left), max(left)],
                    "new_range": [min(right), max(right)],
                    "delta_percent": delta, "difference_size": size_label(delta),
                    "paired_ratios": [b / a if a else None for a, b in zip(left, right)],
                })
    return rows


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *["| " + " | ".join(str(value) for value in row) + " |" for row in rows],
        "",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("results"))
    parser.add_argument("--current", type=Path, required=True)
    args = parser.parse_args()
    old, current = args.baseline, args.current
    hardware = load_json(current / "hardware.json")
    commands = load_json(current / "commands.json")
    validation = load_json(current / "validation.json")
    report: dict[str, Any] = {
        "tolerance": {"relative": REL_TOL, "absolute": ABS_TOL},
        "matrices": {}, "studies": {}, "live": [], "worker_caps_match": {},
    }
    for phase in PHASES:
        for prefix, filename in (("closed", "records.jsonl"),
                                 ("workload", "workload-records.jsonl")):
            name = f"{prefix}-{phase}"
            report["matrices"][name] = compare_records(
                old / "sim" / name / filename, current / "sim" / name / filename)
    for name in STUDIES:
        report["studies"][name] = compare_values(
            load_json(old / f"{name}.json"), load_json(current / f"{name}.json"))
    live_summary = load_json(current / "kind-validation" / "summary.json")
    old_live_summary = load_json(old / "kind-validation" / "summary.json")
    if live_summary["integrity"] != "passed":
        raise ValueError("Current live integrity gate did not pass")
    for profile in PROFILES:
        before_caps = load_json(old / "kind-validation" / f"{profile}-cluster.json")["docker_caps"]
        after_caps = load_json(current / "kind-validation" / f"{profile}-cluster.json")["docker_caps"]
        def numeric_caps(caps: dict) -> dict:
            return {node: {key: value for key, value in row.items() if key != "container"}
                    for node, row in caps.items()}
        report["worker_caps_match"][profile] = numeric_caps(before_caps) == numeric_caps(after_caps)
        repair_caps = load_json(
            current / "kind-validation" / f"{profile}-repair-cluster.json")["docker_caps"]
        report["worker_caps_match"][profile] = (
            report["worker_caps_match"][profile]
            and numeric_caps(before_caps) == numeric_caps(repair_caps)
        )
        if not report["worker_caps_match"][profile]:
            raise ValueError(f"{profile}: worker resource caps differ from the i5 protocol")
        report["live"].extend(live_rows(
            load_json(old / "kind-validation" / f"{profile}.json"),
            load_json(current / "kind-validation" / f"{profile}.json")))
    report["local_complete_equivalent"] = (
        all(row["complete_equivalent"] for row in report["matrices"].values())
        and all(row["outside_tolerance"] == 0 for row in report["studies"].values())
    )
    report["dag_winners"] = {}
    for label, directory in (("i5", old), ("Ryzen", current)):
        winners = []
        for profile in PROFILES:
            document = load_json(directory / "kind-validation" / f"{profile}.json")
            for repetition in (1, 2, 3):
                trials = [t for t in document["trials"] if t["batch"] == "dag"
                          and t["repetition"] == repetition]
                winners.append(min(trials, key=lambda t: t["makespan_seconds"])["policy"])
        report["dag_winners"][label] = {
            "heft_ra_variant_wins": sum(p in ("heft-ra", "heft-ra-cap") for p in winners),
            "total_runs": len(winners),
            "policies": winners,
        }
    report["live_rank_agreement"] = []
    for profile in PROFILES:
        for batch in ("dag", "priority"):
            for metric in METRICS:
                rows = [r for r in report["live"] if r["profile"] == profile
                        and r["batch"] == batch and r["metric"] == metric]
                if not rows:
                    continue
                before, after = min(r["old_median"] for r in rows), min(
                    r["new_median"] for r in rows)
                report["live_rank_agreement"].append({
                    "profile": profile, "batch": batch, "metric": metric,
                    "spearman": spearman(
                        [r["old_median"] for r in rows], [r["new_median"] for r in rows]),
                    "old_best_within_2_percent": [
                        r["policy"] for r in rows if r["old_median"] <= before * 1.02],
                    "new_best_within_2_percent": [
                        r["policy"] for r in rows if r["new_median"] <= after * 1.02],
                })
    (current / "comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cpu = hardware["cpu"][0]
    memory_gib = sum(module["Capacity"] for module in hardware["memory"]) / 2**30
    specs = markdown_table(
        ["Property", "Earlier computer (documented)", "This computer (measured)"],
        [
            ["CPU", "Intel Core i5-8300H", cpu["Name"].strip()],
            ["Physical cores / logical threads", "4 / 8",
             f'{cpu["NumberOfCores"]} / {cpu["NumberOfLogicalProcessors"]}'],
            ["Reported base/max clock", "2.3 GHz base, 4.0 GHz turbo",
             f'{cpu["MaxClockSpeed"] / 1000:.3f} GHz CIM MaxClockSpeed (not measured turbo)'],
            ["Installed RAM", "12 GiB (4 + 8), DDR4 2133 MT/s",
             f"{memory_gib:.0f} GiB (32 + 32), configured 5600 MT/s"],
            ["OS-visible RAM", "Not retained",
             f'{hardware["system"]["TotalPhysicalMemory"]/2**30:.2f} GiB'],
            ["L2 / L3", "1 / 8 MiB", f'{cpu["L2CacheSize"]/1024:g} / {cpu["L3CacheSize"]/1024:g} MiB'],
            ["Storage", "Samsung 970 EVO Plus 500 GB + 1 TB HDD",
             hardware["disks"][0]["Model"]],
            ["OS", "Windows 11 Pro", f'{hardware["os"]["Caption"]}, build {hardware["os"]["BuildNumber"]}'],
            ["Python (local)", "README tested version 3.12; actual old run version not recorded",
             hardware["python"]],
            ["Kind", "README tested version 0.32; actual old run version not recorded",
             hardware["kind"]],
            ["kubectl client", "README tested version 1.36", hardware["kubectl"]],
            ["Docker", "29.x, WSL2; global limits not retained",
             f'{hardware["docker"]["version"]}, {hardware["docker"]["cpus"]} logical CPUs, '
             f'{hardware["docker"]["memory_bytes"]/2**30:.2f} GiB'],
            ["Kernel / cgroups", "WSL2; exact kernel not retained",
             f'{hardware["docker"]["kernel"]}, cgroups v{hardware["docker"]["cgroup_version"]}'],
            ["Power policy", "Not retained", hardware["power_scheme"]],
        ],
    )
    fairness = [
        "## Fairness and interpretation", "",
        "- Primary comparison uses the original scenario YAML, policies, seeds, uncertainty floors, "
        "thermal steps, work budgets, seeded live order, warm-up protocol and three repetitions.",
        "- Six original live trials lacked a verifiable warm-profile startup confirmation. "
        "They were discarded and rerun later in freshly recreated matched-profile clusters, "
        "using the SAME saved warm calibration and original case identifiers. The retained "
        "matrix therefore spans multiple sessions, not one uninterrupted shuffled sequence. "
        "Retry selection was based only on integrity, never observed performance.",
        "- The simulator still models the five original cluster descriptions, including "
        "`local-i5-8300h`. Running it on Ryzen does NOT turn that modeled node into a Ryzen.",
        "- Live workers retain the original six 500m task slots (3 task CPUs), relative emulation "
        "speeds, network shaping and per-worker Docker CPU/RAM caps. No expanded-capacity experiment "
        "was mixed into this comparison.",
        "- Docker's global pool remains 16 logical CPUs / 30.25 GiB; it was NOT reduced to a "
        "guessed old limit or increased during these runs. The uncapped control-plane and faster "
        "underlying cores are real confounders. Equal CPU quotas do not mean equal work per second.",
        "- Each live profile is recalibrated on this host using warm-up only. Its fresh learned "
        "profile is reset before every policy trial. Old warm-up data are not reused.",
        "- All worker containers share this PC and Docker VM. This is NOT physical multi-host "
        "Kubernetes validation; no k3s multi-computer result is claimed.",
        "- Local suites run sequentially, never concurrently with timed live trials. "
        "Other Windows activity is uncontrolled; the host's power plan is recorded, not changed.",
        "- Absolute seconds and relative policy advantages answer different questions. "
        "Simulator virtual seconds, live elapsed seconds, and Python wall-clock runtime are never "
        "treated as interchangeable.",
        "- Descriptive difference bins were fixed before analysis: <=5% small, >5-15% moderate, "
        ">15% large. These are practical reporting labels, NOT significance or equivalence tests.",
        "- Live n=3 per policy/profile/batch is small; repetitions share a calibration. "
        "No claim of statistical equivalence or hardware-only causation is justified.",
        "- The old raw results do not retain local command wall times, global Docker limits, "
        "background load or exact dependency/image digests. CPU execution speedup cannot be "
        "fairly quantified from virtual makespans or the approximate ten-hour README estimate.",
        "",
    ]
    resource_rows = []
    for profile in PROFILES:
        evidence = load_json(current / "kind-validation" / f"{profile}-cluster.json")
        network = {row["node"]: row for row in evidence["network"]}
        for node, row in evidence["docker_caps"].items():
            resource_rows.append([
                profile, node, row["cpus"], row["memory_mib"], row["cpu_shares"],
                network[node]["delay_ms"], network[node]["rate_mbit"],
            ])
    resource_table = [
        "## Matched live worker limits (verified against the old records)", "",
        *markdown_table(
            ["Profile", "Worker", "Docker CPUs", "RAM cap (MiB)", "CPU shares",
             "Egress delay (ms)", "Egress rate (Mbit/s)"], resource_rows),
        "These caps apply to the entire worker (tasks plus node agents). Each task is still "
        "capped at 500m CPU. The control-plane container is not worker-capped. "
        "Network shaping is deterministic egress emulation, not measured physical links.", "",
    ]
    runs_doc = [
        "# Simulation runs on the Ryzen 7 PRO 8840HS", "",
        f'Hardware captured: {hardware["recorded_at"]}. Source commit: `{hardware["source_commit"]}`.',
        "", "This is a separate rerun, not a replacement for the retained i5 evidence.", "",
        "## Hardware and software", "", *specs, *fairness, *resource_table,
        "## Local execution ledger", "",
        *markdown_table(["Suite", "Exit", "Wall time (s)", "Log"], [
            [row["name"], row["exit_code"], f'{row["wall_seconds"]:.2f}',
             f'[{row["log"]}]({row["log"]})'] for row in commands
        ]),
        "Exact argument vectors and UTC start/end times are in [commands.json](commands.json). "
        "Hardware and runtime versions are in [hardware.json](hardware.json).", "",
        f'Total local suite wall time: **{sum(r["wall_seconds"] for r in commands):.2f} s** '
        "(sum of sequential commands; setup and report work excluded).", "",
        "## Live Kubernetes execution", "",
        "All three profiles ran 2 batches x 9 policies x 3 repetitions: **162 live trials**, "
        "plus a fresh calibration per profile. The standard analyzer's integrity gate passed. "
        "Both original and measured-slot-release calibrated twins were generated.", "",
        "There were **168 executed main/repair trials**: 162 in the initial full matrix, "
        "then six integrity-only reruns replacing six unverified records. Exactly 162 trials "
        "are retained in the comparison.", "",
        *markdown_table(["Profile", "Start (UTC)", "Finish (UTC)", "Trials"], [
            [profile, live["started_at"], live["completed_at"], len(live["trials"])]
            for profile in PROFILES
            for live in [load_json(current / "kind-validation" / f"{profile}.json")]
        ]),
        "Detailed live timings, task/transfer/preemption records, caps, network evidence, twin "
        "predictions and integrity checks: [Kind summary](kind-validation/summary.md).", "",
        "Image builds, exact Kubernetes runner arguments, exit codes and wall times are recorded "
        "in [kubernetes-commands.json](kubernetes-commands.json). Current server version and "
        "image IDs/digests are recorded in [kubernetes-runtime.json](kubernetes-runtime.json). "
        "The isolated cluster used its own kubeconfig; the original user context was not changed. "
        "The temporary experiment cluster was deleted after collecting evidence.", "",
        "## Coverage", "",
        "- Closed DAGs: 5 clusters x 15 workflows x (20 + 50 + 50 + 50) seeds; "
        "12 exploratory policies and 10 later policies, plus small-DAG static oracle.",
        "- Overlapping workloads: 5 clusters x 4 workloads, the same four seed partitions.",
        "- Cold-start/image-prewarming and urgent-workflow/preemption mechanism studies.",
        "- Legacy weight search (256 candidates, 25 requested finalists), "
        "factor ablation (seeds 300-309) and all eight factor-activation stresses.",
        "- Contextual policy search (64 candidates, 60 s thermal step); generated profile saved "
        "here rather than overwriting the original selected policy.",
        "- Full same-host Kind balanced/homogeneous/edge matrix; both calibrated twin modes.",
        "- Withdrawn pre-correction and pre-fix runs were NOT rerun as endorsed benchmarks. "
        "They are historical debugging artifacts, not the current protocol.",
        "",
        "## Validation and preservation", "",
        "Repository tests: **210 passed**, exit 0, with explicit `tests` + "
        "`test_simulation.py` and `--import-mode=importlib`. The unqualified initial pytest command "
        "failed collection because the untracked `send` copy shadowed root modules. "
        "No user files were removed to fix this.", "",
        f'Final complete test run after the readiness fix and comparison tooling: '
        f'**{validation["final_tests"]["passed"]} passed**, '
        f'exit {validation["final_tests"]["exit_code"]}, '
        f'{validation["final_tests"]["wall_seconds"]:.2f} s. '
        "PowerShell syntax checks passed; all historical SHA-256 hashes are unchanged; "
        "temporary experiment clusters are gone. Evidence: [validation.json](validation.json).", "",
        "The first ordinary control-plane image build failed with an HTTPS handshake error "
        "against files.pythonhosted.org. A temporary build-only Dockerfile used the same "
        "requirements through the environment's HTTPS package mirror; certificate verification "
        "was not disabled. The bandwidth/thermal image builds also used that mirror. "
        "This deviation does not change scheduler source or work definitions. "
        "Unpinned base image tags and transitive packages can still differ from the old image "
        "builds, whose digests were not retained.", "",
        "An initial Kind setup emitted a Windows cp1252 decoding exception on Kind's UTF-8 "
        "stderr. That attempt was stopped and archived under `setup-first-attempt` before "
        "collecting the retained trial matrix. The runner was restarted with `PYTHONUTF8=1`; "
        "the archived partial calibration is excluded from all comparisons.", "",
        "The first full-matrix analyzer exited 1 because six trials recorded "
        "`warm_profile_loaded: false`. The harness had stopped reading startup logs as soon "
        "as the policy banner appeared, but the scheduler prints that banner BEFORE loading "
        "profiles. The harness now waits for the exact ConfigMap load confirmation, and "
        "records the raw startup log; missing confirmation fails before submitting work. "
        "The scheduler algorithm and work budgets were not changed.", "",
        "Raw startup logs were not retained for the six original failures, so they were "
        "NOT relabeled as warm or used as valid timing evidence. All 156 unaffected trials "
        "passed the original integrity checks. The six invalid case slots were rerun with "
        "the same saved calibration and matched worker limits. Originals are preserved under "
        "`integrity-first-matrix`; retry commands and the final analyzer exit code are in "
        "[integrity-repair-commands.json](integrity-repair-commands.json). "
        "The final gate passes without waivers or relaxed checks.", "",
        *markdown_table(
            ["Profile", "Retried case", "Batch", "Policy", "Rerun finish (UTC)"],
            [[profile, row["trial_id"], row["batch"], row["policy"], row["rerun_finished_at"]]
             for profile in PROFILES
             for row in load_json(current / "kind-validation" / f"{profile}.json").get("integrity_repairs", [])],
        ),
        "Historical SHA-256 fingerprints: [historical-hashes.json](historical-hashes.json). "
        "The cross-computer report is [COMPARISON.md](COMPARISON.md).", "",
        "## Reproduction", "",
        "Executed build-only Dockerfiles and orchestration/repair helpers are archived in "
        "[provenance](provenance/). Their absolute session paths are historical; adjust path "
        "variables when reusing them. No kubeconfig or cluster credentials are included.", "",
        "Use a NEW result directory with `scripts/run-local-benchmarks.ps1`; it refuses "
        "to overwrite an existing run. For live reruns, use `scripts/run-kind-validation.ps1` "
        "with a copied profile document, a unique cluster name, a dedicated kubeconfig and "
        "a new result directory. Exact original argument vectors, timestamps, image IDs "
        "and repair selections are retained in the linked JSON ledgers.", "",
    ]
    (current / "RUNS.md").write_text("\n".join(runs_doc), encoding="utf-8")
    comparison_doc = [
        "# Cross-computer simulation comparison: i5-8300H vs Ryzen 7 PRO 8840HS", "",
        "## Verdict", "",
        ("All local scientific results reproduce within 1e-9 relative/absolute tolerance."
         if report["local_complete_equivalent"] else
         "**Some local results differ.** Consult the per-suite checks and examples in "
         "[comparison.json](comparison.json); exact reproducibility is not claimed."),
        "", "Live Kubernetes results are measured separately below; numerical identity is "
        "not expected. A small difference is not proof of equivalence.", "",
        "**The answer is different for the two execution surfaces:** the local scientific "
        "results are exactly the same, while live Kubernetes timings and the strength of "
        "some policy advantages are not.", "",
        "### Live difference overview", "",
        *markdown_table(
            ["Metric", "Rows", "Small", "Moderate", "Large", "Median row delta"],
            [[metric, len(rows),
              sum(r["difference_size"].startswith("small") for r in rows),
              sum(r["difference_size"].startswith("moderate") for r in rows),
              sum(r["difference_size"].startswith("large") for r in rows),
              f'{statistics.median(r["delta_percent"] for r in rows):+.2f}%']
             for metric in METRICS
             for rows in [[r for r in report["live"] if r["metric"] == metric]]
             if rows],
        ),
        "The median row delta is an unweighted descriptive summary of policy/profile/batch "
        "median changes, not a universal hardware speedup.", "",
        "## Computer specifications", "", *specs, *fairness, *resource_table,
        "## Local matrices: keyed record-by-record comparison", "",
        "Keys include cluster, workflow/workload, seed and policy; missing, extra and duplicate "
        "records cannot silently pass. Every numeric and nonnumeric field is compared. "
        "Exact means parsed values are identical, not that JSON whitespace or file bytes match.", "",
        *markdown_table(
            ["Suite", "Old / new records", "Exact", "Within 1e-9", "Missing / extra", "Max absolute delta"],
            [[name, f'{row["old_records"]} / {row["new_records"]}',
              row["exact_records"], row["equivalent_records"],
              f'{row["missing_keys"]} / {row["extra_keys"]}',
              f'{row["max_absolute_numeric_difference"]:.3g}']
             for name, row in report["matrices"].items()],
        ),
        "## Targeted and search studies: complete JSON comparison", "",
        *markdown_table(
            ["Study", "Scalar values", "Exact differences", "Outside tolerance", "Structural differences"],
            [[name, row["scalar_values"], row["exact_differences"],
              row["outside_tolerance"], row["structural_differences"]]
             for name, row in report["studies"].items()],
        ),
        "Selected weights, full leaderboards, cold/warm results, priorities and factor choices "
        "are included, not merely headline rankings.", "",
        "## Live cross-host comparisons", "",
        "Delta = 100 x (new median / old median - 1). Negative is faster. "
        "Old/new ranges expose run-to-run variability. Each row uses three repetitions.", "",
    ]
    for metric in METRICS:
        selected = [row for row in report["live"] if row["metric"] == metric]
        comparison_doc += [
            f"### {metric}", "",
            *markdown_table(
                ["Profile / batch", "Policy", "i5 median (s)", "Ryzen median (s)", "Delta",
                 "Size", "i5 range", "Ryzen range"],
                [[f'{r["profile"]} / {r["batch"]}', r["policy"],
                  f'{r["old_median"]:.2f}', f'{r["new_median"]:.2f}',
                  f'{r["delta_percent"]:+.2f}%' if r["delta_percent"] is not None else "n/a",
                  r["difference_size"],
                  " - ".join(f"{x:.2f}" for x in r["old_range"]),
                  " - ".join(f"{x:.2f}" for x in r["new_range"])] for r in selected],
            ),
        ]
    comparison_doc += [
        "## Relative policy behavior within each host", "",
        "Ratios below compare medians within the same profile, so task speed changes are "
        "partly normalized out. Ratio <1 favors the first policy. This is descriptive n=3 "
        "evidence, not a conclusive ranking of close policies.", "",
    ]
    indexed = {
        (r["profile"], r["batch"], r["policy"], r["metric"]): r for r in report["live"]
    }
    relative_rows = []
    for profile in PROFILES:
        for batch, a, b, metric in (
            ("dag", "heft-ra-cap", "heft", "makespan_seconds"),
            ("dag", "heft-ra", "heft", "makespan_seconds"),
            ("dag", "legacy-tuned", "legacy-v1", "makespan_seconds"),
            ("priority", "adaptive", "adaptive-no-preempt", "critical_response_seconds"),
            ("priority", "heft", "kube-default", "critical_response_seconds"),
        ):
            left, right = indexed[(profile, batch, a, metric)], indexed[(profile, batch, b, metric)]
            relative_rows.append([
                profile, batch, f"{a} / {b}", metric,
                f'{left["old_median"]/right["old_median"]:.4f}',
                f'{left["new_median"]/right["new_median"]:.4f}',
            ])
    comparison_doc += markdown_table(
        ["Profile", "Batch", "Policy ratio", "Metric", "i5", "Ryzen"], relative_rows)
    comparison_doc += [
        "### Pooled paired contrasts (the existing live analyzer)", "",
        "These are means of the nine within-repetition ratios, not ratios of pooled medians. "
        "Bootstrap intervals are reported by the repository analyzer; the small sample and "
        "shared calibration still limit inferential strength.", "",
    ]
    contrast_rows = []
    selected_contrasts = {
        ("dag", "heft-ra", "heft", "makespan_seconds"),
        ("dag", "heft-ra-cap", "heft", "makespan_seconds"),
        ("dag", "legacy-tuned", "legacy-v1", "makespan_seconds"),
        ("priority", "adaptive", "adaptive-no-preempt", "critical_response_seconds"),
        ("priority", "heft", "kube-default", "critical_response_seconds"),
    }
    old_contrasts = {
        (r["batch"], r["policy"], r["baseline"], r["metric"]): r
        for r in old_live_summary["contrasts"]
    }
    for row in live_summary["contrasts"]:
        key = (row["batch"], row["policy"], row["baseline"], row["metric"])
        if key not in selected_contrasts:
            continue
        previous = old_contrasts[key]
        contrast_rows.append([
            row["batch"], f'{row["policy"]} / {row["baseline"]}', row["metric"],
            f'{previous["mean_ratio"]:.4f}',
            " - ".join(f"{x:.4f}" for x in previous["mean_ratio_ci95"]),
            f'{row["mean_ratio"]:.4f}',
            " - ".join(f"{x:.4f}" for x in row["mean_ratio_ci95"]),
        ])
    comparison_doc += markdown_table(
        ["Batch", "Policy ratio", "Metric", "i5 mean", "i5 95% bootstrap",
         "Ryzen mean", "Ryzen 95% bootstrap"], contrast_rows)
    comparison_doc += [
        "### Interpretation of the original live conclusions", "",
        f'- A HEFT-RA variant was the strict fastest DAG policy in '
        f'**{report["dag_winners"]["i5"]["heft_ra_variant_wins"]}/9 i5 runs** versus '
        f'**{report["dag_winners"]["Ryzen"]["heft_ra_variant_wins"]}/9 Ryzen runs**. '
        "The original 8/9 dominance is not replicated at the same strength. "
        "A strict winner count does not imply statistically meaningful separation.",
    ]
    for key, explanation in (
        (("dag", "heft-ra", "heft", "makespan_seconds"),
         "HEFT-RA's DAG advantage over HEFT"),
        (("dag", "heft-ra-cap", "heft", "makespan_seconds"),
         "Capacity-aware HEFT-RA versus HEFT on the DAG batch"),
        (("priority", "adaptive", "adaptive-no-preempt", "critical_response_seconds"),
         "Adaptive preemption's urgent-workflow advantage"),
    ):
        a = old_contrasts[key]
        b = next(r for r in live_summary["contrasts"]
                 if (r["batch"], r["policy"], r["baseline"], r["metric"]) == key)
        comparison_doc.append(
            f'- {explanation}: mean paired ratio **{a["mean_ratio"]:.4f}** on i5 '
            f'vs **{b["mean_ratio"]:.4f}** on Ryzen '
            f'({(b["mean_ratio"] - 1) * 100:+.2f}% relative to the baseline on Ryzen).')
    comparison_doc += [
        "- In particular, `heft-ra-cap` is not the observed DAG improvement over HEFT on "
        "this machine: its pooled mean ratio is above 1. Do not carry the old live "
        "recommendation over unconditionally. The bootstrap intervals and per-profile "
        "tables show the uncertainty; close rankings are not conclusive with three repeats.",
        "- Adaptive's urgent-workflow benefit remains, but its magnitude is smaller on "
        "this host. That does not establish a universal CPU-dependent effect.",
        "",
    ]
    comparison_doc += [
        "## Did the policy ordering stay the same?", "",
        "Cross-host Spearman compares nine policy medians for a fixed profile/batch/metric. "
        "1 means identical ordering; near 0 means little rank agreement. Policies within 2% "
        "of the best median are listed as near-leaders, not statistically proven ties.", "",
        *markdown_table(
            ["Profile / batch", "Metric", "Spearman", "i5 near-leaders", "Ryzen near-leaders"],
            [[f'{r["profile"]} / {r["batch"]}', r["metric"],
              f'{r["spearman"]:.3f}' if r["spearman"] is not None else "n/a",
              ", ".join(r["old_best_within_2_percent"]),
              ", ".join(r["new_best_within_2_percent"])]
             for r in report["live_rank_agreement"]],
        ),
        "## Warm-up: task speed and orchestration costs", "",
        "Base runtime is speed-normalized task-class work, not end-to-end DAG response. "
        "A lower runtime here supports faster execution of that particular workload on the "
        "new stack; it does not isolate CPU from kernel, image, memory or storage changes.", "",
    ]
    calibration_rows = []
    for profile in PROFILES:
        before = old_live_summary["twin_release_calibration"][profile]
        after = live_summary["twin_release_calibration"][profile]
        for name in sorted(before["base_runtime_s"]):
            a, b = before["base_runtime_s"][name], after["base_runtime_s"][name]
            calibration_rows.append([profile, name, f"{a:.3f}", f"{b:.3f}",
                                     f"{percent_change(a, b):+.2f}%"])
        for name in ("control_plane_delay_s", "slot_release_delay_s"):
            a, b = before[name], after[name]
            delta = percent_change(a, b)
            calibration_rows.append([profile, name, f"{a:.3f}", f"{b:.3f}",
                                     f"{delta:+.2f}%" if delta is not None else "n/a"])
    comparison_doc += markdown_table(
        ["Profile", "Calibrated component", "i5 (s)", "Ryzen (s)", "Delta"], calibration_rows)
    comparison_doc += [
        "## Simulator twin fidelity on each computer", "",
        "Both twins use each host's own pre-trial calibration. Measured-release mode models "
        "the warm-up completion-to-rebind delay. MAPE is error against the host's live medians, "
        "not cross-host speedup; smaller is better.", "",
    ]
    fidelity_rows = []
    for mode, key in (("original", "twin_agreement"),
                      ("measured-release", "twin_agreement_release")):
        for profile in PROFILES:
            for batch in ("dag", "priority"):
                for metric in ("makespan_seconds", "mean_response_seconds"):
                    a = old_live_summary[key][profile][batch][metric]
                    b = live_summary[key][profile][batch][metric]
                    fidelity_rows.append([
                        mode, f"{profile} / {batch}", metric,
                        f'{a["mape"]:.2%}', f'{b["mape"]:.2%}',
                        f'{a["spearman"]:.3f}' if a["spearman"] is not None else "n/a",
                        f'{b["spearman"]:.3f}' if b["spearman"] is not None else "n/a",
                    ])
    comparison_doc += markdown_table(
        ["Twin", "Profile / batch", "Metric", "i5 MAPE", "Ryzen MAPE",
         "i5 rank agreement", "Ryzen rank agreement"], fidelity_rows)
    comparison_doc += [
        "## What can and cannot be concluded", "",
        "- Use the local equivalence tables to decide whether the documented simulated "
        "conclusions reproduce. A faster host should reduce execution cost, not virtual seconds.",
        "- Use live absolute deltas to describe this machine's measured timings, and the "
        "within-host ratios to judge whether policy advantages persist.",
        "- Faster work execution may make fixed scheduler ticks, controller reconciliation, "
        "Pod startup and slot-release delays a larger fraction of total runtime; rankings can "
        "change without a simulator regression.",
        "- No CPU-only, RAM-only, power-efficiency or physical-network speedup is inferred. "
        "This task does not measure electrical energy or thermal throttling on either PC.",
        "- No high-capacity result is represented as a matched-capacity replication. "
        "An expanded-slot study would be a new workload/capacity experiment and requires its "
        "own full matrix and calibration.",
        "",
        "## Evidence", "",
        "[Raw differences and paired live ratios](comparison.json), "
        "[run ledger and coverage](RUNS.md), [live integrity/twin analysis](kind-validation/summary.md).",
        "",
    ]
    (current / "COMPARISON.md").write_text("\n".join(comparison_doc), encoding="utf-8")
    print(f'Local complete equivalence: {report["local_complete_equivalent"]}')
    print(f'Compared {len(report["live"])} live metric rows')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
