"""Versioned, unit-explicit input schema for simulation experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from models.enums import CoolingClass, NodeType, PriorityClass, TaskClass


SCHEMA_VERSION = 1


class ModelValidationError(ValueError):
    """Raised when a cluster or workload description is not physically valid."""


def _positive(name: str, value: float) -> None:
    if value <= 0:
        raise ModelValidationError(f"{name} must be > 0, got {value}")


def _non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ModelValidationError(f"{name} must be >= 0, got {value}")


def _fraction(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ModelValidationError(f"{name} must be in [0, 1], got {value}")


def _enum(enum_type, raw: str, field_name: str):
    try:
        return enum_type[str(raw).upper()]
    except KeyError as exc:
        allowed = ", ".join(item.name for item in enum_type)
        raise ModelValidationError(
            f"unknown {field_name} '{raw}'; expected one of: {allowed}"
        ) from exc


_TRUE_STRINGS = frozenset({"true", "yes", "on", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "off", "0"})


def parse_bool(raw: Any, field_name: str) -> bool:
    """Parse a YAML boolean without Python's ``bool('false') is True`` trap."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
    raise ModelValidationError(
        f"{field_name} must be a boolean, got {raw!r}")


@dataclass(frozen=True)
class CalibrationSpec:
    status: str = "unspecified"
    source: str = "unspecified"
    uncertainty_fraction: float = 0.0

    def __post_init__(self):
        allowed = {"unspecified", "estimated", "measured", "synthetic"}
        if self.status not in allowed:
            raise ModelValidationError(
                f"calibration.status must be one of {sorted(allowed)}"
            )
        _fraction("calibration.uncertainty_fraction", self.uncertainty_fraction)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "CalibrationSpec":
        data = data or {}
        return cls(
            status=str(data.get("status", "unspecified")).lower(),
            source=str(data.get("source", "unspecified")),
            uncertainty_fraction=float(data.get("uncertainty_fraction", 0.0)),
        )


@dataclass(frozen=True)
class CpuSpec:
    architecture: str
    microarchitecture: str
    physical_cores: int
    threads_per_core: int
    base_frequency_ghz: float
    max_frequency_ghz: float
    calibrated_gflops_per_core: float
    l3_cache_mib: float
    idle_power_w: float
    max_power_w: float
    smt_efficiency: float = 0.30
    vector_extensions: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.architecture:
            raise ModelValidationError("cpu.architecture must not be empty")
        if self.physical_cores < 1 or self.threads_per_core < 1:
            raise ModelValidationError("CPU core and thread counts must be >= 1")
        for name in (
            "base_frequency_ghz", "max_frequency_ghz",
            "calibrated_gflops_per_core", "idle_power_w", "max_power_w",
        ):
            _positive(f"cpu.{name}", getattr(self, name))
        _non_negative("cpu.l3_cache_mib", self.l3_cache_mib)
        _fraction("cpu.smt_efficiency", self.smt_efficiency)
        if self.max_frequency_ghz < self.base_frequency_ghz:
            raise ModelValidationError("CPU max frequency cannot be below base frequency")
        if self.max_power_w < self.idle_power_w:
            raise ModelValidationError("CPU max power cannot be below idle power")

    @property
    def logical_cpus(self) -> int:
        return self.physical_cores * self.threads_per_core

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CpuSpec":
        return cls(
            architecture=str(data["architecture"]),
            microarchitecture=str(data.get("microarchitecture", "unknown")),
            physical_cores=int(data["physical_cores"]),
            threads_per_core=int(data.get("threads_per_core", 1)),
            base_frequency_ghz=float(data["base_frequency_ghz"]),
            max_frequency_ghz=float(data.get(
                "max_frequency_ghz", data["base_frequency_ghz"])),
            calibrated_gflops_per_core=float(data["calibrated_gflops_per_core"]),
            l3_cache_mib=float(data.get("l3_cache_mib", 0.0)),
            idle_power_w=float(data["idle_power_w"]),
            max_power_w=float(data["max_power_w"]),
            smt_efficiency=float(data.get("smt_efficiency", 0.30)),
            vector_extensions=tuple(str(v).upper() for v in data.get(
                "vector_extensions", ())),
        )


