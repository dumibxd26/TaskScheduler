"""Calibratable fluid resource models for the discrete-event simulator.

Network transfers are fluid TCP flows routed over a link graph. Each flow is
limited by progressive max-min filling over the channels it crosses, by its
receive window divided by the round-trip time, and by the Mathis et al.
(1997) loss/RTT ceiling ``MSS * sqrt(3/2) / (RTT * sqrt(p))``. Full-duplex
media expose one channel per direction; half-duplex media (802.11 by default)
share one channel between both directions. Thermal evolution uses a
first-order resistance/capacitance model. CPU rates respect calibrated
per-core throughput, Amdahl scaling, SMT efficiency, and a node-wide capacity
ceiling that includes the SMT gain.
"""

from __future__ import annotations

import heapq
import math
import weakref
from dataclasses import dataclass
from typing import Iterable, Mapping

from simulation.schema import (
    ClusterSpec,
    CpuSpec,
    NetworkLinkSpec,
    NodeSpec,
    StorageSpec,
    ThermalSpec,
)


PROTOCOL_EFFICIENCY = 0.97
DEFAULT_TCP_WINDOW_MIB = 4.0
TCP_MSS_BYTES = 1448.0
MATHIS_CONSTANT = math.sqrt(1.5)
MIB = 1024.0 * 1024.0
EPSILON = 1e-9


@dataclass(frozen=True)
class Hop:
    """One traversal of a link; ``forward`` means endpoint_a -> endpoint_b."""

    link_id: str
    forward: bool


def channel_id(link: NetworkLinkSpec, forward: bool) -> str:
    """Capacity resource used by a hop (shared for half-duplex media)."""
    if not link.is_full_duplex:
        return link.link_id
    return f"{link.link_id}:{'ab' if forward else 'ba'}"


def tcp_loss_rate_cap_mib_s(round_trip_time_s: float, loss_rate: float) -> float:
    """Mathis et al. steady-state TCP throughput bound for random loss."""
    if loss_rate <= 0 or round_trip_time_s <= 0:
        return float("inf")
    bytes_per_s = (
        TCP_MSS_BYTES * MATHIS_CONSTANT
        / (round_trip_time_s * math.sqrt(loss_rate))
    )
    return bytes_per_s / MIB


@dataclass(frozen=True)
class NetworkFlow:
    flow_id: str
    source: str
    destination: str
    remaining_mib: float
    route: tuple[Hop, ...]
    channels: tuple[str, ...]
    round_trip_time_s: float
    loss_rate: float = 0.0
    tcp_window_mib: float = DEFAULT_TCP_WINDOW_MIB

    @property
    def tcp_window_rate_mib_s(self) -> float:
        if self.round_trip_time_s <= 0 or self.tcp_window_mib <= 0:
            return float("inf")
        return self.tcp_window_mib / self.round_trip_time_s

    @property
    def loss_rate_cap_mib_s(self) -> float:
        return tcp_loss_rate_cap_mib_s(self.round_trip_time_s, self.loss_rate)

    @property
    def tcp_rate_cap_mib_s(self) -> float:
        return min(self.tcp_window_rate_mib_s, self.loss_rate_cap_mib_s)


@dataclass(frozen=True)
class CpuDemand:
    execution_id: str
    requested_cores: float
    parallel_fraction: float


