"""Tests for the resource-aware HEFT estimator and digital-twin model features."""

import pytest

from simulation.engine import Simulator
from simulation.estimation import (
    cluster_transfer_s,
    contended_execution_s,
    contended_transfer_s,
    expected_failure_delay_s,
    thermal_aware_execution_s,
)
from simulation.policies import create_policy
from simulation.schema import ClusterSpec, TaskSpec, WorkflowSpec, load_cluster, load_workflow
from tests.test_simulation_engine import AssignedPolicy, work
from tests.test_simulation_schema import node, task


def star(nodes, bandwidth=100.0):
    return ClusterSpec.from_mapping({
        "cluster": {
            "id": "star",
            "network_vertices": ["switch"],
            "nodes": nodes,
            "links": [
                {"id": f"{item['id']}-link", "endpoints": [item["id"], "switch"],
                 "bandwidth_mib_s": bandwidth, "latency_ms": 0}
                for item in nodes
            ],
        },
    })


def test_contended_transfer_shares_the_producer_uplink():
    cluster = star([node("a"), node("b"), node("c")])
    alone = contended_transfer_s(cluster, "a", "b", 97.0)
    shared = contended_transfer_s(cluster, "a", "b", 97.0, [("a", "c")] * 3)

    assert alone == pytest.approx(cluster_transfer_s(cluster, "a", "b", 97.0))
    assert alone == pytest.approx(1.0)
    assert shared == pytest.approx(4.0)
    # Flows on unrelated channels do not slow the transfer.
    assert contended_transfer_s(
        cluster, "a", "b", 97.0, [("c", "a")]) == pytest.approx(1.0)


def test_thermal_aware_estimate_anticipates_throttling():
    hot = node("hot")
    hot["background_cpu_fraction"] = 0
    hot["thermal"].update({
        "initial_c": 60, "throttle_c": 70, "shutdown_c": 90,
        "thermal_resistance_c_per_w": 2.0, "thermal_capacitance_j_per_c": 5,
        "minimum_throttle_factor": 0.4,
    })
    spec = ClusterSpec.from_mapping({"cluster": {"id": "h", "nodes": [hot]}}).nodes[0]
    heavy = TaskSpec.from_mapping({**work("heavy", compute=400), "cpu_cores": 8,
                                   "parallel_fraction": 1.0})

    naive = contended_execution_s(heavy, spec, 60, ())
    predictive = thermal_aware_execution_s(heavy, spec, 60, ())
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "h", "nodes": [hot]}})
    actual = Simulator(
        cluster,
        WorkflowSpec(workflow_id="w", tasks=(heavy,)),
        AssignedPolicy({"heavy": "hot"}),
        thermal_step_s=0.05,
    ).run().tasks["heavy"].execution_s

    assert predictive > naive * 1.2
    assert predictive == pytest.approx(actual, rel=0.05)


def test_expected_failure_delay_grows_with_rate_and_duration():
    reliable = ClusterSpec.from_mapping({"cluster": {"id": "r", "nodes": [node("r")]}}).nodes[0]
    flaky_data = node("f")
    flaky_data["failure_rate_per_hour"] = 36
    flaky = ClusterSpec.from_mapping({"cluster": {"id": "f", "nodes": [flaky_data]}}).nodes[0]
    spec = TaskSpec.from_mapping(task("t"))

    assert expected_failure_delay_s(reliable, spec, 100) == 0.0
    short = expected_failure_delay_s(flaky, spec, 10)
    long = expected_failure_delay_s(flaky, spec, 100)
    assert 0 < short < long
    # 36/h over 100 s: P = 1 - exp(-1) and the expected lost work is 50 s.
    assert long == pytest.approx((1 - 2.718281828 ** -1) * (50 + spec.startup_s), rel=1e-6)


def test_heft_ra_keeps_fanout_children_near_their_large_input(project_root):
    cluster = load_cluster(
        project_root / "scenarios" / "clusters" / "balanced-heterogeneous-4.yaml")
    workflow = load_workflow(
        project_root / "scenarios" / "workflows" / "fanout-1to8.yaml")

    heft = Simulator(cluster, workflow, create_policy("heft"), seed=0).run()
    aware = Simulator(cluster, workflow, create_policy("heft-ra"), seed=0).run()

    # Classic HEFT ships eight 1 GiB inputs through one producer uplink.
    assert heft.network_transfer_mib > 8000
    assert aware.makespan_s < heft.makespan_s * 0.5
    assert aware.network_transfer_mib < heft.network_transfer_mib / 4