def _optional_positive(data: Mapping[str, Any], key: str, name: str) -> float | None:
    if data.get(key) is None:
        return None
    value = float(data[key])
    _positive(name, value)
    return value


@dataclass(frozen=True)
class MemorySpec:
    """Node DRAM. ``per_task_bandwidth_mib_s`` optionally caps one task's
    DRAM rate (a single core or cgroup cannot use every channel)."""

    capacity_mib: float
    calibrated_bandwidth_mib_s: float
    latency_ns: float
    channels: int = 1
    per_task_bandwidth_mib_s: float | None = None

    def __post_init__(self):
        _positive("memory.capacity_mib", self.capacity_mib)
        _positive("memory.calibrated_bandwidth_mib_s", self.calibrated_bandwidth_mib_s)
        _positive("memory.latency_ns", self.latency_ns)
        if self.channels < 1:
            raise ModelValidationError("memory.channels must be >= 1")
        if self.per_task_bandwidth_mib_s is not None:
            _positive("memory.per_task_bandwidth_mib_s", self.per_task_bandwidth_mib_s)

    @property
    def task_bandwidth_limit_mib_s(self) -> float:
        if self.per_task_bandwidth_mib_s is None:
            return self.calibrated_bandwidth_mib_s
        return min(self.per_task_bandwidth_mib_s, self.calibrated_bandwidth_mib_s)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "MemorySpec":
        return cls(
            capacity_mib=float(data["capacity_mib"]),
            calibrated_bandwidth_mib_s=float(data["calibrated_bandwidth_mib_s"]),
            latency_ns=float(data.get("latency_ns", 80.0)),
            channels=int(data.get("channels", 1)),
            per_task_bandwidth_mib_s=_optional_positive(
                data, "per_task_bandwidth_mib_s", "memory.per_task_bandwidth_mib_s"),
        )


@dataclass(frozen=True)
class StorageSpec:
    """Node storage. Optional per-task caps model a per-container I/O limit
    (for example a blkio throttle or a rate-limited emulated device)."""

    capacity_mib: float
    sequential_read_mib_s: float
    sequential_write_mib_s: float
    random_read_iops: float
    random_write_iops: float
    per_task_read_mib_s: float | None = None
    per_task_write_mib_s: float | None = None

    def __post_init__(self):
        for name in (
            "capacity_mib", "sequential_read_mib_s", "sequential_write_mib_s",
            "random_read_iops", "random_write_iops",
        ):
            _positive(f"storage.{name}", getattr(self, name))
        for name in ("per_task_read_mib_s", "per_task_write_mib_s"):
            if getattr(self, name) is not None:
                _positive(f"storage.{name}", getattr(self, name))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "StorageSpec":
        values = {name: float(data[name]) for name in (
            "capacity_mib", "sequential_read_mib_s", "sequential_write_mib_s",
            "random_read_iops", "random_write_iops",
        )}
        return cls(
            **values,
            per_task_read_mib_s=_optional_positive(
                data, "per_task_read_mib_s", "storage.per_task_read_mib_s"),
            per_task_write_mib_s=_optional_positive(
                data, "per_task_write_mib_s", "storage.per_task_write_mib_s"),
        )


