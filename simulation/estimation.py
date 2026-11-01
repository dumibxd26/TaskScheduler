"""Side-effect-free runtime estimates used by simulation scheduling policies."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import exp, inf
from typing import Sequence

from simulation.engine import NodeView, SchedulerSnapshot
from simulation.resources import (
    DEFAULT_TCP_WINDOW_MIB,
    EPSILON,
    CpuDemand,
    NetworkTopology,
    allocate_bounded_rates,
    allocate_cpu_rates,
    cache_miss_fraction,
    estimate_cpu_power_w,
    task_storage_limit_mib_s,
    tcp_loss_rate_cap_mib_s,
    thermal_throttle_factor,
    topology_for,
)
from simulation.schema import ClusterSpec, NodeSpec, TaskSpec


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
        memory_mib * miss_fraction / node.memory.task_bandwidth_limit_mib_s)
    read_s = (
        task.disk_read_mib
        / task_storage_limit_mib_s(
            node.storage, task.random_io_fraction, task.io_block_kib, write=False)
        if task.disk_read_mib > EPSILON else 0.0
    )
    write_s = (
        task.disk_write_mib
        / task_storage_limit_mib_s(
            node.storage, task.random_io_fraction, task.io_block_kib, write=True)
        if task.disk_write_mib > EPSILON else 0.0
    )
    return max(cpu_s, memory_s, read_s, write_s)


def contended_execution_s(task: TaskSpec, node: NodeSpec,
                          temperature_c: float,
                          co_runners: tuple[TaskSpec, ...]) -> float:
    """Estimate a candidate's rates alongside current and tentative co-runners."""
    return max(_contended_dimensions(task, node, temperature_c, co_runners))


