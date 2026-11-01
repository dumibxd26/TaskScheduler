"""Predict live Kind trial outcomes with calibrated simulator twins.

Reads a live result document written by ``run_k8s_experiment.py``, derives
the twin calibration *only from its warm-up phase* (never from trials), and
simulates every live policy on every batch. Writes ``<profile>-twin.json``.

Calibration rules:

* base work of a template = median over warm-up tasks of
  (runtime - input staging) x emulated speed of the node that ran it;
* container startup = median of (task start - binding) per template;
* orchestration delay = mean controller latency from workflow admission to
  Pod creation, plus the median scheduler dispatch latency of the first wave
  of bindings (when capacity was free);
* published file size = median bytes written by each I/O template;
* node egress = median rate that peers measured downloading from the node
  (ts-bw-probe annotations), falling back to the nominal netem rate.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from run_k8s_experiment import epoch, percentile
from simulation.engine import Simulator
from simulation.kind_twin import (
    Calibration,
    load_batches,
    load_profiles,
    twin_cluster,
    twin_workload,
)
from simulation.policies import create_policy
from simulation.workloads import submission_results

ROOT = Path(__file__).parent
LIVE_TO_SIM = {
    "kube-default": "k8s-default",
    "legacy-v1": "legacy-v1",
    "legacy-tuned": "legacy-tuned",
    "eft": "eft",
    "heft": "heft",
    "heft-ra": "heft-ra",
    "heft-ra-cap": "heft-ra-cap",
    "adaptive-no-preempt": "adaptive-no-preempt",
    "adaptive": "adaptive",
}
MIB = 1024 * 1024


def slot_handover_latencies(calibration: dict) -> list[float]:
    """Seconds from a task finishing on a node to the next bind there.

    Every warm-up task is pending from the start, so each bind after the
    first wave reuses a slot that just became free: the gap measures how long
    the control plane takes to notice a completion and place the next Pod.
    """
    finishes = defaultdict(list)
    pod_node = {}
    for workflow in calibration["workflows"]:
        for task in workflow["tasks"]:
            finished = (task.get("metrics") or {}).get("finished_at_epoch")
            if finished and task.get("node_name"):
                finishes[task["node_name"]].append(float(finished))
            pod_node[task.get("pod_name")] = task.get("node_name")
    latencies = []
    for event in calibration["scheduler_events"]["binds"]:
        node = event.get("node") or pod_node.get(event.get("pod"))
        at = event.get("at")
        if not node or not at:
            continue
        prior = [finished for finished in finishes.get(node, []) if finished <= at]
        if prior:
            latencies.append(at - max(prior))
    return latencies


def derive_calibration(document: dict, *,
                       slot_release: bool = False) -> tuple[Calibration, dict]:
    calibration = document["calibration"]
    binds = {event["pod"]: event["at"]
             for event in calibration["scheduler_events"]["binds"] if event["at"]}
    work = defaultdict(list)
    startup = defaultdict(list)
    outputs = defaultdict(list)
    controller = []
    created_and_bound = []
    for workflow in calibration["workflows"]:
        admitted = epoch(workflow["admitted_at"])
        for task in workflow["tasks"]:
            metrics = task.get("metrics") or {}
            template = task.get("template")
            speed = (metrics.get("emulation") or {}).get("speed_factor")
            runtime = metrics.get("runtime_seconds")
            if not template or not speed or runtime is None:
                continue
            staging = float(metrics.get("transfer_seconds", 0.0))
            work[template].append((float(runtime) - staging) * float(speed))
            created = epoch(task.get("pod_created_at"))
            bound = binds.get(task.get("pod_name"))
            started = metrics.get("started_at_epoch")
            if bound is not None and started is not None:
                startup[template].append(float(started) - bound)
            if created is not None and admitted is not None:
                controller.append(created - admitted)
            if created is not None and bound is not None:
                created_and_bound.append((bound, bound - created))
            written = (metrics.get("output_bytes_by_field") or {}).get(
                "generated_file_path")
            if written:
                outputs[template].append(float(written) / MIB)
    total_slots = sum(
        int(float(node["capacity_cpu"]) / 0.5)
        for node in document["cluster"]["nodes"] if node.get("capacity_cpu"))
    first_wave = [latency for _, latency in sorted(created_and_bound)[:max(1, total_slots)]]
    dispatch = statistics.median(first_wave) if first_wave else 0.5
    egress = {}
    nodes = document["cluster"]["nodes"]
    for node in nodes:
        measured = [
            float(peer["bandwidth_mib_s_by_peer"][node["name"]])
            for peer in nodes
            if peer["name"] != node["name"]
            and node["name"] in peer.get("bandwidth_mib_s_by_peer", {})
        ]
        if measured:
            egress[node["name"]] = statistics.median(measured)
    handovers = slot_handover_latencies(calibration)
    handover_median = statistics.median(handovers) if handovers else None
    result = Calibration(
        base_runtime_s={key: statistics.median(values) for key, values in work.items()},
        startup_s={key: statistics.median(values) for key, values in startup.items()},
        control_plane_delay_s=(
            (statistics.fmean(controller) if controller else 1.0) + dispatch),
        output_mib={key: statistics.median(values) for key, values in outputs.items()},
        egress_mib_s=egress,
        slot_release_delay_s=(
            handover_median if slot_release and handover_median else 0.0),
    )
    diagnostics = {
        "slot_handover_latency_median_s": handover_median,
        "slot_handovers": len(handovers),
        "samples_per_template": {key: len(values) for key, values in work.items()},
        "controller_latency_mean_s": statistics.fmean(controller) if controller else None,
        "dispatch_latency_first_wave_median_s": dispatch,
        "first_wave_size": len(first_wave),
    }
    return result, diagnostics


def simulate(profile: str, batch: str, policy: str, calibration: Calibration,
             seeds=(0, 1, 2, 3, 4)) -> dict:
    document = load_profiles(ROOT / "clusters" / "kind_profiles.yaml")
    batches = load_batches(ROOT / "experiments" / "k8s" / "batches.yaml")
    cluster = twin_cluster(profile, document, calibration)
    workload = twin_workload(batches, batch, calibration)
    compiled = workload.compile()
    runs = []
    for seed in seeds:
        sim_policy = create_policy(
            LIVE_TO_SIM[policy], seed=seed, cluster=cluster, workflow=compiled)
        result = Simulator(cluster, compiled, sim_policy, seed=seed).run()
        submissions = submission_results(workload, result)
        responses = [item.response_s for item in submissions.values()]
        runs.append({
            "seed": seed,
            "makespan_seconds": result.makespan_s,
            "mean_response_seconds": statistics.fmean(responses),
            "median_response_seconds": statistics.median(responses),
            "p95_response_seconds": percentile(responses, 0.95),
            "critical_response_seconds": (
                submissions["critical"].response_s if "critical" in submissions else None),
            "preemptions": sum(task.preemptions for task in result.tasks.values()),
            "transfer_mib": result.network_transfer_mib,
            "placements": {task_id: task.node_id for task_id, task in result.tasks.items()},
        })
    summary = {
        key: statistics.fmean(run[key] for run in runs if run[key] is not None)
        for key in ("makespan_seconds", "mean_response_seconds",
                    "median_response_seconds", "p95_response_seconds",
                    "preemptions", "transfer_mib")
    }
    critical = [run["critical_response_seconds"] for run in runs
                if run["critical_response_seconds"] is not None]
    summary["critical_response_seconds"] = statistics.fmean(critical) if critical else None
    return {"summary": summary, "runs": runs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--slot-release", choices=("none", "measured"), default="none",
        help="'measured' keeps a finished task's slot reserved for the "
             "warm-up's median completion-to-rebind latency")
    args = parser.parse_args()
    document = json.loads(args.results.read_text(encoding="utf-8"))
    calibration, diagnostics = derive_calibration(
        document, slot_release=args.slot_release == "measured")
    profile = document["profile"]
    predictions = {
        batch: {
            policy: simulate(profile, batch, policy, calibration)
            for policy in document["policies"]
        }
        for batch in document["batches"]
    }
    suffix = "-twin-release.json" if args.slot_release == "measured" else "-twin.json"
    output = args.output or args.results.with_name(f"{profile}{suffix}")
    output.write_text(json.dumps({
        "schema_version": 1,
        "profile": profile,
        "source": str(args.results.name),
        "calibration": {
            "base_runtime_s": dict(calibration.base_runtime_s),
            "startup_s": dict(calibration.startup_s),
            "control_plane_delay_s": calibration.control_plane_delay_s,
            "slot_release_delay_s": calibration.slot_release_delay_s,
            "output_mib": dict(calibration.output_mib),
            "egress_mib_s": dict(calibration.egress_mib_s),
            "diagnostics": diagnostics,
        },
        "predictions": predictions,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for batch, rows in predictions.items():
        ordered = sorted(rows.items(), key=lambda item: item[1]["summary"]["makespan_seconds"])
        print(batch, " ".join(
            f"{policy}={row['summary']['makespan_seconds']:.1f}s" for policy, row in ordered))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