@dataclass(frozen=True)
class ThermalSpec:
    ambient_c: float
    initial_c: float
    throttle_c: float
    shutdown_c: float
    thermal_resistance_c_per_w: float
    thermal_capacitance_j_per_c: float
    minimum_throttle_factor: float = 0.45

    def __post_init__(self):
        _positive("thermal.thermal_resistance_c_per_w", self.thermal_resistance_c_per_w)
        _positive("thermal.thermal_capacitance_j_per_c", self.thermal_capacitance_j_per_c)
        _fraction("thermal.minimum_throttle_factor", self.minimum_throttle_factor)
        if not self.ambient_c <= self.initial_c < self.shutdown_c:
            raise ModelValidationError(
                "thermal temperatures must satisfy ambient <= initial < shutdown"
            )
        if not self.ambient_c < self.throttle_c < self.shutdown_c:
            raise ModelValidationError(
                "thermal temperatures must satisfy ambient < throttle < shutdown"
            )

    @property
    def time_constant_s(self) -> float:
        return self.thermal_resistance_c_per_w * self.thermal_capacitance_j_per_c

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ThermalSpec":
        return cls(
            ambient_c=float(data.get("ambient_c", 22.0)),
            initial_c=float(data.get("initial_c", 35.0)),
            throttle_c=float(data["throttle_c"]),
            shutdown_c=float(data.get("shutdown_c", float(data["throttle_c"]) + 5.0)),
            thermal_resistance_c_per_w=float(data["thermal_resistance_c_per_w"]),
            thermal_capacitance_j_per_c=float(data["thermal_capacitance_j_per_c"]),
            minimum_throttle_factor=float(data.get("minimum_throttle_factor", 0.45)),
        )


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    node_type: NodeType
    cooling_class: CoolingClass
    cpu: CpuSpec
    memory: MemorySpec
    storage: StorageSpec
    thermal: ThermalSpec
    background_cpu_fraction: float = 0.02
    background_memory_mib: float = 128.0
    failure_rate_per_hour: float = 0.0
    mean_repair_s: float = 30.0
    failure_schedule_s: tuple[float, ...] = ()
    initial_warm_images: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.node_id:
            raise ModelValidationError("node_id must not be empty")
        _fraction("node.background_cpu_fraction", self.background_cpu_fraction)
        _non_negative("node.background_memory_mib", self.background_memory_mib)
        _non_negative("node.failure_rate_per_hour", self.failure_rate_per_hour)
        _positive("node.mean_repair_s", self.mean_repair_s)
        for failure_time_s in self.failure_schedule_s:
            _non_negative("node.failure_schedule_s", failure_time_s)
        if tuple(sorted(self.failure_schedule_s)) != self.failure_schedule_s:
            raise ModelValidationError("node.failure_schedule_s must be sorted")
        if self.background_memory_mib >= self.memory.capacity_mib:
            raise ModelValidationError(
                f"node '{self.node_id}' background memory consumes all capacity"
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "NodeSpec":
        return cls(
            node_id=str(data["id"]),
            node_type=_enum(NodeType, data.get("type", "GENERAL"), "node type"),
            cooling_class=_enum(
                CoolingClass, data.get("cooling_class", "STANDARD"), "cooling class"),
            cpu=CpuSpec.from_mapping(data["cpu"]),
            memory=MemorySpec.from_mapping(data["memory"]),
            storage=StorageSpec.from_mapping(data["storage"]),
            thermal=ThermalSpec.from_mapping(data["thermal"]),
            background_cpu_fraction=float(data.get("background_cpu_fraction", 0.02)),
            background_memory_mib=float(data.get("background_memory_mib", 128.0)),
            failure_rate_per_hour=float(data.get("failure_rate_per_hour", 0.0)),
            mean_repair_s=float(data.get("mean_repair_s", 30.0)),
            failure_schedule_s=tuple(float(v) for v in data.get(
                "failure_schedule_s", ())),
            initial_warm_images=frozenset(str(v) for v in data.get(
                "initial_warm_images", ())),
        )


HALF_DUPLEX_MEDIA = frozenset({"wifi"})


@dataclass(frozen=True)
class NetworkLinkSpec:
    """A routed link between two endpoints.

    ``bandwidth_mib_s`` and ``latency_ms`` describe the endpoint_a -> endpoint_b
    direction; the optional ``reverse_*`` values describe endpoint_b ->
    endpoint_a and default to the forward values. Full-duplex media give each
    direction its own capacity. Half-duplex media (802.11 by default) share one
    channel between both directions, as CSMA/CA stations do.

    ``packet_loss_rate`` is the TCP-visible loss-event rate after link-layer
    retransmission. It removes the lost share of link capacity and, through the
    Mathis et al. ceiling, limits each TCP flow by its round-trip time.
    """

    link_id: str
    endpoint_a: str
    endpoint_b: str
    bandwidth_mib_s: float
    latency_ms: float
    packet_loss_rate: float = 0.0
    jitter_ms: float = 0.0
    medium: str = "ethernet"
    reverse_bandwidth_mib_s: float | None = None
    reverse_latency_ms: float | None = None
    duplex: str | None = None

    def __post_init__(self):
        if not self.link_id or not self.endpoint_a or not self.endpoint_b:
            raise ModelValidationError("network link id and endpoints must not be empty")
        if self.endpoint_a == self.endpoint_b:
            raise ModelValidationError(f"link '{self.link_id}' cannot connect an endpoint to itself")
        _positive("network.bandwidth_mib_s", self.bandwidth_mib_s)
        _non_negative("network.latency_ms", self.latency_ms)
        _fraction("network.packet_loss_rate", self.packet_loss_rate)
        if self.packet_loss_rate >= 1.0:
            raise ModelValidationError("network.packet_loss_rate must be < 1")
        _non_negative("network.jitter_ms", self.jitter_ms)
        if self.reverse_bandwidth_mib_s is not None:
            _positive("network.reverse_bandwidth_mib_s", self.reverse_bandwidth_mib_s)
        if self.reverse_latency_ms is not None:
            _non_negative("network.reverse_latency_ms", self.reverse_latency_ms)
        if self.duplex is not None and self.duplex not in {"full", "half"}:
            raise ModelValidationError(
                f"link '{self.link_id}' duplex must be 'full' or 'half'")

    @property
    def is_full_duplex(self) -> bool:
        if self.duplex is not None:
            return self.duplex == "full"
        return self.medium.lower() not in HALF_DUPLEX_MEDIA

    def bandwidth_in_direction(self, forward: bool) -> float:
        if forward or self.reverse_bandwidth_mib_s is None:
            return self.bandwidth_mib_s
        return self.reverse_bandwidth_mib_s

    def latency_in_direction_ms(self, forward: bool) -> float:
        if forward or self.reverse_latency_ms is None:
            return self.latency_ms
        return self.reverse_latency_ms

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "NetworkLinkSpec":
        endpoints = data["endpoints"]
        if len(endpoints) != 2:
            raise ModelValidationError("network link endpoints must contain exactly two ids")
        return cls(
            link_id=str(data["id"]),
            endpoint_a=str(endpoints[0]),
            endpoint_b=str(endpoints[1]),
            bandwidth_mib_s=float(data["bandwidth_mib_s"]),
            latency_ms=float(data.get("latency_ms", 0.0)),
            packet_loss_rate=float(data.get("packet_loss_rate", 0.0)),
            jitter_ms=float(data.get("jitter_ms", 0.0)),
            medium=str(data.get("medium", "ethernet")),
            reverse_bandwidth_mib_s=(
                float(data["reverse_bandwidth_mib_s"])
                if data.get("reverse_bandwidth_mib_s") is not None else None),
            reverse_latency_ms=(
                float(data["reverse_latency_ms"])
                if data.get("reverse_latency_ms") is not None else None),
            duplex=(str(data["duplex"]).lower()
                    if data.get("duplex") is not None else None),
        )


@dataclass(frozen=True)
class ClusterSpec:
    cluster_id: str
    nodes: tuple[NodeSpec, ...]
    network_vertices: frozenset[str]
    links: tuple[NetworkLinkSpec, ...]
    registry_endpoint: str | None = None
    family: str = "custom"
    description: str = ""
    calibration: CalibrationSpec = CalibrationSpec()
    schema_version: int = SCHEMA_VERSION
    # Orchestration latency between a task becoming eligible (arrival, all
    # parents finished, or a retry) and its Pod reaching the scheduler queue.
    control_plane_delay_s: float = 0.0
    # Latency between a task finishing and its node capacity becoming
    # placeable again (Kubernetes must first observe the Pod's completion).
    slot_release_delay_s: float = 0.0

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported cluster schema_version {self.schema_version}"
            )
        _non_negative("cluster.control_plane_delay_s", self.control_plane_delay_s)
        _non_negative("cluster.slot_release_delay_s", self.slot_release_delay_s)
        if not self.cluster_id or not self.nodes:
            raise ModelValidationError("cluster id and at least one node are required")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ModelValidationError("cluster node ids must be unique")
        link_ids = [link.link_id for link in self.links]
        if len(link_ids) != len(set(link_ids)):
            raise ModelValidationError("cluster network link ids must be unique")
        vertices = set(self.network_vertices) | set(node_ids)
        if self.registry_endpoint:
            vertices.add(self.registry_endpoint)
        for link in self.links:
            unknown = {link.endpoint_a, link.endpoint_b} - vertices
            if unknown:
                raise ModelValidationError(
                    f"link '{link.link_id}' references unknown endpoints: {sorted(unknown)}"
                )
        if len(self.nodes) > 1 and not self.links:
            raise ModelValidationError("multi-node clusters require network links")
        self._assert_nodes_connected(vertices)

    def _assert_nodes_connected(self, vertices: set[str]) -> None:
        adjacency = {vertex: set() for vertex in vertices}
        for link in self.links:
            adjacency[link.endpoint_a].add(link.endpoint_b)
            adjacency[link.endpoint_b].add(link.endpoint_a)
        start = self.nodes[0].node_id
        visited = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current] - visited:
                visited.add(neighbor)
                pending.append(neighbor)
        required = {node.node_id for node in self.nodes}
        if self.registry_endpoint:
            required.add(self.registry_endpoint)
        unreachable = required - visited
        if unreachable:
            raise ModelValidationError(
                f"cluster has network-unreachable required endpoints: {sorted(unreachable)}"
            )

    @property
    def nodes_by_id(self) -> dict[str, NodeSpec]:
        return {node.node_id: node for node in self.nodes}

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ClusterSpec":
        cluster = data.get("cluster", data)
        return cls(
            cluster_id=str(cluster["id"]),
            nodes=tuple(NodeSpec.from_mapping(node) for node in cluster["nodes"]),
            network_vertices=frozenset(str(v) for v in cluster.get(
                "network_vertices", ())),
            links=tuple(NetworkLinkSpec.from_mapping(link) for link in cluster.get(
                "links", ())),
            registry_endpoint=(str(cluster["registry_endpoint"])
                               if cluster.get("registry_endpoint") else None),
            family=str(cluster.get("family", "custom")),
            description=str(cluster.get("description", "")),
            calibration=CalibrationSpec.from_mapping(cluster.get("calibration")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            control_plane_delay_s=float(cluster.get("control_plane_delay_s", 0.0)),
            slot_release_delay_s=float(cluster.get("slot_release_delay_s", 0.0)),
        )


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_class: TaskClass
    image: str
    image_size_mib: float
    startup_s: float
    supported_architectures: frozenset[str]
    cpu_cores: float
    memory_mib: float
    working_set_mib: float
    compute_gflop: float
    memory_read_mib: float
    memory_write_mib: float
    disk_read_mib: float
    disk_write_mib: float
    random_io_fraction: float = 0.0
    io_block_kib: float = 64.0
    parallel_fraction: float = 0.0
    cache_reuse_fraction: float = 0.0
    output_mib: Mapping[str, float] = field(default_factory=dict)
    checkpointable: bool = False
    checkpoint_size_mib: float = 0.0
    checkpoint_interval_s: float = 30.0
    gang_group_id: str | None = None
    release_s: float = 0.0
    priority: PriorityClass | None = None
    preemptable: bool | None = None
    workflow_instance_id: str | None = None

    def __post_init__(self):
        if not self.task_id or not self.image or not self.supported_architectures:
            raise ModelValidationError(
                "task id, image, and at least one supported architecture are required"
            )
        _positive("task.cpu_cores", self.cpu_cores)
        _positive("task.memory_mib", self.memory_mib)
        for name in (
            "image_size_mib", "startup_s", "working_set_mib", "compute_gflop", "memory_read_mib",
            "memory_write_mib", "disk_read_mib", "disk_write_mib",
            "checkpoint_size_mib",
        ):
            _non_negative(f"task.{name}", getattr(self, name))
        _fraction("task.random_io_fraction", self.random_io_fraction)
        _fraction("task.parallel_fraction", self.parallel_fraction)
        _fraction("task.cache_reuse_fraction", self.cache_reuse_fraction)
        _positive("task.io_block_kib", self.io_block_kib)
        _positive("task.checkpoint_interval_s", self.checkpoint_interval_s)
        _non_negative("task.release_s", self.release_s)
        if self.working_set_mib > self.memory_mib:
            raise ModelValidationError(
                f"task '{self.task_id}' working set exceeds its memory request"
            )
        for output_name, size in self.output_mib.items():
            _non_negative(f"task.output_mib.{output_name}", float(size))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            task_id=str(data["id"]),
            task_class=_enum(TaskClass, data["class"], "task class"),
            image=str(data["image"]),
            image_size_mib=float(data.get("image_size_mib", 0.0)),
            startup_s=float(data.get("startup_s", 0.25)),
            supported_architectures=frozenset(str(v) for v in data.get(
                "supported_architectures", ("amd64",))),
            cpu_cores=float(data.get("cpu_cores", 1.0)),
            memory_mib=float(data["memory_mib"]),
            working_set_mib=float(data.get("working_set_mib", data["memory_mib"])),
            compute_gflop=float(data.get("compute_gflop", 0.0)),
            memory_read_mib=float(data.get("memory_read_mib", 0.0)),
            memory_write_mib=float(data.get("memory_write_mib", 0.0)),
            disk_read_mib=float(data.get("disk_read_mib", 0.0)),
            disk_write_mib=float(data.get("disk_write_mib", 0.0)),
            random_io_fraction=float(data.get("random_io_fraction", 0.0)),
            io_block_kib=float(data.get("io_block_kib", 64.0)),
            parallel_fraction=float(data.get("parallel_fraction", 0.0)),
            cache_reuse_fraction=float(data.get("cache_reuse_fraction", 0.0)),
            output_mib={str(k): float(v) for k, v in data.get(
                "output_mib", {}).items()},
            checkpointable=parse_bool(
                data.get("checkpointable", False), "task.checkpointable"),
            checkpoint_size_mib=float(data.get("checkpoint_size_mib", 0.0)),
            checkpoint_interval_s=float(data.get("checkpoint_interval_s", 30.0)),
            gang_group_id=(str(data["gang_group_id"])
                           if data.get("gang_group_id") else None),
            release_s=float(data.get("release_s", 0.0)),
            priority=(_enum(PriorityClass, data["priority"], "task priority")
                      if data.get("priority") is not None else None),
            preemptable=(parse_bool(data["preemptable"], "task.preemptable")
                         if data.get("preemptable") is not None else None),
            workflow_instance_id=(str(data["workflow_instance_id"])
                                  if data.get("workflow_instance_id") else None),
        )


