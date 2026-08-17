import pytest

from simulation.resources import (
    CpuDemand,
    NetworkTopology,
    advance_temperature,
    allocate_cpu_rates,
    allocate_network_rates,
    cache_miss_fraction,
    effective_link_capacity_mib_s,
    thermal_throttle_factor,
)
from simulation.schema import ClusterSpec
from tests.test_simulation_schema import node


def cluster_mapping():
    return {
        "cluster": {
            "id": "routed",
            "network_vertices": ["switch"],
            "nodes": [node("a"), node("b"), node("c")],
            "links": [
                {"id": "a-sw", "endpoints": ["a", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
                {"id": "b-sw", "endpoints": ["b", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
                {"id": "c-sw", "endpoints": ["c", "switch"],
                 "bandwidth_mib_s": 50, "latency_ms": 2},
            ],
        },
    }


def test_routes_by_latency_and_applies_tcp_window_limit():
    cluster = ClusterSpec.from_mapping(cluster_mapping())
    topology = NetworkTopology(cluster)
    flow = topology.new_flow("a-b", "a", "b", 100, tcp_window_mib=0.1)

    assert flow.route == ("a-sw", "b-sw")
    assert flow.round_trip_time_s == pytest.approx(0.004)
    rates = allocate_network_rates([flow], topology.links)
    assert rates["a-b"] == pytest.approx(25.0)


def test_max_min_network_sharing_conserves_bottleneck_capacity():
    cluster = ClusterSpec.from_mapping(cluster_mapping())
    topology = NetworkTopology(cluster)
    flows = [
        topology.new_flow("a-b", "a", "b", 100),
        topology.new_flow("a-c", "a", "c", 100),
    ]

    rates = allocate_network_rates(flows, topology.links)
    a_capacity = effective_link_capacity_mib_s(topology.links["a-sw"])

    assert rates["a-b"] == pytest.approx(a_capacity / 2)
    assert rates["a-c"] == pytest.approx(a_capacity / 2)
    assert sum(rates.values()) == pytest.approx(a_capacity)


def test_rc_temperature_converges_toward_known_steady_state():
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "one", "nodes": [node("a")]}})
    thermal = cluster.nodes[0].thermal
    steady = thermal.ambient_c + 40.0 * thermal.thermal_resistance_c_per_w

    after_ten_constants = advance_temperature(
        thermal.initial_c, 40.0, thermal, 10 * thermal.time_constant_s)

    assert after_ten_constants == pytest.approx(steady, rel=1e-4)
    assert thermal_throttle_factor(thermal.throttle_c, thermal) == 1.0
    assert thermal_throttle_factor(thermal.shutdown_c, thermal) == pytest.approx(
        thermal.minimum_throttle_factor)


def test_cpu_rates_never_exceed_node_capacity_and_heat_reduces_rate():
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "one", "nodes": [node("a")]}})
    spec = cluster.nodes[0]
    demands = [
        CpuDemand("one", requested_cores=4, parallel_fraction=1.0),
        CpuDemand("two", requested_cores=4, parallel_fraction=1.0),
    ]

    cool = allocate_cpu_rates(spec, demands, temperature_c=60)
    hot = allocate_cpu_rates(spec, demands, temperature_c=spec.thermal.shutdown_c)
    capacity = (
        spec.cpu.calibrated_gflops_per_core
        * spec.cpu.physical_cores
        * (1.0 - spec.background_cpu_fraction)
    )

    assert sum(cool.values()) <= capacity + 1e-9
    assert sum(hot.values()) == pytest.approx(
        sum(cool.values()) * spec.thermal.minimum_throttle_factor)


def test_cache_reuse_reduces_dram_traffic_only_when_working_set_fits():
    fitting = cache_miss_fraction(working_set_mib=4, cache_reuse_fraction=0.9,
                                  available_cache_mib=8)
    oversized = cache_miss_fraction(working_set_mib=80, cache_reuse_fraction=0.9,
                                    available_cache_mib=8)

    assert fitting == pytest.approx(0.1)
    assert oversized == pytest.approx(0.91)