class NetworkTopology:
    """Shortest-latency directional routing over an explicit link graph."""

    def __init__(self, cluster: ClusterSpec):
        self.links = {link.link_id: link for link in cluster.links}
        self._adjacency: dict[str, list[tuple[str, NetworkLinkSpec, bool]]] = {}
        for link in cluster.links:
            self._adjacency.setdefault(link.endpoint_a, []).append(
                (link.endpoint_b, link, True))
            self._adjacency.setdefault(link.endpoint_b, []).append(
                (link.endpoint_a, link, False))
        self.channel_capacities: dict[str, float] = {}
        for link in cluster.links:
            for forward in (True, False):
                channel = channel_id(link, forward)
                capacity = effective_link_capacity_mib_s(link, forward)
                previous = self.channel_capacities.get(channel)
                self.channel_capacities[channel] = (
                    capacity if previous is None else min(previous, capacity))
        self._routes: dict[tuple[str, str], tuple[Hop, ...]] = {}

    def route(self, source: str, destination: str) -> tuple[Hop, ...]:
        key = (source, destination)
        cached = self._routes.get(key)
        if cached is not None:
            return cached
        route = self._shortest_route(source, destination)
        self._routes[key] = route
        return route

    def _shortest_route(self, source: str, destination: str) -> tuple[Hop, ...]:
        if source == destination:
            return ()
        pending: list[tuple[float, int, str, tuple[Hop, ...]]] = [
            (0.0, 0, source, ())]
        best = {source: 0.0}
        sequence = 1
        while pending:
            latency, _, current, path = heapq.heappop(pending)
            if current == destination:
                return path
            if latency > best.get(current, float("inf")):
                continue
            for neighbor, link, forward in self._adjacency.get(current, ()):
                candidate = latency + link.latency_in_direction_ms(forward)
                if candidate < best.get(neighbor, float("inf")):
                    best[neighbor] = candidate
                    heapq.heappush(pending, (
                        candidate, sequence, neighbor,
                        path + (Hop(link.link_id, forward),)))
                    sequence += 1
        raise ValueError(f"no network route from '{source}' to '{destination}'")

    def one_way_latency_s(self, route: Iterable[Hop]) -> float:
        return sum(
            self.links[hop.link_id].latency_in_direction_ms(hop.forward)
            for hop in route
        ) / 1000.0

    def round_trip_time_s(self, source: str, destination: str,
                          forward_latency_s: float | None = None) -> float:
        forward = (
            self.one_way_latency_s(self.route(source, destination))
            if forward_latency_s is None else forward_latency_s
        )
        return forward + self.one_way_latency_s(self.route(destination, source))

    def channels(self, route: Iterable[Hop]) -> tuple[str, ...]:
        return tuple(
            channel_id(self.links[hop.link_id], hop.forward) for hop in route)

    def path_loss_rate(self, route: Iterable[Hop]) -> float:
        delivered = 1.0
        for hop in route:
            delivered *= 1.0 - self.links[hop.link_id].packet_loss_rate
        return 1.0 - delivered

    def bottleneck_capacity_mib_s(self, route: Iterable[Hop]) -> float:
        return min(
            (self.channel_capacities[channel] for channel in self.channels(route)),
            default=float("inf"),
        )

    def new_flow(self, flow_id: str, source: str, destination: str,
                 size_mib: float, tcp_window_mib: float = DEFAULT_TCP_WINDOW_MIB,
                 *, forward_latency_s: float | None = None) -> NetworkFlow:
        route = self.route(source, destination)
        return NetworkFlow(
            flow_id=flow_id,
            source=source,
            destination=destination,
            remaining_mib=float(size_mib),
            route=route,
            channels=self.channels(route),
            round_trip_time_s=self.round_trip_time_s(
                source, destination, forward_latency_s),
            loss_rate=self.path_loss_rate(route),
            tcp_window_mib=tcp_window_mib,
        )


def effective_link_capacity_mib_s(link: NetworkLinkSpec,
                                  forward: bool = True) -> float:
    """Application-level payload capacity after headers and lost packets."""
    return (
        link.bandwidth_in_direction(forward)
        * PROTOCOL_EFFICIENCY
        * max(0.0, 1.0 - link.packet_loss_rate)
    )


_TOPOLOGY_CACHE: dict[int, tuple["weakref.ref[ClusterSpec]", NetworkTopology]] = {}


def topology_for(cluster: ClusterSpec) -> NetworkTopology:
    """Reuse one routing table per immutable cluster object.

    Entries are keyed by object identity and dropped by a weakref callback, so
    sampled clusters do not accumulate for the life of the process.
    """
    key = id(cluster)
    entry = _TOPOLOGY_CACHE.get(key)
    if entry is not None and entry[0]() is cluster:
        return entry[1]
    topology = NetworkTopology(cluster)
    reference = weakref.ref(
        cluster, lambda _reference, key=key: _TOPOLOGY_CACHE.pop(key, None))
    _TOPOLOGY_CACHE[key] = (reference, topology)
    return topology


