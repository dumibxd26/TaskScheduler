"""Deterministic application-level simulator for heterogeneous DAG execution."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from math import inf
from types import MappingProxyType
from typing import Mapping, Protocol

from models.enums import PriorityClass
from simulation.core import EventQueue, SimulationClock
from simulation.resources import (
    EPSILON,
    CpuDemand,
    NetworkFlow,
    NetworkTopology,
    advance_temperature,
    allocate_bounded_rates,
    allocate_cpu_rates,
    allocate_network_rates,
    cache_miss_fraction,
    estimate_cpu_power_w,
    storage_rate_limit_mib_s,
)
from simulation.schema import (
    ClusterSpec,
    DependencySpec,
    NodeSpec,
    TaskSpec,
    WorkflowSpec,
    validate_task_compatibility,
)


class SimulationDeadlockError(RuntimeError):
    """Raised when unfinished work has no possible future event."""


class InvalidPlacementError(ValueError):
    """Raised when a policy returns an infeasible or malformed placement."""


class TaskPhase(str, Enum):
    WAITING = "waiting"
    READY = "ready"
    PREPARING = "preparing"
    STARTING = "starting"
    RUNNING = "running"
    FINISHED = "finished"


@dataclass(frozen=True)
class Placement:
    task_id: str
    node_id: str


@dataclass(frozen=True)
class NodeView:
    node: NodeSpec
    available: bool
    free_logical_cpus: float
    free_memory_mib: float
    temperature_c: float
    warm_images: frozenset[str]
    running_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskView:
    task: TaskSpec
    phase: TaskPhase
    ready_at_s: float | None
    assigned_node_id: str | None


@dataclass(frozen=True)
class SchedulerSnapshot:
    now_s: float
    cluster: ClusterSpec
    workflow: WorkflowSpec
    ready_tasks: tuple[TaskSpec, ...]
    nodes: Mapping[str, NodeView]
    tasks: Mapping[str, TaskView]


class SimulationPolicy(Protocol):
    name: str

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        """Return feasible placements for zero or more currently ready tasks."""


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    node_id: str
    ready_at_s: float
    placed_at_s: float
    started_at_s: float
    finished_at_s: float
    cold_start: bool
    attempts: int
    preemptions: int

    @property
    def queue_s(self) -> float:
        return self.placed_at_s - self.ready_at_s

    @property
    def preparation_s(self) -> float:
        return self.started_at_s - self.placed_at_s

    @property
    def execution_s(self) -> float:
        return self.finished_at_s - self.started_at_s


@dataclass(frozen=True)
class NodeResult:
    node_id: str
    energy_j: float
    final_temperature_c: float
    peak_temperature_c: float
    failures: int


@dataclass(frozen=True)
class SimulationResult:
    cluster_id: str
    workflow_id: str
    policy_name: str
    makespan_s: float
    network_transfer_mib: float
    cold_starts: int
    tasks: Mapping[str, TaskResult]
    nodes: Mapping[str, NodeResult]


@dataclass
class _TaskRuntime:
    spec: TaskSpec
    phase: TaskPhase = TaskPhase.WAITING
    ready_at_s: float | None = None
    placed_at_s: float | None = None
    started_at_s: float | None = None
    finished_at_s: float | None = None
    node_id: str | None = None
    cold_start: bool = False
    pending_preparations: int = 0
    remaining_compute_gflop: float = 0.0
    remaining_memory_mib: float = 0.0
    remaining_disk_read_mib: float = 0.0
    remaining_disk_write_mib: float = 0.0
    attempt: int = 0
    preemptions: int = 0
    checkpoint_state: tuple[float, float, float, float] | None = None

    def __post_init__(self):
        self.remaining_compute_gflop = self.spec.compute_gflop
        self.remaining_memory_mib = self.spec.memory_read_mib + self.spec.memory_write_mib
        self.remaining_disk_read_mib = self.spec.disk_read_mib
        self.remaining_disk_write_mib = self.spec.disk_write_mib


@dataclass
class _NodeRuntime:
    spec: NodeSpec
    temperature_c: float
    peak_temperature_c: float
    warm_images: set[str]
    reserved_logical_cpus: float = 0.0
    reserved_memory_mib: float = 0.0
    energy_j: float = 0.0
    available: bool = True
    failures: int = 0
    failure_epoch: int = 0


@dataclass
class _TransferRuntime:
    transfer_id: str
    kind: str
    source: str
    destination: str
    total_mib: float
    remaining_mib: float
    route: tuple[str, ...]
    round_trip_time_s: float
    waiters: set[str] = field(default_factory=set)
    image: str | None = None
    active: bool = False

    def flow(self) -> NetworkFlow:
        return NetworkFlow(
            flow_id=self.transfer_id,
            source=self.source,
            destination=self.destination,
            remaining_mib=self.remaining_mib,
            route=self.route,
            round_trip_time_s=self.round_trip_time_s,
        )


@dataclass(frozen=True)
class _Rates:
    network: Mapping[str, float]
    cpu: Mapping[str, float]
    memory: Mapping[str, float]
    disk_read: Mapping[str, float]
    disk_write: Mapping[str, float]


class Simulator:
    """Run one workflow with virtual time and dynamically shared resources."""

    def __init__(self, cluster: ClusterSpec, workflow: WorkflowSpec,
                 policy: SimulationPolicy, *, thermal_step_s: float = 0.5,
                 seed: int = 0):
        if thermal_step_s <= 0:
            raise ValueError("thermal_step_s must be > 0")
        validate_task_compatibility(cluster, workflow)
        self.cluster = cluster
        self.workflow = workflow
        self.policy = policy
        self.thermal_step_s = thermal_step_s
        self.random = random.Random(seed)
        self.clock = SimulationClock()
        self.events = EventQueue()
        self.topology = NetworkTopology(cluster)
        self.tasks = {
            task.task_id: _TaskRuntime(task) for task in workflow.tasks
        }
        self.nodes = {
            node.node_id: _NodeRuntime(
                spec=node,
                temperature_c=node.thermal.initial_c,
                peak_temperature_c=node.thermal.initial_c,
                warm_images=set(node.initial_warm_images),
            )
            for node in cluster.nodes
        }
        self.parents: dict[str, list[DependencySpec]] = {
            task.task_id: [] for task in workflow.tasks
        }
        self.children: dict[str, list[str]] = {
            task.task_id: [] for task in workflow.tasks
        }
        for dependency in workflow.dependencies:
            self.parents[dependency.child].append(dependency)
            self.children[dependency.parent].append(dependency.child)
        self.transfers: dict[str, _TransferRuntime] = {}
        self.image_transfers: dict[tuple[str, str], str] = {}
        self.network_transfer_mib = 0.0
        self._next_transfer_sequence = 0
        for task in workflow.tasks:
            if task.release_s > 0:
                self.events.schedule(task.release_s, "task_release", task.task_id)
        for node in cluster.nodes:
            for failure_time_s in node.failure_schedule_s:
                self.events.schedule(
                    failure_time_s, "node_failure_forced", node.node_id)
            self._schedule_stochastic_failure(node.node_id)

    def run(self, *, max_virtual_time_s: float = 365 * 24 * 3600) -> SimulationResult:
        for task_id, parents in self.parents.items():
            if not parents and self.tasks[task_id].spec.release_s <= 0:
                self._mark_ready(task_id)

        while not self._finished:
            self._schedule_ready_tasks()
            self._finish_zero_work_tasks()
            if self._finished:
                break

            rates = self._calculate_rates()
            next_timer_s = self.events.peek().time if len(self.events) else inf
            next_work_s = self._next_resource_event_s(rates)
            next_thermal_s = (
                self.clock.now + self.thermal_step_s
                if self._has_active_cpu_work else inf
            )
            next_time_s = min(next_timer_s, next_work_s, next_thermal_s)
            if next_time_s == inf:
                unfinished = [
                    task_id for task_id, task in self.tasks.items()
                    if task.phase != TaskPhase.FINISHED
                ]
                raise SimulationDeadlockError(
                    f"no event can advance unfinished tasks: {unfinished}"
                )
            if next_time_s > max_virtual_time_s:
                raise TimeoutError(
                    f"simulation exceeded {max_virtual_time_s} virtual seconds"
                )

            elapsed_s = max(0.0, next_time_s - self.clock.now)
            self._advance_resources(elapsed_s, rates)
            self.clock.advance_to(next_time_s)
            self._complete_resource_events()
            for event in list(self.events.drain_at(next_time_s)):
                self._handle_timer(event.kind, event.payload)

        return self._build_result()

    @property
    def _finished(self) -> bool:
        return all(task.phase == TaskPhase.FINISHED for task in self.tasks.values())

    @property
    def _has_active_cpu_work(self) -> bool:
        return any(
            task.phase == TaskPhase.RUNNING
            and task.remaining_compute_gflop > EPSILON
            for task in self.tasks.values()
        )

    def _mark_ready(self, task_id: str) -> None:
        task = self.tasks[task_id]
        task.phase = TaskPhase.READY
        task.ready_at_s = self.clock.now

    def _snapshot(self) -> SchedulerSnapshot:
        node_views = {}
        for node_id, node in self.nodes.items():
            running = tuple(
                task_id for task_id, task in self.tasks.items()
                if task.node_id == node_id and task.phase == TaskPhase.RUNNING
            )
            node_views[node_id] = NodeView(
                node=node.spec,
                available=node.available,
                free_logical_cpus=node.spec.cpu.logical_cpus - node.reserved_logical_cpus,
                free_memory_mib=(
                    node.spec.memory.capacity_mib
                    - node.spec.background_memory_mib
                    - node.reserved_memory_mib
                ),
                temperature_c=node.temperature_c,
                warm_images=frozenset(node.warm_images),
                running_task_ids=running,
            )
        task_views = {
            task_id: TaskView(
                task=task.spec,
                phase=task.phase,
                ready_at_s=task.ready_at_s,
                assigned_node_id=task.node_id,
            )
            for task_id, task in self.tasks.items()
        }
        ready = tuple(
            task.spec for task in self.tasks.values()
            if task.phase == TaskPhase.READY
        )
        return SchedulerSnapshot(
            now_s=self.clock.now,
            cluster=self.cluster,
            workflow=self.workflow,
            ready_tasks=ready,
            nodes=MappingProxyType(node_views),
            tasks=MappingProxyType(task_views),
        )

    def _schedule_ready_tasks(self) -> None:
        while True:
            snapshot = self._snapshot()
            if not snapshot.ready_tasks:
                return
            placements = self.policy.schedule(snapshot)
            placed_ids = {placement.task_id for placement in placements}
            gang_groups: dict[str, set[str]] = {}
            for task in self.workflow.tasks:
                if (task.gang_group_id
                        and self.tasks[task.task_id].phase != TaskPhase.FINISHED):
                    gang_groups.setdefault(task.gang_group_id, set()).add(task.task_id)
            for gang_id, members in gang_groups.items():
                touched = members & placed_ids
                if touched and touched != members:
                    raise InvalidPlacementError(
                        f"policy returned partial placement for gang '{gang_id}'"
                    )
                if touched and any(
                    self.tasks[task_id].phase != TaskPhase.READY for task_id in members
                ):
                    raise InvalidPlacementError(
                        f"policy placed gang '{gang_id}' before every member was ready"
                    )
            seen: set[str] = set()
            for placement in placements:
                if placement.task_id in seen:
                    raise InvalidPlacementError(
                        f"policy placed task '{placement.task_id}' more than once"
                    )
                seen.add(placement.task_id)
                self._place_task(placement)
            if not getattr(self.policy, "preemptive", False):
                return
            if not self._preempt_for_priority():
                return

    def _place_task(self, placement: Placement) -> None:
        if placement.task_id not in self.tasks:
            raise InvalidPlacementError(f"unknown task '{placement.task_id}'")
        if placement.node_id not in self.nodes:
            raise InvalidPlacementError(f"unknown node '{placement.node_id}'")
        task = self.tasks[placement.task_id]
        node = self.nodes[placement.node_id]
        if task.phase != TaskPhase.READY:
            raise InvalidPlacementError(
                f"task '{placement.task_id}' is not ready"
            )
        if node.spec.cpu.architecture not in task.spec.supported_architectures:
            raise InvalidPlacementError(
                f"task '{placement.task_id}' does not support node architecture"
            )
        if not node.available:
            raise InvalidPlacementError(
                f"task '{placement.task_id}' cannot use unavailable node '{placement.node_id}'"
            )
        free_cpus = node.spec.cpu.logical_cpus - node.reserved_logical_cpus
        free_memory = (
            node.spec.memory.capacity_mib
            - node.spec.background_memory_mib
            - node.reserved_memory_mib
        )
        if task.spec.cpu_cores > free_cpus + EPSILON:
            raise InvalidPlacementError(
                f"task '{placement.task_id}' exceeds free CPU on '{placement.node_id}'"
            )
        if task.spec.memory_mib > free_memory + EPSILON:
            raise InvalidPlacementError(
                f"task '{placement.task_id}' exceeds free memory on '{placement.node_id}'"
            )

        node.reserved_logical_cpus += task.spec.cpu_cores
        node.reserved_memory_mib += task.spec.memory_mib
        task.node_id = placement.node_id
        task.placed_at_s = self.clock.now
        task.phase = TaskPhase.PREPARING
        self._prepare_image(task)
        self._prepare_dependencies(task)
        if task.pending_preparations == 0:
            self._begin_startup(task)

    def _prepare_image(self, task: _TaskRuntime) -> None:
        node = self.nodes[task.node_id]
        if task.spec.image in node.warm_images:
            return
        task.cold_start = True
        key = (node.spec.node_id, task.spec.image)
        existing = self.image_transfers.get(key)
        if existing:
            self.transfers[existing].waiters.add(task.spec.task_id)
            task.pending_preparations += 1
            return
        if task.spec.image_size_mib <= EPSILON or not self.cluster.registry_endpoint:
            node.warm_images.add(task.spec.image)
            return
        task.pending_preparations += 1
        transfer_id = self._create_transfer(
            kind="image",
            source=self.cluster.registry_endpoint,
            destination=node.spec.node_id,
            size_mib=task.spec.image_size_mib,
            waiters={task.spec.task_id},
            image=task.spec.image,
        )
        self.image_transfers[key] = transfer_id

    def _prepare_dependencies(self, task: _TaskRuntime) -> None:
        for dependency in self.parents[task.spec.task_id]:
            parent = self.tasks[dependency.parent]
            if dependency.data_mib <= EPSILON or parent.node_id == task.node_id:
                continue
            task.pending_preparations += 1
            self._create_transfer(
                kind="dependency",
                source=parent.node_id,
                destination=task.node_id,
                size_mib=dependency.data_mib,
                waiters={task.spec.task_id},
            )

    def _create_transfer(self, *, kind: str, source: str, destination: str,
                         size_mib: float, waiters: set[str],
                         image: str | None = None) -> str:
        transfer_id = f"transfer-{self._next_transfer_sequence}"
        self._next_transfer_sequence += 1
        flow = self.topology.new_flow(
            transfer_id, source, destination, size_mib)
        latency_ms = sum(
            max(
                0.0,
                self.topology.links[link_id].latency_ms
                + self.random.uniform(
                    -self.topology.links[link_id].jitter_ms,
                    self.topology.links[link_id].jitter_ms,
                ),
            )
            for link_id in flow.route
        )
        propagation_s = latency_ms / 1000.0
        transfer = _TransferRuntime(
            transfer_id=transfer_id,
            kind=kind,
            source=source,
            destination=destination,
            total_mib=size_mib,
            remaining_mib=size_mib,
            route=flow.route,
            round_trip_time_s=2.0 * propagation_s,
            waiters=waiters,
            image=image,
        )
        self.transfers[transfer_id] = transfer
        self.network_transfer_mib += size_mib
        if propagation_s <= EPSILON:
            transfer.active = True
        else:
            self.events.schedule(
                self.clock.now + propagation_s, "transfer_activate", transfer_id)
        return transfer_id

    def _begin_startup(self, task: _TaskRuntime) -> None:
        task.phase = TaskPhase.STARTING
        if task.spec.startup_s <= EPSILON:
            self._start_execution(task)
        else:
            self.events.schedule(
                self.clock.now + task.spec.startup_s,
                "startup_complete",
                (task.spec.task_id, task.attempt),
            )

    def _start_execution(self, task: _TaskRuntime) -> None:
        task.phase = TaskPhase.RUNNING
        task.started_at_s = self.clock.now
        if task.spec.checkpointable:
            self.events.schedule(
                self.clock.now + task.spec.checkpoint_interval_s,
                "checkpoint",
                (task.spec.task_id, task.attempt),
            )

    def _calculate_rates(self) -> _Rates:
        active_flows = [
            transfer.flow() for transfer in self.transfers.values()
            if transfer.active and transfer.remaining_mib > EPSILON
            and self._endpoint_available(transfer.source)
            and self._endpoint_available(transfer.destination)
        ]
        network = allocate_network_rates(active_flows, self.topology.links)
        cpu: dict[str, float] = {}
        memory: dict[str, float] = {}
        disk_read: dict[str, float] = {}
        disk_write: dict[str, float] = {}

        for node_id, node in self.nodes.items():
            if not node.available:
                continue
            running = [
                task for task in self.tasks.values()
                if task.node_id == node_id and task.phase == TaskPhase.RUNNING
            ]
            cpu.update(allocate_cpu_rates(
                node.spec,
                [
                    CpuDemand(task.spec.task_id, task.spec.cpu_cores,
                              task.spec.parallel_fraction)
                    for task in running
                    if task.remaining_compute_gflop > EPSILON
                ],
                node.temperature_c,
            ))
            memory_ids = [
                task.spec.task_id for task in running
                if task.remaining_memory_mib > EPSILON
            ]
            dram_rates = allocate_bounded_rates(
                node.spec.memory.calibrated_bandwidth_mib_s,
                {task_id: node.spec.memory.calibrated_bandwidth_mib_s
                 for task_id in memory_ids},
            )
            cache_share_mib = (
                node.spec.cpu.l3_cache_mib / len(memory_ids)
                if memory_ids else node.spec.cpu.l3_cache_mib
            )
            for task_id, dram_rate in dram_rates.items():
                task = self.tasks[task_id].spec
                miss_fraction = cache_miss_fraction(
                    task.working_set_mib,
                    task.cache_reuse_fraction,
                    cache_share_mib,
                )
                memory[task_id] = dram_rate / miss_fraction
            read_limits = {
                task.spec.task_id: storage_rate_limit_mib_s(
                    node.spec.storage,
                    task.spec.random_io_fraction,
                    task.spec.io_block_kib,
                    write=False,
                )
                for task in running if task.remaining_disk_read_mib > EPSILON
            }
            write_limits = {
                task.spec.task_id: storage_rate_limit_mib_s(
                    node.spec.storage,
                    task.spec.random_io_fraction,
                    task.spec.io_block_kib,
                    write=True,
                )
                for task in running if task.remaining_disk_write_mib > EPSILON
            }
            disk_read.update(allocate_bounded_rates(
                node.spec.storage.sequential_read_mib_s, read_limits))
            disk_write.update(allocate_bounded_rates(
                node.spec.storage.sequential_write_mib_s, write_limits))

        return _Rates(network, cpu, memory, disk_read, disk_write)

    def _next_resource_event_s(self, rates: _Rates) -> float:
        durations: list[float] = []
        for transfer_id, rate in rates.network.items():
            if rate > EPSILON:
                durations.append(self.transfers[transfer_id].remaining_mib / rate)
        dimensions = (
            ("remaining_compute_gflop", rates.cpu),
            ("remaining_memory_mib", rates.memory),
            ("remaining_disk_read_mib", rates.disk_read),
            ("remaining_disk_write_mib", rates.disk_write),
        )
        for attribute, dimension_rates in dimensions:
            for task_id, rate in dimension_rates.items():
                if rate > EPSILON:
                    durations.append(getattr(self.tasks[task_id], attribute) / rate)
        return self.clock.now + min(durations) if durations else inf

    def _advance_resources(self, elapsed_s: float, rates: _Rates) -> None:
        if elapsed_s <= 0:
            return
        for transfer_id, rate in rates.network.items():
            transfer = self.transfers[transfer_id]
            transfer.remaining_mib = max(
                0.0, transfer.remaining_mib - rate * elapsed_s)
        dimensions = (
            ("remaining_compute_gflop", rates.cpu),
            ("remaining_memory_mib", rates.memory),
            ("remaining_disk_read_mib", rates.disk_read),
            ("remaining_disk_write_mib", rates.disk_write),
        )
        for attribute, dimension_rates in dimensions:
            for task_id, rate in dimension_rates.items():
                task = self.tasks[task_id]
                setattr(task, attribute, max(
                    0.0, getattr(task, attribute) - rate * elapsed_s))

        for node_id, node in self.nodes.items():
            active_cores = sum(
                task.spec.cpu_cores for task in self.tasks.values()
                if task.node_id == node_id
                and task.phase == TaskPhase.RUNNING
                and task.remaining_compute_gflop > EPSILON
            )
            power_w = (
                estimate_cpu_power_w(node.spec, active_cores)
                if node.available else 0.2 * node.spec.cpu.idle_power_w
            )
            node.energy_j += power_w * elapsed_s
            node.temperature_c = advance_temperature(
                node.temperature_c, power_w, node.spec.thermal, elapsed_s)
            node.peak_temperature_c = max(
                node.peak_temperature_c, node.temperature_c)

    def _complete_resource_events(self) -> None:
        completed_transfers = [
            transfer_id for transfer_id, transfer in self.transfers.items()
            if transfer.active and transfer.remaining_mib <= EPSILON
        ]
        for transfer_id in completed_transfers:
            self._complete_transfer(transfer_id)
        self._finish_zero_work_tasks()

    def _complete_transfer(self, transfer_id: str) -> None:
        transfer = self.transfers.pop(transfer_id)
        if transfer.kind == "image":
            self.nodes[transfer.destination].warm_images.add(transfer.image)
            self.image_transfers.pop((transfer.destination, transfer.image), None)
        for task_id in transfer.waiters:
            task = self.tasks[task_id]
            task.pending_preparations -= 1
            if task.pending_preparations == 0:
                self._begin_startup(task)

    def _finish_zero_work_tasks(self) -> None:
        completed = [
            task for task in self.tasks.values()
            if task.phase == TaskPhase.RUNNING
            and task.remaining_compute_gflop <= EPSILON
            and task.remaining_memory_mib <= EPSILON
            and task.remaining_disk_read_mib <= EPSILON
            and task.remaining_disk_write_mib <= EPSILON
        ]
        for task in completed:
            self._complete_task(task)

    def _complete_task(self, task: _TaskRuntime) -> None:
        node = self.nodes[task.node_id]
        node.reserved_logical_cpus -= task.spec.cpu_cores
        node.reserved_memory_mib -= task.spec.memory_mib
        task.phase = TaskPhase.FINISHED
        task.finished_at_s = self.clock.now
        for child_id in self.children[task.spec.task_id]:
            child = self.tasks[child_id]
            if child.phase == TaskPhase.WAITING and all(
                self.tasks[dependency.parent].phase == TaskPhase.FINISHED
                for dependency in self.parents[child_id]
            ) and child.spec.release_s <= self.clock.now:
                self._mark_ready(child_id)

    def _handle_timer(self, kind: str, payload) -> None:
        if kind == "transfer_activate":
            if payload in self.transfers:
                self.transfers[payload].active = True
            return
        if kind == "startup_complete":
            task_id, attempt = payload
            task = self.tasks[task_id]
            if task.phase == TaskPhase.STARTING and task.attempt == attempt:
                self._start_execution(task)
            return
        if kind == "checkpoint":
            task_id, attempt = payload
            task = self.tasks[task_id]
            if task.phase == TaskPhase.RUNNING and task.attempt == attempt:
                task.checkpoint_state = (
                    task.remaining_compute_gflop,
                    task.remaining_memory_mib,
                    task.remaining_disk_read_mib,
                    task.remaining_disk_write_mib,
                )
                task.remaining_disk_write_mib += task.spec.checkpoint_size_mib
                self.events.schedule(
                    self.clock.now + task.spec.checkpoint_interval_s,
                    "checkpoint",
                    (task_id, attempt),
                )
            return
        if kind == "task_release":
            task = self.tasks[payload]
            if task.phase == TaskPhase.WAITING and all(
                self.tasks[dependency.parent].phase == TaskPhase.FINISHED
                for dependency in self.parents[payload]
            ):
                self._mark_ready(payload)
            return
        if kind == "node_failure_forced":
            self._fail_node(payload)
            return
        if kind == "node_failure_stochastic":
            node_id, epoch = payload
            if self.nodes[node_id].failure_epoch == epoch:
                self._fail_node(node_id)
            return
        if kind == "node_repair":
            node = self.nodes[payload]
            if not node.available:
                node.available = True
                self._schedule_stochastic_failure(payload)
            return
        raise ValueError(f"unknown simulation timer kind '{kind}'")

    def _endpoint_available(self, endpoint: str) -> bool:
        return endpoint not in self.nodes or self.nodes[endpoint].available

    def _schedule_stochastic_failure(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.spec.failure_rate_per_hour <= 0:
            return
        rate_per_s = node.spec.failure_rate_per_hour / 3600.0
        delay_s = self.random.expovariate(rate_per_s)
        self.events.schedule(
            self.clock.now + delay_s,
            "node_failure_stochastic",
            (node_id, node.failure_epoch),
        )

    def _fail_node(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if not node.available:
            return
        node.available = False
        node.failures += 1
        node.failure_epoch += 1
        affected = [
            task for task in self.tasks.values()
            if task.node_id == node_id and task.phase != TaskPhase.FINISHED
        ]
        for task in affected:
            self._interrupt_task(task, preempted=False)
        self.events.schedule(
            self.clock.now + node.spec.mean_repair_s,
            "node_repair",
            node_id,
        )

    def _interrupt_task(self, task: _TaskRuntime, *, preempted: bool) -> None:
        for transfer_id, transfer in list(self.transfers.items()):
            if task.spec.task_id not in transfer.waiters:
                continue
            transfer.waiters.remove(task.spec.task_id)
            if not transfer.waiters:
                self.transfers.pop(transfer_id)
                if transfer.kind == "image":
                    self.image_transfers.pop(
                        (transfer.destination, transfer.image), None)
        node = self.nodes[task.node_id]
        node.reserved_logical_cpus -= task.spec.cpu_cores
        node.reserved_memory_mib -= task.spec.memory_mib
        if task.checkpoint_state is None:
            task.remaining_compute_gflop = task.spec.compute_gflop
            task.remaining_memory_mib = (
                task.spec.memory_read_mib + task.spec.memory_write_mib)
            task.remaining_disk_read_mib = task.spec.disk_read_mib
            task.remaining_disk_write_mib = task.spec.disk_write_mib
        else:
            (
                task.remaining_compute_gflop,
                task.remaining_memory_mib,
                task.remaining_disk_read_mib,
                task.remaining_disk_write_mib,
            ) = task.checkpoint_state
        task.attempt += 1
        if preempted:
            task.preemptions += 1
        task.pending_preparations = 0
        task.node_id = None
        task.placed_at_s = None
        task.started_at_s = None
        task.phase = TaskPhase.READY
        task.ready_at_s = self.clock.now

    def _task_priority(self, task: _TaskRuntime) -> PriorityClass:
        return task.spec.priority or self.workflow.priority

    def _task_preemptable(self, task: _TaskRuntime) -> bool:
        if task.spec.preemptable is not None:
            return task.spec.preemptable
        return self.workflow.preemptable

    def _preempt_for_priority(self) -> bool:
        ready = sorted(
            (task for task in self.tasks.values() if task.phase == TaskPhase.READY),
            key=lambda task: -self._task_priority(task).value,
        )
        for pending in ready:
            if pending.spec.gang_group_id:
                continue
            pending_priority = self._task_priority(pending)
            options = []
            for node_id, node in self.nodes.items():
                if (not node.available
                        or node.spec.cpu.architecture not in pending.spec.supported_architectures):
                    continue
                free_cpu = node.spec.cpu.logical_cpus - node.reserved_logical_cpus
                free_memory = (
                    node.spec.memory.capacity_mib
                    - node.spec.background_memory_mib
                    - node.reserved_memory_mib
                )
                if (pending.spec.cpu_cores <= free_cpu + EPSILON
                        and pending.spec.memory_mib <= free_memory + EPSILON):
                    continue
                victims = sorted(
                    (
                        task for task in self.tasks.values()
                        if task.node_id == node_id
                        and task.phase in {
                            TaskPhase.PREPARING, TaskPhase.STARTING, TaskPhase.RUNNING}
                        and not task.spec.gang_group_id
                        and self._task_preemptable(task)
                        and self._task_priority(task).value < pending_priority.value
                    ),
                    key=lambda task: (
                        self._task_priority(task).value,
                        0 if task.checkpoint_state is not None else 1,
                        task.spec.task_id,
                    ),
                )
                selected = []
                for victim in victims:
                    selected.append(victim)
                    free_cpu += victim.spec.cpu_cores
                    free_memory += victim.spec.memory_mib
                    if (pending.spec.cpu_cores <= free_cpu + EPSILON
                            and pending.spec.memory_mib <= free_memory + EPSILON):
                        options.append((len(selected), node_id, list(selected)))
                        break
            if options:
                _, _, victims = min(options, key=lambda option: (option[0], option[1]))
                for victim in victims:
                    self._interrupt_task(victim, preempted=True)
                return True
        return False

    def _build_result(self) -> SimulationResult:
        task_results = {
            task_id: TaskResult(
                task_id=task_id,
                node_id=task.node_id,
                ready_at_s=task.ready_at_s,
                placed_at_s=task.placed_at_s,
                started_at_s=task.started_at_s,
                finished_at_s=task.finished_at_s,
                cold_start=task.cold_start,
                attempts=task.attempt + 1,
                preemptions=task.preemptions,
            )
            for task_id, task in self.tasks.items()
        }
        node_results = {
            node_id: NodeResult(
                node_id=node_id,
                energy_j=node.energy_j,
                final_temperature_c=node.temperature_c,
                peak_temperature_c=node.peak_temperature_c,
                failures=node.failures,
            )
            for node_id, node in self.nodes.items()
        }
        policy_name = getattr(self.policy, "name", self.policy.__class__.__name__)
        if callable(policy_name):
            policy_name = policy_name()
        return SimulationResult(
            cluster_id=self.cluster.cluster_id,
            workflow_id=self.workflow.workflow_id,
            policy_name=str(policy_name),
            makespan_s=self.clock.now,
            network_transfer_mib=self.network_transfer_mib,
            cold_starts=sum(task.cold_start for task in self.tasks.values()),
            tasks=MappingProxyType(task_results),
            nodes=MappingProxyType(node_results),
        )