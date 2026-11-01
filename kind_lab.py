"""Create and configure same-host Kind validation clusters from profiles.

Every profile in ``clusters/kind_profiles.yaml`` is applied the same way:

1. render a Kind config (worker names, node-type and capacity labels),
2. create the cluster and cap each worker container with ``docker update``,
3. shape each worker's egress with ``tc netem`` and read the qdisc back,
4. publish emulated speeds in the ``ts-emulation`` ConfigMap (read only by
   task containers) and record everything in a JSON sidecar.

Usage::

    python kind_lab.py create balanced --record results/kind-validation/balanced-cluster.json
    python kind_lab.py apply-network balanced
    python kind_lab.py reset-network
    python kind_lab.py render-config balanced
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).parent
PROFILES = ROOT / "clusters" / "kind_profiles.yaml"
EMULATION_CONFIGMAP = "ts-emulation"
TASK_SLOT_CPU = 0.5
TASK_SLOT_MEMORY_MIB = 256
_DELAY = re.compile(r"delay\s+([0-9.]+)(us|ms|s)\b")
_RATE = re.compile(r"rate\s+([0-9.]+)(bit|Kbit|Mbit|Gbit|Tbit)\b")
_DELAY_UNITS_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0}
_RATE_UNITS_MBIT = {"bit": 1e-6, "Kbit": 1e-3, "Mbit": 1.0, "Gbit": 1e3, "Tbit": 1e6}


@dataclass(frozen=True)
class Worker:
    name: str
    node_type: str
    slots: int
    speed: Mapping[str, float]
    delay_ms: float
    rate_mbit: float

    @property
    def capacity_cpu(self) -> float:
        return self.slots * TASK_SLOT_CPU

    @property
    def capacity_memory_mib(self) -> int:
        # Memory never binds before CPU: one extra half slot of headroom.
        return self.slots * TASK_SLOT_MEMORY_MIB + TASK_SLOT_MEMORY_MIB // 2


def load_document(path: Path = PROFILES) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported Kind profile schema_version")
    return document


def workers(document: Mapping[str, Any], profile: str) -> list[Worker]:
    if profile not in document["profiles"]:
        raise ValueError(
            f"unknown profile {profile!r}; expected one of "
            f"{sorted(document['profiles'])}")
    result = []
    for item in document["profiles"][profile]["workers"]:
        worker = Worker(
            name=str(item["name"]),
            node_type=str(item["node_type"]),
            slots=int(item["slots"]),
            speed={key: float(item["speed"][key]) for key in ("cpu", "memory", "io")},
            delay_ms=float(item["network"]["delay_ms"]),
            rate_mbit=float(item["network"]["rate_mbit"]),
        )
        if worker.slots < 1 or worker.rate_mbit <= 0 or worker.delay_ms < 0:
            raise ValueError(f"invalid worker definition for {worker.name}")
        if any(value <= 0 for value in worker.speed.values()):
            raise ValueError(f"speeds for {worker.name} must be positive")
        result.append(worker)
    if len({worker.name for worker in result}) != len(result):
        raise ValueError(f"profile {profile!r} repeats a worker name")
    return result


def render_kind_config(document: Mapping[str, Any], profile: str) -> str:
    nodes: list[dict] = [{"role": "control-plane"}]
    for worker in workers(document, profile):
        nodes.append({
            "role": "worker",
            "labels": {
                "node-type": worker.node_type,
                "ts.capacity/cpu": f"{worker.capacity_cpu:g}",
                "ts.capacity/memory": str(worker.capacity_memory_mib),
                "ts.io/cooling-class": "STANDARD",
                "ts.io/thermal-throttle-c": "100",
                "ts.io/kind-profile": profile,
            },
            "kubeadmConfigPatches": [
                "kind: JoinConfiguration\n"
                "nodeRegistration:\n"
                f"  name: {worker.name}\n"
            ],
        })
    config = {
        "kind": "Cluster",
        "apiVersion": "kind.x-k8s.io/v1alpha4",
        "name": document.get("cluster_name", "ts-cluster"),
        "nodes": nodes,
    }
    return yaml.safe_dump(config, sort_keys=False)


def docker_caps(document: Mapping[str, Any], worker: Worker) -> tuple[float, int]:
    host = document["host"]
    cpus = round(worker.capacity_cpu + float(host["agent_cpus"]), 3)
    memory = int(host["agent_memory_mib"]) + worker.slots * int(host["task_memory_mib"])
    return cpus, memory


def netem_command(container: str, worker: Worker, interface: str = "eth0") -> list[str]:
    return [
        "docker", "exec", container, "tc", "qdisc", "replace", "dev", interface,
        "root", "netem", "delay", f"{worker.delay_ms:g}ms",
        "rate", f"{worker.rate_mbit:g}mbit",
    ]


def parse_qdisc(text: str) -> dict[str, float | None]:
    """Extract delay (ms) and rate (Mbit/s) from ``tc qdisc show`` output."""
    if not text.lower().lstrip().startswith("qdisc netem"):
        return {"delay_ms": None, "rate_mbit": None}
    delay = _DELAY.search(text)
    rate = _RATE.search(text)
    return {
        "delay_ms": (float(delay.group(1)) * _DELAY_UNITS_MS[delay.group(2)]
                     if delay else 0.0),
        "rate_mbit": (float(rate.group(1)) * _RATE_UNITS_MBIT[rate.group(2)]
                      if rate else None),
    }


def _run(command: Sequence[str], *, check: bool = True,
         input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command), capture_output=True, text=True, input=input_text,
        check=False)
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}")
    return completed


def worker_containers(cluster: str) -> dict[str, str]:
    """Map Kubernetes worker node names to their Docker container names."""
    containers = _run([
        "docker", "ps", "--filter", f"label=io.x-k8s.kind.cluster={cluster}",
        "--filter", "label=io.x-k8s.kind.role=worker", "--format", "{{.Names}}",
    ]).stdout.split()
    by_ip = {}
    for container in containers:
        ip = _run([
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container,
        ]).stdout.strip()
        by_ip[ip] = container
    nodes = json.loads(_run(["kubectl", "get", "nodes", "-o", "json"]).stdout)
    mapping = {}
    for node in nodes["items"]:
        addresses = {
            item["type"]: item["address"] for item in node["status"]["addresses"]}
        container = by_ip.get(addresses.get("InternalIP"))
        if container:
            mapping[node["metadata"]["name"]] = container
    return mapping


def apply_network(document: Mapping[str, Any], profile: str,
                  interface: str = "eth0") -> list[dict]:
    cluster = document.get("cluster_name", "ts-cluster")
    containers = worker_containers(cluster)
    records = []
    for worker in workers(document, profile):
        container = containers[worker.name]
        command = netem_command(container, worker, interface)
        _run(command)
        observed = _run([
            "docker", "exec", container, "tc", "qdisc", "show", "dev", interface,
        ]).stdout.strip()
        parsed = parse_qdisc(observed)
        if (parsed["rate_mbit"] is None
                or abs(parsed["rate_mbit"] - worker.rate_mbit) > 1e-6 * worker.rate_mbit
                or abs(parsed["delay_ms"] - worker.delay_ms) > 1e-6):
            raise RuntimeError(
                f"{worker.name}: applied qdisc does not match the profile: {observed}")
        records.append({
            "node": worker.name,
            "container": container,
            "interface": interface,
            "delay_ms": worker.delay_ms,
            "rate_mbit": worker.rate_mbit,
            "command": command,
            "observed_qdisc": observed,
            "observed": parsed,
        })
    return records


def reset_network(cluster: str, interface: str = "eth0") -> list[str]:
    reset = []
    for container in worker_containers(cluster).values():
        _run(["docker", "exec", container, "tc", "qdisc", "del", "dev",
              interface, "root"], check=False)
        reset.append(container)
    return reset


def publish_emulation(document: Mapping[str, Any], profile: str,
                      network: list[dict], namespace: str = "default") -> dict:
    """Write the speed ConfigMap and descriptive node annotations."""
    speeds = {worker.name: dict(worker.speed) for worker in workers(document, profile)}
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": EMULATION_CONFIGMAP, "namespace": namespace},
        "data": {"node-speeds": json.dumps(speeds, sort_keys=True),
                 "profile": profile},
    }
    _run(["kubectl", "apply", "-f", "-"], input_text=json.dumps(manifest))
    by_node = {record["node"]: record for record in network}
    for worker in workers(document, profile):
        record = by_node.get(worker.name, {})
        _run([
            "kubectl", "annotate", "node", worker.name, "--overwrite",
            f"ts.io/kind-profile={profile}",
            f"ts.io/netem-delay-ms={record.get('delay_ms', worker.delay_ms):g}",
            f"ts.io/netem-rate-mbit={record.get('rate_mbit', worker.rate_mbit):g}",
            f"ts.io/emulated-speed={json.dumps(dict(worker.speed), sort_keys=True)}",
        ])
    return speeds


def create(document: Mapping[str, Any], profile: str, record: Path | None) -> dict:
    cluster = document.get("cluster_name", "ts-cluster")
    config_text = render_kind_config(document, profile)
    _run(["kind", "delete", "cluster", "--name", cluster], check=False)
    with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8") as stream:
        stream.write(config_text)
        config_path = stream.name
    try:
        _run(["kind", "create", "cluster", "--config", config_path, "--wait", "180s"])
    finally:
        Path(config_path).unlink(missing_ok=True)
    containers = worker_containers(cluster)
    caps = {}
    for worker in workers(document, profile):
        cpus, memory = docker_caps(document, worker)
        # CPU shares proportional to slots make any residual host contention
        # fair per task rather than per worker container.
        shares = 1024 * worker.slots
        _run(["docker", "update", f"--cpus={cpus:g}", f"--cpu-shares={shares}",
              f"--memory={memory}m", f"--memory-swap={memory}m",
              containers[worker.name]])
        caps[worker.name] = {"container": containers[worker.name],
                             "cpus": cpus, "cpu_shares": shares,
                             "memory_mib": memory}
    _run(["kubectl", "wait", "--for=condition=Ready", "nodes", "--all",
          "--timeout=180s"])
    network = apply_network(document, profile)
    speeds = publish_emulation(document, profile, network)
    evidence = {
        "schema_version": 1,
        "profile": profile,
        "mirrors": document["profiles"][profile]["mirrors"],
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "kind_config": config_text,
        "docker_caps": caps,
        "network": network,
        "speeds": speeds,
        "evidence_scope": (
            "worker containers on one Docker host; capacities, speeds, and "
            "links are emulated, not independent hardware"),
    }
    if record is not None:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("profile")
    create_parser.add_argument("--record", type=Path)
    network = commands.add_parser("apply-network")
    network.add_argument("profile")
    commands.add_parser("reset-network")
    render = commands.add_parser("render-config")
    render.add_argument("profile")
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    args = parser.parse_args(argv)
    document = load_document(args.profiles)
    if args.command == "create":
        evidence = create(document, args.profile, args.record)
        print(json.dumps({key: evidence[key] for key in (
            "profile", "docker_caps", "speeds")}, indent=2))
    elif args.command == "apply-network":
        print(json.dumps(apply_network(document, args.profile), indent=2))
    elif args.command == "reset-network":
        print(json.dumps(reset_network(document.get("cluster_name", "ts-cluster"))))
    else:
        sys.stdout.write(render_kind_config(document, args.profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