def allocate_network_rates(
    flows: Iterable[NetworkFlow],
    capacities: Mapping[str, float],
) -> dict[str, float]:
    """Allocate max-min fair rates under channel and per-flow TCP ceilings."""
    flow_by_id = {flow.flow_id: flow for flow in flows if flow.remaining_mib > EPSILON}
    if not flow_by_id:
        return {}
    for flow in flow_by_id.values():
        if not flow.channels:
            raise ValueError(f"network flow '{flow.flow_id}' has an empty route")
        unknown = [channel for channel in flow.channels if channel not in capacities]
        if unknown:
            raise ValueError(
                f"network flow '{flow.flow_id}' uses unknown channels {unknown}")

    rates = {flow_id: 0.0 for flow_id in flow_by_id}
    caps = {flow_id: flow.tcp_rate_cap_mib_s for flow_id, flow in flow_by_id.items()}
    residual = {
        channel: float(capacities[channel])
        for flow in flow_by_id.values() for channel in flow.channels
    }
    active = set(flow_by_id)

    while active:
        counts: dict[str, int] = {}
        for flow_id in active:
            for channel in flow_by_id[flow_id].channels:
                counts[channel] = counts.get(channel, 0) + 1
        increments = [residual[channel] / count for channel, count in counts.items()]
        increments.extend(caps[flow_id] - rates[flow_id] for flow_id in active)
        delta = max(0.0, min(increments))
        if delta > EPSILON:
            for flow_id in active:
                rates[flow_id] += delta
            for channel, count in counts.items():
                residual[channel] = max(0.0, residual[channel] - delta * count)
        saturated = {
            channel for channel, count in counts.items()
            if residual[channel] <= EPSILON
        }
        frozen = {
            flow_id for flow_id in active
            if any(channel in saturated for channel in flow_by_id[flow_id].channels)
            or rates[flow_id] >= caps[flow_id] - EPSILON
        }
        if not frozen:
            break
        active -= frozen

    return rates


def advance_temperature(current_c: float, power_w: float,
                        thermal: ThermalSpec, elapsed_s: float) -> float:
    """Advance a lumped first-order RC thermal model over ``elapsed_s``."""
    if elapsed_s <= 0:
        return current_c
    steady_state_c = thermal.ambient_c + power_w * thermal.thermal_resistance_c_per_w
    decay = math.exp(-elapsed_s / thermal.time_constant_s)
    return steady_state_c + (current_c - steady_state_c) * decay


def thermal_throttle_factor(temperature_c: float, thermal: ThermalSpec) -> float:
    """Return available compute capacity from 1.0 down to the configured floor."""
    if temperature_c <= thermal.throttle_c:
        return 1.0
    span = max(thermal.shutdown_c - thermal.throttle_c, EPSILON)
    position = min(1.0, (temperature_c - thermal.throttle_c) / span)
    return 1.0 - position * (1.0 - thermal.minimum_throttle_factor)


def estimate_cpu_power_w(node: NodeSpec, reserved_cores: float) -> float:
    """Estimate package power using an empirical convex utilization curve."""
    usable_cores = max(node.cpu.logical_cpus * (1.0 - node.background_cpu_fraction), EPSILON)
    utilization = min(1.0, max(0.0, reserved_cores / usable_cores))
    dynamic_fraction = utilization ** 1.4
    return node.cpu.idle_power_w + (
        node.cpu.max_power_w - node.cpu.idle_power_w
    ) * dynamic_fraction


def _amdahl_speedup(effective_cores: float, parallel_fraction: float) -> float:
    if effective_cores <= 1.0:
        return max(effective_cores, EPSILON)
    return 1.0 / ((1.0 - parallel_fraction) + parallel_fraction / effective_cores)


def _effective_task_cores(cpu: CpuSpec, requested_cores: float) -> float:
    physical = min(requested_cores, float(cpu.physical_cores))
    smt_threads = max(0.0, requested_cores - cpu.physical_cores)
    return physical + smt_threads * cpu.smt_efficiency