def _contended_dimensions(task: TaskSpec, node: NodeSpec, temperature_c: float,
                          co_runners: tuple[TaskSpec, ...],
                          ) -> tuple[float, float, float, float]:
    """CPU, DRAM, read, and write durations under fair sharing."""
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
    dram_rate = min(
        node.memory.task_bandwidth_limit_mib_s,
        node.memory.calibrated_bandwidth_mib_s / memory_consumers
        if memory_consumers else node.memory.calibrated_bandwidth_mib_s,
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
        item.task_id: task_storage_limit_mib_s(
            node.storage, item.random_io_fraction, item.io_block_kib, write=False)
        for item in tasks if item.disk_read_mib > EPSILON
    }
    write_limits = {
        item.task_id: task_storage_limit_mib_s(
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
    return cpu_s, memory_s, read_s, write_s


def isolated_transfer_s(snapshot: SchedulerSnapshot, source: str,
                        destination: str, size_mib: float) -> float:
    return cluster_transfer_s(snapshot.cluster, source, destination, size_mib)


def cluster_transfer_s(cluster: ClusterSpec, source: str, destination: str,
                       size_mib: float) -> float:
    """Uncontended transfer time: propagation plus size at the TCP ceiling."""
    if source == destination or size_mib <= EPSILON:
        return 0.0
    topology = topology_for(cluster)
    route = topology.route(source, destination)
    rate = min(
        topology.bottleneck_capacity_mib_s(route),
        tcp_rate_ceiling_mib_s(topology, source, destination),
    )
    return topology.one_way_latency_s(route) + size_mib / max(rate, EPSILON)


def tcp_rate_ceiling_mib_s(topology: NetworkTopology, source: str,
                           destination: str) -> float:
    route = topology.route(source, destination)
    round_trip_time_s = topology.round_trip_time_s(source, destination)
    window_rate = (
        DEFAULT_TCP_WINDOW_MIB / round_trip_time_s
        if round_trip_time_s > 0 else float("inf")
    )
    return min(
        window_rate,
        tcp_loss_rate_cap_mib_s(
            round_trip_time_s, topology.path_loss_rate(route)),
    )


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

# ---------------------------------------------------------------------------
# Resource-aware estimates (HEFT-RA)
# ---------------------------------------------------------------------------

def contended_transfer_s(cluster: ClusterSpec, source: str, destination: str,
                         size_mib: float,
                         background: Sequence[tuple[str, str]] = ()) -> float:
    """Transfer time when sharing channels with ``background`` flows.

    First-order max-min estimate: on each channel of the route, the new flow
    receives ``capacity / (n + 1)`` where ``n`` background flows (in flight
    or already planned in this scheduling round) use that channel. The TCP
    window and Mathis loss ceilings still apply. Background flows that finish
    early are ignored, so the estimate errs on the slow side.
    """
    if source == destination or size_mib <= EPSILON:
        return 0.0
    topology = topology_for(cluster)
    route = topology.route(source, destination)
    channels = topology.channels(route)
    sharing: Counter = Counter()
    wanted = set(channels)
    for flow_source, flow_destination in background:
        if flow_source == flow_destination:
            continue
        for channel in topology.channels(
                topology.route(flow_source, flow_destination)):
            if channel in wanted:
                sharing[channel] += 1
    share = min(
        topology.channel_capacities[channel] / (sharing[channel] + 1)
        for channel in channels
    )
    rate = min(share, tcp_rate_ceiling_mib_s(topology, source, destination))
    return topology.one_way_latency_s(route) + size_mib / max(rate, EPSILON)


def thermal_aware_execution_s(task: TaskSpec, node: NodeSpec,
                              temperature_c: float,
                              co_runners: tuple[TaskSpec, ...],
                              steps: int = 64) -> float:
    """Execution time with CPU throttling predicted over the task's lifetime.

    The node is assumed to keep the candidate and its co-runners busy. Its
    temperature follows the first-order RC response toward the steady state of
    that power, and CPU progress is integrated with the throttle factor at
    each step. Memory and storage phases are unaffected by throttling.
    """
    cool_c = min(temperature_c, node.thermal.throttle_c)
    cpu_s, memory_s, read_s, write_s = _contended_dimensions(
        task, node, cool_c, co_runners)
    if task.compute_gflop <= EPSILON or cpu_s <= EPSILON:
        return max(cpu_s, memory_s, read_s, write_s)
    thermal = node.thermal
    busy_cores = task.cpu_cores + sum(
        runner.cpu_cores for runner in co_runners if runner.compute_gflop > EPSILON)
    power_w = estimate_cpu_power_w(node, busy_cores)
    steady_c = thermal.ambient_c + power_w * thermal.thermal_resistance_c_per_w
    tau = thermal.time_constant_s
    step = cpu_s / steps
    elapsed = 0.0
    done = 0.0
    while done < cpu_s - EPSILON:
        midpoint = elapsed + step / 2
        temperature = steady_c + (temperature_c - steady_c) * exp(-midpoint / tau)
        factor = max(thermal_throttle_factor(temperature, thermal), EPSILON)
        progress = min(step * factor, cpu_s - done)
        elapsed += progress / factor
        done += progress
    return max(elapsed, memory_s, read_s, write_s)


def expected_failure_delay_s(node: NodeSpec, task: TaskSpec,
                             duration_s: float) -> float:
    """Expected extra time from a Poisson node failure during the task."""
    rate_per_s = node.failure_rate_per_hour / 3600.0
    if rate_per_s <= 0 or duration_s <= 0:
        return 0.0
    probability = 1.0 - exp(-rate_per_s * duration_s)
    lost_work = (
        min(duration_s, task.checkpoint_interval_s) / 2.0
        if task.checkpointable else duration_s / 2.0
    )
    return probability * (lost_work + task.startup_s)


def resource_aware_estimate(snapshot: SchedulerSnapshot, task: TaskSpec,
                            node_view: NodeView,
                            co_runners: tuple[TaskSpec, ...] = (),
                            background: Sequence[tuple[str, str]] = (),
                            pulls: frozenset | set = frozenset(),
                            ) -> TaskEstimate:
    """EFT estimate with contention, thermal, and failure-risk corrections.

    ``pulls`` holds ``(node_id, image)`` pairs whose image download is already
    in flight or planned this round; the engine shares such a pull, so it is
    priced without adding a competing flow.
    """
    node = node_view.node
    inputs: list[tuple[str, float]] = []
    shared_pull = None
    if (task.image not in node_view.warm_images
            and task.image_size_mib > EPSILON
            and snapshot.cluster.registry_endpoint):
        if (node.node_id, task.image) in pulls:
            shared_pull = contended_transfer_s(
                snapshot.cluster, snapshot.cluster.registry_endpoint,
                node.node_id, task.image_size_mib,
                [flow for flow in background
                 if flow != (snapshot.cluster.registry_endpoint, node.node_id)])
        else:
            inputs.append((snapshot.cluster.registry_endpoint, task.image_size_mib))
    for dependency in snapshot.workflow.dependencies:
        if dependency.child != task.task_id or dependency.data_mib <= EPSILON:
            continue
        parent_node = snapshot.tasks[dependency.parent].assigned_node_id
        if parent_node and parent_node != node.node_id:
            inputs.append((parent_node, dependency.data_mib))
    flows = list(background)
    transfer_s = shared_pull or 0.0
    # A task's own inputs run concurrently and compete with each other too.
    for source, size_mib in inputs:
        transfer_s = max(transfer_s, contended_transfer_s(
            snapshot.cluster, source, node.node_id, size_mib, flows))
        flows.append((source, node.node_id))
    preparation_s = task.startup_s + transfer_s
    execution_s = thermal_aware_execution_s(
        task, node, node_view.temperature_c, co_runners)
    risk_s = expected_failure_delay_s(node, task, preparation_s + execution_s)
    return TaskEstimate(
        preparation_s=preparation_s,
        execution_s=execution_s,
        finish_s=snapshot.now_s + preparation_s + execution_s + risk_s,
        active_energy_j=estimate_cpu_power_w(node, task.cpu_cores) * execution_s,
        transfer_mib=sum(size for _, size in inputs),
    )


def planned_input_flows(snapshot: SchedulerSnapshot, task: TaskSpec,
                        node_view: NodeView,
                        pulls: frozenset | set = frozenset(),
                        ) -> list[tuple[str, str]]:
    """Flows a placement will start (dependency inputs and a new image pull)."""
    node_id = node_view.node.node_id
    flows = []
    if (task.image not in node_view.warm_images
            and task.image_size_mib > EPSILON
            and snapshot.cluster.registry_endpoint
            and (node_id, task.image) not in pulls):
        flows.append((snapshot.cluster.registry_endpoint, node_id))
    for dependency in snapshot.workflow.dependencies:
        if dependency.child != task.task_id or dependency.data_mib <= EPSILON:
            continue
        parent_node = snapshot.tasks[dependency.parent].assigned_node_id
        if parent_node and parent_node != node_id:
            flows.append((parent_node, node_id))
    return flows
