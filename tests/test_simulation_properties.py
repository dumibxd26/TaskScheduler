import pytest
from hypothesis import given, settings, strategies as st

from simulation.engine import Simulator
from simulation.policies import RandomPolicy
from simulation.resources import (
    Hop,
    NetworkFlow,
    advance_temperature,
    allocate_bounded_rates,
    allocate_network_rates,
    effective_link_capacity_mib_s,
)
from simulation.schema import NetworkLinkSpec, ThermalSpec, load_cluster, load_workflow


@given(
    bandwidth=st.floats(min_value=0.1, max_value=10_000, allow_nan=False),
    packet_loss=st.floats(min_value=0, max_value=0.5, allow_nan=False),
    flow_count=st.integers(min_value=1, max_value=30),
)
@settings(max_examples=80, deadline=None)
def test_network_allocation_never_exceeds_shared_link_capacity(
    bandwidth, packet_loss, flow_count,
):
    link = NetworkLinkSpec(
        link_id="shared",
        endpoint_a="a",
        endpoint_b="b",
        bandwidth_mib_s=bandwidth,
        latency_ms=1,
        packet_loss_rate=packet_loss,
    )
    flows = [
        NetworkFlow(
            flow_id=f"flow-{index}",
            source="a",
            destination="b",
            remaining_mib=100,
            route=(Hop("shared", True),),
            channels=("shared:ab",),
            round_trip_time_s=0,
        )
        for index in range(flow_count)
    ]

    rates = allocate_network_rates(
        flows, {"shared:ab": effective_link_capacity_mib_s(link)})

    assert sum(rates.values()) <= effective_link_capacity_mib_s(link) + 1e-7
    assert max(rates.values()) - min(rates.values()) <= 1e-7


@given(
    capacity=st.floats(min_value=0, max_value=10_000, allow_nan=False),
    limits=st.lists(
        st.floats(min_value=0, max_value=2_000, allow_nan=False),
        min_size=1,
        max_size=20,
    ),
)
@settings(max_examples=80, deadline=None)
def test_bounded_allocation_respects_capacity_and_consumer_limits(capacity, limits):
    demand_limits = {f"demand-{index}": limit for index, limit in enumerate(limits)}

    rates = allocate_bounded_rates(capacity, demand_limits)

    assert sum(rates.values()) <= capacity + 1e-7
    for demand_id, rate in rates.items():
        assert 0 <= rate <= demand_limits[demand_id] + 1e-7
    if sum(limits) >= capacity:
        assert sum(rates.values()) == pytest.approx(capacity, abs=1e-7)


@given(
    current_c=st.floats(min_value=-20, max_value=180, allow_nan=False),
    power_w=st.floats(min_value=0, max_value=500, allow_nan=False),
    resistance=st.floats(min_value=0.01, max_value=5, allow_nan=False),
    capacitance=st.floats(min_value=1, max_value=1000, allow_nan=False),
    elapsed_s=st.floats(min_value=0, max_value=10_000, allow_nan=False),
)
@settings(max_examples=80, deadline=None)
def test_rc_temperature_stays_between_initial_and_steady_state(
    current_c, power_w, resistance, capacitance, elapsed_s,
):
    thermal = ThermalSpec(
        ambient_c=20,
        initial_c=30,
        throttle_c=90,
        shutdown_c=100,
        thermal_resistance_c_per_w=resistance,
        thermal_capacitance_j_per_c=capacitance,
    )
    steady_c = thermal.ambient_c + power_w * resistance

    result = advance_temperature(current_c, power_w, thermal, elapsed_s)

    assert min(current_c, steady_c) - 1e-9 <= result
    assert result <= max(current_c, steady_c) + 1e-9


def test_seed_replays_jitter_failures_placements_and_metrics(project_root):
    cluster = load_cluster(
        project_root / "scenarios" / "clusters" / "unreliable-thermal-3.yaml")
    workflow = load_workflow(
        project_root / "scenarios" / "workflows" / "diamond.yaml")

    first = Simulator(cluster, workflow, RandomPolicy(seed=17), seed=17).run()
    replay = Simulator(cluster, workflow, RandomPolicy(seed=17), seed=17).run()

    assert first == replay