def test_per_task_caps_limit_a_single_pod_but_not_the_node():
    data = node("capped")
    data["background_cpu_fraction"] = 0
    data["initial_warm_images"] = ["work:v1"]
    data["memory"]["per_task_bandwidth_mib_s"] = 1000
    data["storage"]["per_task_write_mib_s"] = 50
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "c", "nodes": [data]}})
    memory_task = {**work("mem", compute=0), "memory_read_mib": 1000,
                   "working_set_mib": 512, "memory_mib": 1024}
    writer = {**work("io", compute=0), "disk_write_mib": 100}
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "caps", "tasks": [memory_task, writer]}})

    result = Simulator(
        cluster, workflow, AssignedPolicy({"mem": "capped", "io": "capped"})).run()

    assert result.tasks["mem"].execution_s == pytest.approx(1.0, rel=1e-3)
    assert result.tasks["io"].execution_s == pytest.approx(2.0, rel=1e-3)


def test_control_plane_delay_postpones_roots_children_and_retries():
    data = node("only")
    data["background_cpu_fraction"] = 0
    data["initial_warm_images"] = ["work:v1"]
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "cp", "control_plane_delay_s": 1.5, "nodes": [data]}})
    workflow = WorkflowSpec.from_mapping({
        "workflow": {
            "id": "chain",
            "tasks": [work("first"), work("second")],
            "dependencies": [{"parent": "first", "child": "second"}],
        },
    })

    result = Simulator(
        cluster, workflow, AssignedPolicy({"first": "only", "second": "only"})).run()

    assert result.tasks["first"].ready_at_s == pytest.approx(1.5)
    assert result.tasks["second"].ready_at_s == pytest.approx(
        result.tasks["first"].finished_at_s + 1.5)
    assert result.makespan_s == pytest.approx(1.5 + 1.0 + 1.5 + 1.0)


def _slot_node(node_id, cores, gflops, read_mib_s):
    data = node(node_id)
    data["cpu"].update(physical_cores=cores, threads_per_core=1,
                       calibrated_gflops_per_core=gflops)
    data["storage"].update(sequential_read_mib_s=read_mib_s)
    return data


def _slot_limited_fanout():
    cluster = star([
        _slot_node("fast", 2, 16.0, 500),
        _slot_node("disk", 2, 4.0, 5000),
        _slot_node("spare-1", 1, 4.0, 500),
        _slot_node("spare-2", 1, 4.0, 500),
    ])
    source = work("source", compute=0)
    source.update({"class": "IO_BOUND", "disk_read_mib": 1000})
    tasks = [work("blocker", compute=160), source] + [
        work(f"child-{index}", compute=48) for index in (1, 2, 3)]
    workflow = WorkflowSpec.from_mapping({"workflow": {
        "id": "slot-limited-fanout",
        "tasks": tasks,
        "dependencies": [
            {"parent": "source", "child": f"child-{index}", "data_mib": 200}
            for index in (1, 2, 3)
        ],
    }})
    return cluster, workflow


def test_capacity_aware_lookahead_does_not_count_on_taken_slots():
    # The long blocker takes one of fast's two slots first. The slot-blind
    # lookahead still co-locates the source with all three children on fast,
    # so two children must fetch their input remotely and run on slow disk.
    cluster, workflow = _slot_limited_fanout()

    blind = Simulator(cluster, workflow, create_policy("heft-ra"), seed=0).run()
    aware = Simulator(cluster, workflow, create_policy("heft-ra-cap"),
                      seed=0).run()

    assert blind.tasks["source"].node_id == "fast"
    assert aware.tasks["source"].node_id == "disk"
    assert aware.makespan_s < blind.makespan_s * 0.75


def test_capacity_aware_lookahead_matches_heft_ra_on_wide_nodes(project_root):
    cluster = load_cluster(
        project_root / "scenarios" / "clusters" / "balanced-heterogeneous-4.yaml")
    workflow = load_workflow(
        project_root / "scenarios" / "workflows" / "fanout-1to8.yaml")

    blind = Simulator(cluster, workflow, create_policy("heft-ra"), seed=0).run()
    aware = Simulator(cluster, workflow, create_policy("heft-ra-cap"),
                      seed=0).run()

    assert aware.makespan_s == pytest.approx(blind.makespan_s)
    assert {task_id: task.node_id for task_id, task in aware.tasks.items()} == {
        task_id: task.node_id for task_id, task in blind.tasks.items()}