def effective_node_cores(cpu: CpuSpec) -> float:
    """Physical cores plus the throughput gained from SMT siblings."""
    smt_threads = max(0, cpu.logical_cpus - cpu.physical_cores)
    return cpu.physical_cores + smt_threads * cpu.smt_efficiency


def node_cpu_capacity_gflops(node: NodeSpec, throttle: float = 1.0) -> float:
    """Node-wide compute ceiling available to scheduled work."""
    return (
        node.cpu.calibrated_gflops_per_core
        * effective_node_cores(node.cpu)
        * (1.0 - node.background_cpu_fraction)
        * throttle
    )


def allocate_cpu_rates(node: NodeSpec, demands: Iterable[CpuDemand],
                       temperature_c: float) -> dict[str, float]:
    """Allocate GFLOP/s while respecting per-task scaling and node capacity."""
    demand_list = list(demands)
    if not demand_list:
        return {}
    throttle = thermal_throttle_factor(temperature_c, node.thermal)
    node_capacity = node_cpu_capacity_gflops(node, throttle)
    desired: dict[str, float] = {}
    for demand in demand_list:
        effective_cores = _effective_task_cores(node.cpu, demand.requested_cores)
        desired[demand.execution_id] = (
            node.cpu.calibrated_gflops_per_core
            * _amdahl_speedup(effective_cores, demand.parallel_fraction)
            * throttle
        )
    desired_total = sum(desired.values())
    if desired_total <= node_capacity + EPSILON:
        return desired
    scale = node_capacity / desired_total
    return {execution_id: rate * scale for execution_id, rate in desired.items()}


def equal_share_rates(capacity: float, execution_ids: Iterable[str]) -> dict[str, float]:
    ids = list(execution_ids)
    if not ids:
        return {}
    share = capacity / len(ids)
    return {execution_id: share for execution_id in ids}


def allocate_bounded_rates(capacity: float,
                           demand_limits: Mapping[str, float]) -> dict[str, float]:
    """Max-min allocation for one resource with per-consumer rate ceilings."""
    rates = {demand_id: 0.0 for demand_id in demand_limits}
    active = set(demand_limits)
    residual = max(0.0, capacity)
    while active and residual > EPSILON:
        share = residual / len(active)
        limited = {
            demand_id for demand_id in active
            if demand_limits[demand_id] - rates[demand_id] <= share + EPSILON
        }
        if not limited:
            for demand_id in active:
                rates[demand_id] += share
            break
        for demand_id in limited:
            increment = max(0.0, demand_limits[demand_id] - rates[demand_id])
            rates[demand_id] += increment
            residual -= increment
        active -= limited
    return rates


def storage_rate_limit_mib_s(storage: StorageSpec, random_io_fraction: float,
                             block_kib: float, *, write: bool) -> float:
    """Blend sequential throughput and random IOPS as time-per-MiB costs."""
    sequential = (
        storage.sequential_write_mib_s if write else storage.sequential_read_mib_s
    )
    random_iops = storage.random_write_iops if write else storage.random_read_iops
    random_throughput = random_iops * block_kib / 1024.0
    sequential_fraction = 1.0 - random_io_fraction
    seconds_per_mib = (
        sequential_fraction / sequential
        + random_io_fraction / max(random_throughput, EPSILON)
    )
    return 1.0 / max(seconds_per_mib, EPSILON)


def task_storage_limit_mib_s(storage: StorageSpec, random_io_fraction: float,
                             block_kib: float, *, write: bool) -> float:
    """Per-task storage ceiling: device mix limit and optional per-task cap."""
    limit = storage_rate_limit_mib_s(
        storage, random_io_fraction, block_kib, write=write)
    cap = storage.per_task_write_mib_s if write else storage.per_task_read_mib_s
    return limit if cap is None else min(limit, cap)


def cache_miss_fraction(working_set_mib: float, cache_reuse_fraction: float,
                        available_cache_mib: float) -> float:
    """Estimate DRAM traffic after reusable accesses served by the shared L3."""
    if working_set_mib <= EPSILON or cache_reuse_fraction <= 0:
        return 1.0
    cache_coverage = min(1.0, available_cache_mib / working_set_mib)
    return max(0.05, 1.0 - cache_reuse_fraction * cache_coverage)