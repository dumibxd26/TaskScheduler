"""Side-effect-free runtime estimates used by simulation scheduling policies."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from simulation.engine import NodeView, SchedulerSnapshot
from simulation.resources import (
    EPSILON,
    CpuDemand,
    NetworkTopology,
    allocate_bounded_rates,
    allocate_cpu_rates,
    cache_miss_fraction,
    effective_link_capacity_mib_s,
    estimate_cpu_power_w,
    storage_rate_limit_mib_s,
)
from simulation.schema import NodeSpec, TaskSpec


@dataclass(frozen=True)
class TaskEstimate:
    preparation_s: float
    execution_s: float
    finish_s: float
    active_energy_j: float
    transfer_mib: float


def isolated_execution_s(task: TaskSpec, node: NodeSpec,
                         temperature_c: float | None = None) -> float:
    """Roofline-style maximum of independently calibrated resource ceilings."""
    temperature = node.thermal.initial_c if temperature_c is None else temperature_c
    cpu_rates = allocate_cpu_rates(
        node,
        [CpuDemand(task.task_id, task.cpu_cores, task.parallel_fraction)],
        temperature,
    )
    cpu_s = (
        task.compute_gflop / max(cpu_rates.get(task.task_id, 0.0), EPSILON)
        if task.compute_gflop > EPSILON else 0.0
    )
    memory_mib = task.memory_read_mib + task.memory_write_mib
    miss_fraction = cache_miss_fraction(
        task.working_set_mib, task.cache_reuse_fraction, node.cpu.l3_cache_mib)
    memory_s = (
        memory_mib * miss_fraction / node.memory.calibrated_bandwidth_mib_s)
    read_s = (
        task.disk_read_mib
        / storage_rate_limit_mib_s(
            node.storage, task.random_io_fraction, task.io_block_kib, write=False)
        if task.disk_read_mib > EPSILON else 0.0
    )
    write_s = (
        task.disk_write_mib
        / storage_rate_limit_mib_s(
            node.storage, task.random_io_fraction, task.io_block_kib, write=True)
        if task.disk_write_mib > EPSILON else 0.0
    )
    return max(cpu_s, memory_s, read_s, write_s)


def contended_execution_s(task: TaskSpec, node: NodeSpec,
                          temperature_c: float,
                          co_runners: tuple[TaskSpec, ...]) -> float:
    """Estimate a candidate's rates alongside current and tentative co-runners."""
    tasks = co_runners + (task,)
    cpu_rates = allocate_cpu_rates(
        node,
        [
            CpuDemand(item.task_id, item.cpu_cores, item.parallel_fraction)
            for item in tasks if item.compute_gflop > EPSILON
        ],
        temperature_c,
    )
    cpu_s = (
        task.compute_gflop / max(cpu_rates.get(task.task_id, 0.0), EPSILON)
        if task.compute_gflop > EPSILON else 0.0
    )
    memory_consumers = sum(
        item.memory_read_mib + item.memory_write_mib > EPSILON for item in tasks)
    dram_rate = (
        node.memory.calibrated_bandwidth_mib_s / memory_consumers
        if memory_consumers else node.memory.calibrated_bandwidth_mib_s
    )
    cache_share_mib = (
        node.cpu.l3_cache_mib / memory_consumers
        if memory_consumers else node.cpu.l3_cache_mib
    )
    miss_fraction = cache_miss_fraction(
        task.working_set_mib, task.cache_reuse_fraction, cache_share_mib)
    memory_rate = dram_rate / miss_fraction
    memory_s = (task.memory_read_mib + task.memory_write_mib) / memory_rate
    read_limits = {
        item.task_id: storage_rate_limit_mib_s(
            node.storage, item.random_io_fraction, item.io_block_kib, write=False)
        for item in tasks if item.disk_read_mib > EPSILON
    }
    write_limits = {
        item.task_id: storage_rate_limit_mib_s(
            node.storage, item.random_io_fraction, item.io_block_kib, write=True)
        for item in tasks if item.disk_write_mib > EPSILON
    }
    read_rates = allocate_bounded_rates(
        node.storage.sequential_read_mib_s, read_limits)
    write_rates = allocate_bounded_rates(
        node.storage.sequential_write_mib_s, write_limits)
    read_s = (
        task.disk_read_mib / max(read_rates.get(task.task_id, 0.0), EPSILON)
        if task.disk_read_mib > EPSILON else 0.0
    )
    write_s = (
        task.disk_write_mib / max(write_rates.get(task.task_id, 0.0), EPSILON)
        if task.disk_write_mib > EPSILON else 0.0
    )
    return max(cpu_s, memory_s, read_s, write_s)


def isolated_transfer_s(snapshot: SchedulerSnapshot, source: str,
                        destination: str, size_mib: float) -> float:
    if source == destination or size_mib <= EPSILON:
        return 0.0
    topology = NetworkTopology(snapshot.cluster)
    flow = topology.new_flow("estimate", source, destination, size_mib)
    link_rate = min(
        effective_link_capacity_mib_s(topology.links[link_id])
        for link_id in flow.route
    )
    rate = min(link_rate, flow.tcp_window_rate_mib_s)
    return topology.one_way_latency_s(flow.route) + size_mib / max(rate, EPSILON)


def estimate_task(snapshot: SchedulerSnapshot, task: TaskSpec,
                  node_view: NodeView,
                  co_runners: tuple[TaskSpec, ...] = ()) -> TaskEstimate:
    node = node_view.node
    transfers: list[tuple[float, float]] = []
    if (task.image not in node_view.warm_images
            and task.image_size_mib > EPSILON
            and snapshot.cluster.registry_endpoint):
        transfers.append((
            isolated_transfer_s(
                snapshot, snapshot.cluster.registry_endpoint,
                node.node_id, task.image_size_mib),
            task.image_size_mib,
        ))
    for dependency in snapshot.workflow.dependencies:
        if dependency.child != task.task_id or dependency.data_mib <= EPSILON:
            continue
        parent_node = snapshot.tasks[dependency.parent].assigned_node_id
        if parent_node and parent_node != node.node_id:
            transfers.append((
                isolated_transfer_s(
                    snapshot, parent_node, node.node_id, dependency.data_mib),
                dependency.data_mib,
            ))
    preparation_s = task.startup_s + max(
        (duration for duration, _ in transfers), default=0.0)
    execution_s = contended_execution_s(
        task, node, node_view.temperature_c, co_runners)
    active_power_w = estimate_cpu_power_w(node, task.cpu_cores)
    return TaskEstimate(
        preparation_s=preparation_s,
        execution_s=execution_s,
        finish_s=snapshot.now_s + preparation_s + execution_s,
        active_energy_j=active_power_w * execution_s,
        transfer_mib=sum(size for _, size in transfers),
    )


def average_execution_s(snapshot: SchedulerSnapshot, task: TaskSpec) -> float:
    compatible = [
        view.node for view in snapshot.nodes.values()
        if view.node.cpu.architecture in task.supported_architectures
        and view.node.cpu.logical_cpus >= task.cpu_cores
        and view.node.memory.capacity_mib - view.node.background_memory_mib >= task.memory_mib
    ]
    if not compatible:
        return inf
    return sum(isolated_execution_s(task, node) for node in compatible) / len(compatible)


def average_communication_s(snapshot: SchedulerSnapshot, size_mib: float) -> float:
    node_ids = list(snapshot.nodes)
    samples = [
        isolated_transfer_s(snapshot, source, destination, size_mib)
        for source in node_ids
        for destination in node_ids
        if source != destination
    ]
    return sum(samples) / len(samples) if samples else 0.0