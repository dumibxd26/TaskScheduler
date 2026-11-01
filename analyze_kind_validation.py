"""Integrity gate, aggregation, and twin comparison for the Kind validation.

For every profile in ``results/kind-validation`` this reads the live result
(``<profile>.json``), the cluster evidence written by ``kind_lab.py create``
(``<profile>-cluster.json``), and the twin predictions
(``<profile>-twin.json``). Any integrity violation aborts the analysis: a
summary is only produced from complete, verified evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

import yaml

from kind_lab import load_document, parse_qdisc, workers as profile_workers

ROOT = Path(__file__).parent
DEFAULT_PROFILES = ("balanced", "homogeneous", "edge")
SPEED_KEYS = {"CPU_BOUND": "cpu", "MEMORY_BOUND": "memory", "IO_BOUND": "io"}
METRICS = ("makespan_seconds", "mean_response_seconds", "p95_response_seconds",
           "critical_response_seconds")
CONTRASTS = (
    ("dag", "heft", "kube-default"),
    ("dag", "heft-ra", "heft"),
    ("dag", "heft-ra-cap", "heft"),
    ("dag", "heft-ra-cap", "heft-ra"),
    ("dag", "heft", "eft"),
    ("dag", "legacy-tuned", "legacy-v1"),
    ("dag", "adaptive", "heft"),
    ("priority", "adaptive", "adaptive-no-preempt"),
    ("priority", "heft", "kube-default"),
    ("priority", "heft-ra", "heft"),
    ("priority", "heft-ra-cap", "heft"),
    ("priority", "heft-ra-cap", "heft-ra"),
    ("priority", "legacy-tuned", "legacy-v1"),
)


class IntegrityError(ValueError):
    pass


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def spearman(xs, ys):
    """Spearman rank correlation with average ranks for ties."""
    def ranks(values):
        order = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while (end + 1 < len(order)
                   and values[order[end + 1]] == values[order[position]]):
                end += 1
            average = (position + end) / 2 + 1
            for index in order[position:end + 1]:
                result[index] = average
            position = end + 1
        return result

    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mean_x, mean_y = statistics.fmean(rx), statistics.fmean(ry)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    denominator = math.sqrt(
        sum((a - mean_x) ** 2 for a in rx) * sum((b - mean_y) ** 2 for b in ry))
    return numerator / denominator if denominator else None


def bootstrap_mean_ci(values, seed=2026, samples=4000):
    if not values:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(samples))
    return [percentile(means, 0.025), percentile(means, 0.975)]


def critical_response(trial):
    for workflow in trial["workflows"]:
        if workflow.get("shape") == "critical":
            return workflow["response_seconds"]
    return None


def validate_cluster(profile, evidence, live, document):
    _check(evidence.get("profile") == profile,
           f"{profile}: cluster evidence describes {evidence.get('profile')!r}")
    expected = {worker.name: worker for worker in profile_workers(document, profile)}
    records = {record["node"]: record for record in evidence.get("network", [])}
    _check(set(records) == set(expected),
           f"{profile}: shaped workers {sorted(records)} != {sorted(expected)}")
    for name, worker in expected.items():
        parsed = parse_qdisc(records[name]["observed_qdisc"])
        _check(parsed["rate_mbit"] is not None
               and math.isclose(parsed["rate_mbit"], worker.rate_mbit, rel_tol=1e-6)
               and math.isclose(parsed["delay_ms"], worker.delay_ms, abs_tol=1e-6),
               f"{profile}/{name}: observed qdisc {records[name]['observed_qdisc']!r} "
               f"does not match {worker.delay_ms} ms / {worker.rate_mbit} Mbit")
        _check(evidence["speeds"].get(name) == dict(worker.speed),
               f"{profile}/{name}: published speeds differ from the profile")
    nodes = {node["name"]: node for node in live["cluster"]["nodes"]}
    _check(set(nodes) == set(expected),
           f"{profile}: live cluster nodes {sorted(nodes)} != profile workers")
    for name, worker in expected.items():
        node = nodes[name]
        _check(math.isclose(float(node["netem_delay_ms"]), worker.delay_ms)
               and math.isclose(float(node["netem_rate_mbit"]), worker.rate_mbit),
               f"{profile}/{name}: node annotations disagree with the profile")
        _check(float(node["capacity_cpu"]) == worker.capacity_cpu,
               f"{profile}/{name}: capacity label differs from the profile")
    return expected


def validate_task(profile, label, task, expected_workers, templates):
    _check(task.get("state") == "FINISHED" and task.get("pod_phase") == "Succeeded",
           f"{label}/{task.get('name')}: task did not succeed")
    _check(task.get("restart_count") == 0,
           f"{label}/{task.get('name')}: container restarted")
    _check(not task.get("collection_error"),
           f"{label}/{task.get('name')}: collection error {task.get('collection_error')}")
    metrics = task.get("metrics") or {}
    _check(isinstance(metrics.get("runtime_seconds"), (int, float)),
           f"{label}/{task.get('name')}: missing runtime metrics")
    _check(metrics.get("node_name") == task.get("node_name"),
           f"{label}/{task.get('name')}: ran on {metrics.get('node_name')!r} but "
           f"status says {task.get('node_name')!r}")
    template = templates.get(task.get("template"))
    _check(template is not None, f"{label}/{task.get('name')}: unknown template")
    kind = SPEED_KEYS[template["spec"]["taskClass"]]
    worker = expected_workers[task["node_name"]]
    factor = (metrics.get("emulation") or {}).get("speed_factor")
    _check(factor is not None and math.isclose(float(factor), worker.speed[kind]),
           f"{label}/{task.get('name')}: emulated {kind} speed {factor} != "
           f"{worker.speed[kind]} for {worker.name}")


def validate_workflows(profile, label, workflows, definition, expected_workers,
                       templates):
    expected_shapes = {item["name"]: item for item in definition["workflows"]}
    shapes = {workflow.get("shape"): workflow for workflow in workflows}
    _check(set(shapes) == set(expected_shapes),
           f"{label}: workflows {sorted(shapes)} != {sorted(expected_shapes)}")
    for shape, workflow in shapes.items():
        _check(workflow.get("state") == "FINISHED",
               f"{label}/{shape}: workflow ended {workflow.get('state')}")
        _check(workflow.get("response_seconds") is not None,
               f"{label}/{shape}: missing response time")
        spec_tasks = {task["name"]: task for task in expected_shapes[shape]["tasks"]}
        tasks = {task["name"]: task for task in workflow["tasks"]}
        _check(set(tasks) == set(spec_tasks),
               f"{label}/{shape}: tasks {sorted(tasks)} != {sorted(spec_tasks)}")
        for name, task in tasks.items():
            _check(task.get("template") == spec_tasks[name]["template"],
                   f"{label}/{shape}/{name}: template mismatch")
            validate_task(profile, f"{label}/{shape}", task, expected_workers, templates)
        for name, spec in spec_tasks.items():
            for dependency in spec.get("dependsOn", []):
                if "generated_file_path" not in dependency.get("dataFields", []):
                    continue
                producer = tasks[dependency["parent"]]
                consumer = tasks[name]
                produced = (producer["metrics"].get("output_bytes_by_field") or {}).get(
                    "generated_file_path")
                consumed = consumer["metrics"].get("io_bytes_read")
                _check(produced and consumed == produced,
                       f"{label}/{shape}/{name}: read {consumed} of {produced} bytes")
                co_located = producer["node_name"] == consumer["node_name"]
                _check(consumer.get("transfer_mode") == (
                           "local" if co_located else "remote"),
                       f"{label}/{shape}/{name}: transfer mode "
                       f"{consumer.get('transfer_mode')} with co_located={co_located}")


def validate_profile(profile, live, evidence, document, templates):
    _check(live.get("schema_version") == 2, f"{profile}: unexpected schema")
    _check(live.get("profile") == profile, f"{profile}: wrong profile in result")
    _check(live.get("completed_at") and not live.get("error"),
           f"{profile}: run incomplete or failed: {live.get('error')}")
    expected_workers = validate_cluster(profile, evidence, live, document)
    calibration = live["calibration"]
    profiles = json.loads(calibration["profiles_json"])["profiles"]
    for template in live["templates"]:
        _check(template in profiles, f"{profile}: no learned profile for {template}")
        nodes = profiles[template]["nodes"]
        for worker in expected_workers:
            count = len(nodes.get(worker, {}).get("observations", []))
            _check(count >= 3,
                   f"{profile}: calibration has {count} samples for {template}/{worker}")
    for workflow in calibration["workflows"]:
        _check(workflow.get("state") == "FINISHED",
               f"{profile}: calibration workflow {workflow['name']} failed")
        for task in workflow["tasks"]:
            validate_task(profile, f"{profile}/calibration", task,
                          expected_workers, templates)
    scheduled = {tuple(item) for item in live["schedule"]}
    seen = set()
    for trial in live["trials"]:
        key = (trial["repetition"], trial["batch"], trial["policy"])
        _check(key not in seen, f"{profile}: duplicate trial {key}")
        seen.add(key)
        label = f"{profile}/{trial['trial_id']}/{trial['batch']}/{trial['policy']}"
        _check(trial["scheduler"]["policy"] == trial["policy"],
               f"{label}: scheduler ran {trial['scheduler']['policy']}")
        _check(trial["scheduler"].get("warm_profile_loaded") is True,
               f"{label}: warm profile was not loaded")
        events = trial["scheduler_events"]
        _check(not events["error_lines"], f"{label}: scheduler errors {events['error_lines'][:2]}")
        tick_errors = [line for line in events.get("scheduler_errors", [])
                       if "tick error" in line or "Traceback" in line]
        _check(not tick_errors, f"{label}: scheduler tick errors {tick_errors[:2]}")
        definition = live["batch_definitions"][trial["batch"]]
        validate_workflows(profile, label, trial["workflows"], definition,
                           expected_workers, templates)
        task_count = sum(len(item["tasks"]) for item in definition["workflows"])
        _check(len(events["binds"]) == task_count + len(events["preemptions"]),
               f"{label}: {len(events['binds'])} binds for {task_count} tasks and "
               f"{len(events['preemptions'])} preemptions")
        if trial["policy"] != "adaptive":
            _check(not events["preemptions"], f"{label}: unexpected preemptions")
        for event in events["preemptions"]:
            _check("-pressure-" in event["pod"],
                   f"{label}: preempted non-pressure Pod {event['pod']}")
    _check(seen == scheduled, f"{profile}: missing trials {sorted(scheduled - seen)}")
    return expected_workers


def trial_metrics(trial):
    remote = local = 0
    for workflow in trial["workflows"]:
        for task in workflow["tasks"]:
            if task.get("transfer_mode") == "remote":
                remote += 1
            elif task.get("transfer_mode") == "local":
                local += 1
    return {
        "makespan_seconds": trial["makespan_seconds"],
        "mean_response_seconds": trial["mean_response_seconds"],
        "p95_response_seconds": trial["p95_response_seconds"],
        "critical_response_seconds": critical_response(trial),
        "preemptions": trial["preemptions"],
        "remote_transfers": remote,
        "local_reads": local,
    }


def summarize(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return {"n": len(values), "median": statistics.median(values),
            "mean": statistics.fmean(values), "min": min(values), "max": max(values)}


def analyze(results_dir: Path, profiles):
    document = load_document()
    templates = {
        item["metadata"]["name"]: item
        for item in yaml.safe_load_all(
            (ROOT / "experiments" / "k8s" / "tasktemplates.yaml").read_text(
                encoding="utf-8"))
        if item
    }
    cells = defaultdict(list)
    twins = {}
    pairs = defaultdict(dict)
    for profile in profiles:
        live = json.loads((results_dir / f"{profile}.json").read_text(encoding="utf-8"))
        evidence = json.loads(
            (results_dir / f"{profile}-cluster.json").read_text(encoding="utf-8"))
        validate_profile(profile, live, evidence, document, templates)
        twins[profile] = json.loads(
            (results_dir / f"{profile}-twin.json").read_text(encoding="utf-8"))
        for trial in live["trials"]:
            metrics = trial_metrics(trial)
            cells[(profile, trial["batch"], trial["policy"])].append(metrics)
            pairs[(profile, trial["batch"], trial["repetition"])][trial["policy"]] = metrics

    by_profile = {}
    for (profile, batch, policy), rows in sorted(cells.items()):
        by_profile.setdefault(profile, {}).setdefault(batch, {})[policy] = {
            metric: summarize([row[metric] for row in rows])
            for metric in (*METRICS, "preemptions", "remote_transfers", "local_reads")
        }
    pooled = {}
    for (profile, batch, policy), rows in cells.items():
        pooled.setdefault(batch, {}).setdefault(policy, []).extend(rows)
    pooled_summary = {
        batch: {
            policy: {
                metric: summarize([row[metric] for row in rows])
                for metric in (*METRICS, "preemptions", "remote_transfers", "local_reads")
            }
            for policy, rows in sorted(policies.items())
        }
        for batch, policies in pooled.items()
    }

    contrasts = []
    for batch, policy, baseline in CONTRASTS:
        for metric in METRICS:
            ratios = [
                rows[policy][metric] / rows[baseline][metric]
                for (profile, row_batch, _), rows in pairs.items()
                if row_batch == batch and policy in rows and baseline in rows
                and rows[policy][metric] and rows[baseline][metric]
            ]
            if not ratios:
                continue
            contrasts.append({
                "batch": batch, "policy": policy, "baseline": baseline,
                "metric": metric, "pairs": len(ratios),
                "mean_ratio": statistics.fmean(ratios),
                "mean_ratio_ci95": bootstrap_mean_ci(ratios),
                "wins": sum(ratio < 0.99 for ratio in ratios),
                "losses": sum(ratio > 1.01 for ratio in ratios),
            })

    agreement = twin_agreement(by_profile, twins)
    release_twins = {
        profile: json.loads(path.read_text(encoding="utf-8"))
        for profile in profiles
        for path in [results_dir / f"{profile}-twin-release.json"]
        if path.exists()
    }
    release_agreement = (
        twin_agreement(by_profile, release_twins)
        if set(release_twins) == set(profiles) else None)

    return {
        "schema_version": 1,
        "profiles": list(profiles),
        "integrity": "passed",
        "by_profile": by_profile,
        "pooled": pooled_summary,
        "contrasts": contrasts,
        "twin_agreement": agreement,
        "twin_agreement_release": release_agreement,
        "twin_calibration": {
            profile: twin["calibration"] for profile, twin in twins.items()},
        "twin_release_calibration": {
            profile: twin["calibration"] for profile, twin in release_twins.items()},
        "evidence_scope": (
            "same-host Kind worker containers with emulated speeds, slot "
            "capacities, and tc-netem links; descriptive, three repetitions "
            "per profile, batch, and policy"),
    }


def twin_agreement(by_profile, twins):
    """Rank and error agreement between live medians and twin predictions."""
    agreement = {}
    for profile, batches in by_profile.items():
        for batch, policies in batches.items():
            names = sorted(
                name for name in policies
                if name in twins[profile]["predictions"].get(batch, {}))
            row = {}
            for metric in ("makespan_seconds", "mean_response_seconds"):
                live_values = [policies[name][metric]["median"] for name in names]
                twin_values = [
                    twins[profile]["predictions"][batch][name]["summary"][metric]
                    for name in names]
                errors = [abs(t - l) / l for t, l in zip(twin_values, live_values) if l]
                live_best = min(live_values)
                twin_best = min(twin_values)
                row[metric] = {
                    "spearman": spearman(live_values, twin_values),
                    "mape": statistics.fmean(errors) if errors else None,
                    "bias": (statistics.fmean(
                        (t - l) / l for t, l in zip(twin_values, live_values) if l)
                        if live_values else None),
                    "live_best": [n for n, v in zip(names, live_values)
                                  if v <= live_best * 1.02],
                    "twin_best": [n for n, v in zip(names, twin_values)
                                  if v <= twin_best * 1.02],
                    "live": dict(zip(names, live_values)),
                    "twin": dict(zip(names, twin_values)),
                }
            agreement.setdefault(profile, {})[batch] = row
    return agreement


def _agreement_table(lines, title, agreement):
    lines += [f"## {title}", "",
              "| Profile | Batch | Metric | Spearman | MAPE | Mean bias | Live best | Twin best |",
              "|---|---|---|---:|---:|---:|---|---|"]
    for profile, batches in agreement.items():
        for batch, metrics in batches.items():
            for metric, row in metrics.items():
                rho = "n/a" if row["spearman"] is None else f"{row['spearman']:.2f}"
                lines.append(
                    f"| {profile} | {batch} | {metric} | {rho} | {row['mape']:.1%} | "
                    f"{row['bias']:+.1%} | {', '.join(row['live_best'])} | "
                    f"{', '.join(row['twin_best'])} |")
    lines.append("")


def markdown(summary):
    lines = ["# Kind validation summary", "",
             f"Integrity: {summary['integrity']}. {summary['evidence_scope']}.", ""]
    for batch, policies in summary["pooled"].items():
        lines += [f"## Pooled `{batch}` batch (median over trials)", "",
                  "| Policy | Trials | Makespan (s) | Mean response (s) | P95 response (s) | Critical (s) | Preemptions | Remote reads |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for policy, metrics in sorted(
                policies.items(), key=lambda item: item[1]["makespan_seconds"]["median"]):
            critical = metrics["critical_response_seconds"]
            preemptions = metrics["preemptions"]
            lines.append(
                f"| {policy} | {metrics['makespan_seconds']['n']} | "
                f"{metrics['makespan_seconds']['median']:.2f} | "
                f"{metrics['mean_response_seconds']['median']:.2f} | "
                f"{metrics['p95_response_seconds']['median']:.2f} | "
                + (f"{critical['median']:.2f}" if critical else "n/a")
                + f" | {preemptions['mean'] * preemptions['n']:.0f}"
                + f" | {metrics['remote_transfers']['median']:.1f} |")
        lines.append("")
    lines += ["## Per-profile medians", ""]
    for profile, batches in summary["by_profile"].items():
        for batch, policies in batches.items():
            lines += [f"### {profile} / {batch}", "",
                      "| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |",
                      "|---|---:|---:|---:|---:|"]
            agreement = summary["twin_agreement"][profile][batch]
            for policy in sorted(policies, key=lambda name: policies[name][
                    "makespan_seconds"]["median"]):
                lines.append(
                    f"| {policy} | {policies[policy]['makespan_seconds']['median']:.2f} | "
                    f"{policies[policy]['mean_response_seconds']['median']:.2f} | "
                    f"{agreement['makespan_seconds']['twin'][policy]:.2f} | "
                    f"{agreement['mean_response_seconds']['twin'][policy]:.2f} |")
            for metric in ("makespan_seconds", "mean_response_seconds"):
                row = agreement[metric]
                rho = "n/a" if row["spearman"] is None else f"{row['spearman']:.2f}"
                lines.append(
                    f"\n{metric}: Spearman {rho}, MAPE {row['mape']:.1%}, "
                    f"live best {', '.join(row['live_best'])}, "
                    f"twin best {', '.join(row['twin_best'])}.")
            lines.append("")
    _agreement_table(lines, "Twin agreement (original twin)", summary["twin_agreement"])
    if summary.get("twin_agreement_release"):
        _agreement_table(
            lines, "Twin agreement (twin with measured slot-release latency)",
            summary["twin_agreement_release"])
    lines += ["## Paired contrasts (ratio policy / baseline, matched trials)", "",
              "| Batch | Policy vs baseline | Metric | Pairs | Mean ratio | 95% bootstrap CI | Wins / losses |",
              "|---|---|---|---:|---:|---|---:|"]
    for row in summary["contrasts"]:
        low, high = row["mean_ratio_ci95"]
        lines.append(
            f"| {row['batch']} | {row['policy']} vs {row['baseline']} | {row['metric']} | "
            f"{row['pairs']} | {row['mean_ratio']:.3f} | {low:.3f}-{high:.3f} | "
            f"{row['wins']} / {row['losses']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path,
                        default=ROOT / "results" / "kind-validation")
    parser.add_argument("--profiles", nargs="+", default=list(DEFAULT_PROFILES))
    args = parser.parse_args()
    summary = analyze(args.results_dir, args.profiles)
    (args.results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.results_dir / "summary.md").write_text(markdown(summary), encoding="utf-8")
    print(markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