@dataclass(frozen=True)
class DependencySpec:
    parent: str
    child: str
    data_mib: float = 0.0

    def __post_init__(self):
        if not self.parent or not self.child or self.parent == self.child:
            raise ModelValidationError("dependency endpoints must be distinct task ids")
        _non_negative("dependency.data_mib", self.data_mib)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DependencySpec":
        return cls(
            parent=str(data["parent"]),
            child=str(data["child"]),
            data_mib=float(data.get("data_mib", 0.0)),
        )


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    tasks: tuple[TaskSpec, ...]
    dependencies: tuple[DependencySpec, ...] = ()
    priority: PriorityClass = PriorityClass.BATCH
    preemptable: bool = True
    deadline_s: float | None = None
    family: str = "custom"
    tags: frozenset[str] = frozenset()
    description: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported workflow schema_version {self.schema_version}"
            )
        if not self.workflow_id or not self.tasks:
            raise ModelValidationError("workflow id and at least one task are required")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ModelValidationError("workflow task ids must be unique")
        known = set(task_ids)
        for dependency in self.dependencies:
            unknown = {dependency.parent, dependency.child} - known
            if unknown:
                raise ModelValidationError(
                    f"dependency references unknown tasks: {sorted(unknown)}"
                )
        self._assert_acyclic(known)
        if self.deadline_s is not None:
            _positive("workflow.deadline_s", self.deadline_s)

    def _assert_acyclic(self, task_ids: set[str]) -> None:
        children = {task_id: [] for task_id in task_ids}
        indegree = {task_id: 0 for task_id in task_ids}
        for dependency in self.dependencies:
            children[dependency.parent].append(dependency.child)
            indegree[dependency.child] += 1
        pending = [task_id for task_id, degree in indegree.items() if degree == 0]
        visited = 0
        while pending:
            current = pending.pop()
            visited += 1
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    pending.append(child)
        if visited != len(task_ids):
            raise ModelValidationError(f"workflow '{self.workflow_id}' contains a cycle")

    @property
    def tasks_by_id(self) -> dict[str, TaskSpec]:
        return {task.task_id: task for task in self.tasks}

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowSpec":
        workflow = data.get("workflow", data)
        return cls(
            workflow_id=str(workflow["id"]),
            tasks=tuple(TaskSpec.from_mapping(task) for task in workflow["tasks"]),
            dependencies=tuple(DependencySpec.from_mapping(dep) for dep in workflow.get(
                "dependencies", ())),
            priority=_enum(
                PriorityClass, workflow.get("priority", "BATCH"), "priority"),
            preemptable=parse_bool(
                workflow.get("preemptable", True), "workflow.preemptable"),
            deadline_s=(float(workflow["deadline_s"])
                        if workflow.get("deadline_s") is not None else None),
            family=str(workflow.get("family", "custom")),
            tags=frozenset(str(tag) for tag in workflow.get("tags", ())),
            description=str(workflow.get("description", "")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


def _load_yaml(path: str | Path) -> Mapping[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, Mapping):
        raise ModelValidationError(f"'{path}' must contain a YAML mapping")
    return data


def load_cluster(path: str | Path) -> ClusterSpec:
    return ClusterSpec.from_mapping(_load_yaml(path))


def load_workflow(path: str | Path) -> WorkflowSpec:
    return WorkflowSpec.from_mapping(_load_yaml(path))


def validate_task_compatibility(cluster: ClusterSpec, workflow: WorkflowSpec) -> None:
    """Ensure each task has at least one architecture- and capacity-compatible node."""
    for task in workflow.tasks:
        compatible = [
            node for node in cluster.nodes
            if node.cpu.architecture in task.supported_architectures
            and node.cpu.logical_cpus >= task.cpu_cores
            and node.memory.capacity_mib - node.background_memory_mib >= task.memory_mib
        ]
        if not compatible:
            raise ModelValidationError(
                f"task '{task.task_id}' has no architecture/capacity-compatible node "
                f"in cluster '{cluster.cluster_id}'"
            )