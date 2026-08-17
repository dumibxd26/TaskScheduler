"""Calibratable fluid resource models for the discrete-event simulator.

The network allocator follows progressive max-min filling over routed links.
Thermal evolution uses a first-order resistance/capacitance model. CPU rates
respect calibrated per-core throughput, Amdahl scaling, SMT efficiency, and a
node-wide capacity ceiling.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from functools import lru_cache
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
EPSILON = 1e-9


@dataclass(frozen=True)
class NetworkFlow:
    flow_id: str
    source: str
    destination: str
    remaining_mib: float
    route: tuple[str, ...]
    round_trip_time_s: float
    tcp_window_mib: float = DEFAULT_TCP_WINDOW_MIB

    @property
    def tcp_window_rate_mib_s(self) -> float:
        if self.round_trip_time_s <= 0 or self.tcp_window_mib <= 0:
            return float("inf")
        return self.tcp_window_mib / self.round_trip_time_s


@dataclass(frozen=True)
class CpuDemand:
    execution_id: str
    requested_cores: float
    parallel_fraction: float


class NetworkTopology:
    """Shortest-latency routing over an explicit cluster link graph."""

    def __init__(self, cluster: ClusterSpec):
        self.links = {link.link_id: link for link in cluster.links}
        self._adjacency: dict[str, list[tuple[str, NetworkLinkSpec]]] = {}
        for link in cluster.links:
            self._adjacency.setdefault(link.endpoint_a, []).append((link.endpoint_b, link))
            self._adjacency.setdefault(link.endpoint_b, []).append((link.endpoint_a, link))

    @lru_cache(maxsize=None)
    def route(self, source: str, destination: str) -> tuple[str, ...]:
        if source == destination:
            return ()
        pending: list[tuple[float, str, tuple[str, ...]]] = [(0.0, source, ())]
        best = {source: 0.0}
        while pending:
            latency, current, path = heapq.heappop(pending)
            if current == destination:
                return path
            if latency > best.get(current, float("inf")):
                continue
            for neighbor, link in self._adjacency.get(current, ()):
                candidate = latency + link.latency_ms
                if candidate < best.get(neighbor, float("inf")):
                    best[neighbor] = candidate
                    heapq.heappush(
                        pending, (candidate, neighbor, path + (link.link_id,)))
        raise ValueError(f"no network route from '{source}' to '{destination}'")

    def one_way_latency_s(self, route: Iterable[str]) -> float:
        return sum(self.links[link_id].latency_ms for link_id in route) / 1000.0

    def new_flow(self, flow_id: str, source: str, destination: str,
                 size_mib: float, tcp_window_mib: float = DEFAULT_TCP_WINDOW_MIB) -> NetworkFlow:
        route = self.route(source, destination)
        latency = self.one_way_latency_s(route)
        return NetworkFlow(
            flow_id=flow_id,
            source=source,
            destination=destination,
            remaining_mib=float(size_mib),
            route=route,
            round_trip_time_s=2.0 * latency,
            tcp_window_mib=tcp_window_mib,
        )


def effective_link_capacity_mib_s(link: NetworkLinkSpec) -> float:
    """Application-level TCP payload capacity after headers and loss."""
    return (
        link.bandwidth_mib_s
        * PROTOCOL_EFFICIENCY
        * max(0.0, 1.0 - link.packet_loss_rate)
    )


def allocate_network_rates(
    flows: Iterable[NetworkFlow],
    links: Mapping[str, NetworkLinkSpec],
) -> dict[str, float]:
    """Allocate max-min fair rates under all route and TCP-window constraints."""
    flow_by_id = {flow.flow_id: flow for flow in flows if flow.remaining_mib > EPSILON}
    if not flow_by_id:
        return {}
    for flow in flow_by_id.values():
        if not flow.route:
            raise ValueError(f"network flow '{flow.flow_id}' has an empty route")

    rates = {flow_id: 0.0 for flow_id in flow_by_id}
    residual = {
        link_id: effective_link_capacity_mib_s(link)
        for link_id, link in links.items()
    }
    active = set(flow_by_id)

    while active:
        link_counts = {
            link_id: sum(link_id in flow_by_id[flow_id].route for flow_id in active)
            for link_id in links
        }
        increments = [
            residual[link_id] / count
            for link_id, count in link_counts.items()
            if count > 0
        ]
        increments.extend(
            flow_by_id[flow_id].tcp_window_rate_mib_s - rates[flow_id]
            for flow_id in active
        )
        delta = min(increments, default=0.0)
        if delta <= EPSILON:
            break

        for flow_id in active:
            rates[flow_id] += delta
        for link_id, count in link_counts.items():
            if count:
                residual[link_id] = max(0.0, residual[link_id] - delta * count)

        saturated_links = {
            link_id for link_id, count in link_counts.items()
            if count and residual[link_id] <= EPSILON
        }
        newly_frozen = {
            flow_id for flow_id in active
            if any(link_id in saturated_links for link_id in flow_by_id[flow_id].route)
            or rates[flow_id] >= flow_by_id[flow_id].tcp_window_rate_mib_s - EPSILON
        }
        if not newly_frozen:
            break
        active -= newly_frozen

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


def allocate_cpu_rates(node: NodeSpec, demands: Iterable[CpuDemand],
                       temperature_c: float) -> dict[str, float]:
    """Allocate GFLOP/s while respecting per-task scaling and node capacity."""
    demand_list = list(demands)
    if not demand_list:
        return {}
    throttle = thermal_throttle_factor(temperature_c, node.thermal)
    available_fraction = 1.0 - node.background_cpu_fraction
    node_capacity = (
        node.cpu.calibrated_gflops_per_core
        * node.cpu.physical_cores
        * available_fraction
        * throttle
    )
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


def cache_miss_fraction(working_set_mib: float, cache_reuse_fraction: float,
                        available_cache_mib: float) -> float:
    """Estimate DRAM traffic after reusable accesses served by the shared L3."""
    if working_set_mib <= EPSILON or cache_reuse_fraction <= 0:
        return 1.0
    cache_coverage = min(1.0, available_cache_mib / working_set_mib)
    return max(0.05, 1.0 - cache_reuse_fraction * cache_coverage)