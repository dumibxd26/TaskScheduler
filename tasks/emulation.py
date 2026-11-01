"""Runtime emulation helpers shared by the live benchmark task images.

Three independent controls shape what a task container executes:

* ``TS_WORK_SCALE`` / ``TS_TIME_SCALE`` scale generated work and deliberate
  waits (kept for accelerated runs; the validation matrix uses 1.0).
* ``TS_NODE_SPEEDS`` (JSON ``{node: {cpu, memory, io}}``, mounted from the
  ``ts-emulation`` ConfigMap) together with ``TS_NODE_NAME`` (Downward API)
  emulates heterogeneous hardware on one host: a task on a node with speed
  ``s`` for its resource class performs ``1/s`` times the physical work (or,
  for paced storage, runs at ``s`` times the base device rate). The scheduler
  never reads these factors; it must learn them from observed runtimes.
* Parent outputs named by Channel A are staged from the producer's node-local
  directory when co-located, or fetched from its fileserver otherwise.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _positive_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number, got {raw!r}")
    return value


@dataclass(frozen=True)
class EmulationConfig:
    work_scale: float = 1.0
    time_scale: float = 1.0

    @classmethod
    def from_env(cls) -> "EmulationConfig":
        return cls(
            work_scale=_positive_env("TS_WORK_SCALE", 1.0),
            time_scale=_positive_env("TS_TIME_SCALE", 1.0),
        )

    def work_count(self, logical_count: int) -> int:
        if logical_count < 0:
            raise ValueError("logical work count must be non-negative")
        if logical_count == 0:
            return 0
        return max(1, round(logical_count * self.work_scale))

    def duration_s(self, logical_seconds: float) -> float:
        if logical_seconds < 0:
            raise ValueError("logical duration must be non-negative")
        return logical_seconds * self.time_scale


@dataclass(frozen=True)
class NodeSpeed:
    """Emulated per-resource speed of the node this task landed on."""

    node: Optional[str] = None
    cpu: float = 1.0
    memory: float = 1.0
    io: float = 1.0

    @classmethod
    def from_env(cls) -> "NodeSpeed":
        node = os.environ.get("TS_NODE_NAME") or None
        raw = os.environ.get("TS_NODE_SPEEDS", "").strip()
        if not raw or node is None:
            return cls(node=node)
        table = json.loads(raw)
        if not isinstance(table, dict):
            raise ValueError("TS_NODE_SPEEDS must be a JSON object")
        entry = table.get(node) or {}
        values = {}
        for key in ("cpu", "memory", "io"):
            value = float(entry.get(key, 1.0))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"speed '{key}' for node {node} must be positive")
            values[key] = value
        return cls(node=node, **values)

    def factor(self, kind: str) -> float:
        return float(getattr(self, kind))


def emulated_count(logical: int, config: EmulationConfig, speed: float) -> int:
    """Physical repetitions for ``logical`` work on a node of ``speed``."""
    if logical <= 0:
        return 0
    return max(1, round(logical * config.work_scale / speed))


def run_for_cpu_seconds(target_cpu_s: float, chunk) -> int:
    """Repeat ``chunk()`` until this process has used ``target_cpu_s`` of CPU.

    CPU time, not wall time or iteration count, is what a cgroup quota
    meters, so the resulting duration is ``target / quota`` on any idle host
    and is not distorted by hyperthread siblings or by how fast this Python
    build hashes. Returns the number of chunks executed.
    """
    started = time.process_time()
    chunks = 0
    while time.process_time() - started < target_cpu_s:
        chunk()
        chunks += 1
    return chunks


@dataclass
class StagedInputs:
    input_bytes: int = 0
    transfer_seconds: float = 0.0
    mode: Optional[str] = None
    lines: List[str] = field(default_factory=list)


def stage_parent_inputs(inputs_dir: str,
                        file_field: str = "generated_file_path") -> StagedInputs:
    """Copy or fetch each DATA parent's file named by Channel A.

    A parent on the same node is read through the shared hostPath
    (``[LOCAL]``); otherwise the file is downloaded from the producer's
    fileserver (``[FETCH]``), crossing the emulated network.
    """
    staged = StagedInputs()
    file_name = os.environ.get(file_field)
    if not file_name:
        return staged
    os.makedirs(inputs_dir, exist_ok=True)
    parents = sorted(
        (key[:-len("_FILESERVER_URL")], value)
        for key, value in os.environ.items()
        if key.startswith("TS_PARENT_") and key.endswith("_FILESERVER_URL")
    )
    for prefix, url in parents:
        parent_dir = os.environ.get(f"{prefix}_OUTPUTS_DIR", "")
        local_source = os.path.join(
            "/data/outputs", parent_dir.lstrip("/"), file_name)
        destination = os.path.join(
            inputs_dir, f"{prefix.lower()}-{os.path.basename(file_name)}")
        if os.path.isfile(local_source):
            line = f"[LOCAL] {local_source} -> {destination}"
            print(line)
            shutil.copyfile(local_source, destination)
            staged.mode = staged.mode or "local"
        else:
            remote = f"{url}/{file_name}"
            line = f"[FETCH] {remote} -> {destination}"
            print(line)
            started = time.perf_counter()
            urllib.request.urlretrieve(remote, destination)
            staged.transfer_seconds += time.perf_counter() - started
            staged.mode = "remote"
        staged.lines.append(line)
        staged.input_bytes += os.path.getsize(destination)
    return staged


class TaskReport:
    """Measures one task and prints the Channel A and metrics markers."""

    def __init__(self, kind: str):
        self.kind = kind
        self.config = EmulationConfig.from_env()
        self.speed = NodeSpeed.from_env()
        self.started_epoch = time.time()
        self.started = time.perf_counter()
        self.emulation: Dict[str, object] = {
            "work_scale": self.config.work_scale,
            "time_scale": self.config.time_scale,
            "speed_kind": kind,
            "speed_factor": self.speed.factor(kind),
        }

    @property
    def speed_factor(self) -> float:
        return self.speed.factor(self.kind)

    def emit(self, output: dict, *, io_bytes_read: int = 0,
             io_bytes_written: int = 0,
             output_bytes_by_field: Optional[Dict[str, int]] = None,
             transfer_seconds: float = 0.0) -> None:
        runtime = time.perf_counter() - self.started
        print(f"__TS_OUTPUT__={json.dumps(output)}")
        metrics = {
            "runtime_seconds": runtime,
            "started_at_epoch": self.started_epoch,
            "finished_at_epoch": time.time(),
            "node_name": self.speed.node,
            "io_bytes_read": int(io_bytes_read),
            "io_bytes_written": int(io_bytes_written),
            "output_bytes_by_field": dict(output_bytes_by_field or {}),
            "transfer_seconds": transfer_seconds,
            "emulation": self.emulation,
        }
        print(f"__TS_METRICS__={json.dumps(metrics)}")
