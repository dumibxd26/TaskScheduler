"""Digital twins of the same-host Kind validation profiles.

A twin is a simulator cluster plus the live workload, both derived from the
profile file (``clusters/kind_profiles.yaml``) and from calibration
measurements taken on the running Kind cluster before any policy trial:

* one simulated core represents one live 0.5-CPU task slot, so a worker's
  ``slots`` become its core count and its emulated speeds scale the per-slot
  CPU, DRAM, and storage rates (per-task caps keep one Pod from using a whole
  node, exactly like the 500m cgroup limit);
* each worker's tc-netem egress becomes a directional link to a switch whose
  forward rate and delay are the shaped egress (measured when available) and
  whose reverse direction is effectively unshaped;
* measured task work at speed 1.0, container startup, and orchestration
  latency (controller reconcile plus scheduler tick) are model inputs.

Policies in the twin see the same structure as in Kubernetes, so their
predicted ranking can be compared with the observed live ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulation.schema import ClusterSpec, WorkflowSpec
from simulation.workloads import WorkflowSubmission, WorkloadSpec

# Reference rates of one simulated slot at speed 1.0. Only their products
# with the calibrated durations matter, so the absolute values are arbitrary.
SLOT_GFLOPS = 1.0
SLOT_MEMORY_MIB_S = 1000.0
SLOT_STORAGE_MIB_S = 100.0
TASK_MEMORY_MIB = 256.0
MBIT_TO_MIB_S = 1_000_000 / 8 / (1024 * 1024)
FAST_REVERSE_MIB_S = 10_000 * MBIT_TO_MIB_S
PROTOCOL_EFFICIENCY = 0.97
TEMPLATE_CLASSES = {
    "experiment-cpu": ("CPU_BOUND", "cpu"),
    "experiment-cpu-long": ("CPU_BOUND", "cpu"),
    "experiment-mem": ("MEMORY_BOUND", "memory"),
    "experiment-io": ("IO_BOUND", "io"),
    "experiment-io-large": ("IO_BOUND", "io"),
}
IMAGES = {
    "experiment-cpu": "ts-task-cpu:v1",
    "experiment-cpu-long": "ts-task-cpu:v1",
    "experiment-mem": "ts-task-mem:v1",
    "experiment-io": "ts-task-io:v1",
    "experiment-io-large": "ts-task-io:v1",
}


@dataclass(frozen=True)
class Calibration:
    """Measurements taken on the live cluster during the warm-up phase.

    ``base_runtime_s`` is a template's compute time at speed 1.0 (measured
    runtime multiplied by the node's emulated speed, input staging excluded);
    ``output_mib`` is the file a template publishes for DATA children.
    """

    base_runtime_s: Mapping[str, float]
    startup_s: Mapping[str, float]
    control_plane_delay_s: float
    output_mib: Mapping[str, float]
    egress_mib_s: Mapping[str, float]
    # Time from a task finishing to its slot being placeable again; 0 keeps
    # the original twin, which frees capacity the instant a task ends.
    slot_release_delay_s: float = 0.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Calibration":
        return cls(
            base_runtime_s={k: float(v) for k, v in data["base_runtime_s"].items()},
            startup_s={k: float(v) for k, v in data["startup_s"].items()},
            control_plane_delay_s=float(data["control_plane_delay_s"]),
            output_mib={k: float(v) for k, v in data.get("output_mib", {}).items()},
            egress_mib_s={k: float(v) for k, v in data.get("egress_mib_s", {}).items()},
            slot_release_delay_s=float(data.get("slot_release_delay_s", 0.0)),
        )


def load_profiles(path: str | Path) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported Kind profile schema_version")
    return document


def twin_cluster(profile_name: str, document: Mapping[str, Any],
                 calibration: Calibration) -> ClusterSpec:
    profile = document["profiles"][profile_name]
    nodes = []
    links = []
    for worker in profile["workers"]:
        slots = int(worker["slots"])
        speed = worker["speed"]
        name = worker["name"]
        nodes.append({
            "id": name,
            "type": worker["node_type"],
            "cooling_class": "STANDARD",
            "background_cpu_fraction": 0.0,
            "background_memory_mib": 32,
            "initial_warm_images": sorted(set(IMAGES.values())),
            "cpu": {
                "architecture": "amd64",
                "microarchitecture": "kind-emulated-slot",
                "physical_cores": slots,
                "threads_per_core": 1,
                "base_frequency_ghz": 2.3,
                "max_frequency_ghz": 2.3,
                "calibrated_gflops_per_core": SLOT_GFLOPS * float(speed["cpu"]),
                "l3_cache_mib": 1,
                "idle_power_w": 2,
                "max_power_w": 2 + 6 * slots,
                "smt_efficiency": 0,
            },
            "memory": {
                "capacity_mib": slots * TASK_MEMORY_MIB + 64,
                "calibrated_bandwidth_mib_s": (
                    slots * SLOT_MEMORY_MIB_S * float(speed["memory"])),
                "per_task_bandwidth_mib_s": SLOT_MEMORY_MIB_S * float(speed["memory"]),
                "latency_ns": 80,
                "channels": 2,
            },
            "storage": {
                "capacity_mib": 100_000,
                "sequential_read_mib_s": 10_000,
                "sequential_write_mib_s": (
                    slots * SLOT_STORAGE_MIB_S * float(speed["io"])),
                "per_task_write_mib_s": SLOT_STORAGE_MIB_S * float(speed["io"]),
                "random_read_iops": 1_000_000,
                "random_write_iops": 1_000_000,
            },
            "thermal": {
                "ambient_c": 25, "initial_c": 40, "throttle_c": 95,
                "shutdown_c": 105, "thermal_resistance_c_per_w": 0.1,
                "thermal_capacitance_j_per_c": 100,
            },
        })
        nominal = float(worker["network"]["rate_mbit"]) * MBIT_TO_MIB_S
        measured = calibration.egress_mib_s.get(name)
        # The engine applies protocol efficiency itself; undo it for a
        # measured application-level rate so the twin reproduces it.
        egress = measured / PROTOCOL_EFFICIENCY if measured else nominal
        links.append({
            "id": f"{name}-link",
            "endpoints": [name, "switch"],
            "bandwidth_mib_s": round(egress, 6),
            "latency_ms": float(worker["network"]["delay_ms"]) + 0.05,
            "reverse_bandwidth_mib_s": round(FAST_REVERSE_MIB_S, 6),
            "reverse_latency_ms": 0.05,
            "medium": "ethernet",
        })
    return ClusterSpec.from_mapping({
        "schema_version": 1,
        "cluster": {
            "id": f"kind-twin-{profile_name}",
            "family": f"kind-twin-{profile['mirrors']}",
            "description": f"Calibrated digital twin of Kind profile '{profile_name}'.",
            "calibration": {"status": "measured",
                            "source": "Kind warm-up phase", "uncertainty_fraction": 0},
            "network_vertices": ["switch"],
            "control_plane_delay_s": calibration.control_plane_delay_s,
            "slot_release_delay_s": calibration.slot_release_delay_s,
            "nodes": nodes,
            "links": links,
        },
    })


def _task(template: str, name: str, calibration: Calibration) -> dict:
    task_class, kind = TEMPLATE_CLASSES[template]
    duration = calibration.base_runtime_s[template]
    data: dict = {
        "id": name,
        "class": task_class,
        "image": IMAGES[template],
        "image_size_mib": 0,
        "startup_s": calibration.startup_s.get(template, 1.0),
        "supported_architectures": ["amd64"],
        "cpu_cores": 1,
        "memory_mib": TASK_MEMORY_MIB,
        "working_set_mib": 128,
    }
    if kind == "cpu":
        data["compute_gflop"] = duration * SLOT_GFLOPS
    elif kind == "memory":
        data["memory_read_mib"] = duration * SLOT_MEMORY_MIB_S
    else:
        data["disk_write_mib"] = duration * SLOT_STORAGE_MIB_S
    return data


def load_batches(path: str | Path) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported batch schema_version")
    return document


def twin_workload(batches: Mapping[str, Any], batch: str,
                  calibration: Calibration) -> WorkloadSpec:
    """Simulator workload equivalent to one live trial batch."""
    delay_s = float(batches.get("priority_delay_s", 0.0))
    submissions = []
    for workflow in batches["batches"][batch]["workflows"]:
        templates = {task["name"]: task["template"] for task in workflow["tasks"]}
        tasks = [_task(task["template"], task["name"], calibration)
                 for task in workflow["tasks"]]
        dependencies = []
        for task in workflow["tasks"]:
            for dependency in task.get("dependsOn", []):
                parent = dependency["parent"]
                moves_file = (
                    dependency.get("dependencyType", "DATA") == "DATA"
                    and "generated_file_path" in dependency.get("dataFields", []))
                dependencies.append({
                    "parent": parent,
                    "child": task["name"],
                    "data_mib": (calibration.output_mib.get(templates[parent], 0.0)
                                 if moves_file else 0.0),
                })
        spec = WorkflowSpec.from_mapping({
            "workflow": {
                "id": workflow["name"],
                "priority": workflow["priority"],
                "preemptable": workflow.get("preemptable", True),
                "tasks": tasks,
                "dependencies": dependencies,
            },
        })
        arrival = 0.0 if workflow.get("arrival", "start") == "start" else delay_s
        submissions.append(WorkflowSubmission(
            workflow["name"], spec, arrival_s=arrival))
    return WorkloadSpec(workload_id=f"kind-{batch}-batch",
                        submissions=tuple(submissions))


def cluster_mapping(cluster: ClusterSpec) -> dict:
    """Serialisable YAML mapping for a twin cluster (for inspection)."""
    def node_mapping(node) -> dict:
        return {
            "id": node.node_id,
            "type": node.node_type.name,
            "slots": node.cpu.physical_cores,
            "cpu_gflops_per_slot": node.cpu.calibrated_gflops_per_core,
            "memory_mib_s_per_slot": node.memory.per_task_bandwidth_mib_s,
            "storage_write_mib_s_per_slot": node.storage.per_task_write_mib_s,
        }
    return {
        "cluster": cluster.cluster_id,
        "control_plane_delay_s": cluster.control_plane_delay_s,
        "nodes": [node_mapping(node) for node in cluster.nodes],
        "links": [
            {"id": link.link_id, "egress_mib_s": link.bandwidth_mib_s,
             "egress_latency_ms": link.latency_ms}
            for link in cluster.links
        ],
    }


def submission_task_count(workload: WorkloadSpec) -> int:
    return sum(len(submission.workflow.tasks) for submission in workload.submissions)


__all__ = [
    "Calibration", "load_batches", "load_profiles", "twin_cluster",
    "twin_workload", "cluster_mapping", "submission_task_count",
]
