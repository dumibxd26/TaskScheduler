"""Slot release latency: simulator support and twin calibration."""

import pytest

from kind_twin_predict import derive_calibration, slot_handover_latencies
from simulation.engine import Simulator
from simulation.schema import ClusterSpec, ModelValidationError, WorkflowSpec
from tests.test_simulation_engine import AssignedPolicy, work
from tests.test_simulation_schema import node


def _one_slot_cluster(release_delay_s):
    single = node("solo")
    single["cpu"].update(physical_cores=1, threads_per_core=1,
                         calibrated_gflops_per_core=8.0)
    single["initial_warm_images"] = ["work:v1"]
    return ClusterSpec.from_mapping({"cluster": {
        "id": "one-slot",
        "slot_release_delay_s": release_delay_s,
        "nodes": [single],
        "links": [],
    }})


def _two_independent_tasks():
    return WorkflowSpec.from_mapping({"workflow": {
        "id": "two",
        "tasks": [work("first", compute=8), work("second", compute=8)],
        "dependencies": [],
    }})


def test_release_delay_keeps_the_slot_reserved_after_completion():
    workflow = _two_independent_tasks()
    policy = {"first": "solo", "second": "solo"}

    immediate = Simulator(_one_slot_cluster(0.0), workflow,
                          AssignedPolicy(policy), seed=0).run()
    delayed = Simulator(_one_slot_cluster(2.5), workflow,
                        AssignedPolicy(policy), seed=0).run()

    first_end = immediate.tasks["first"].finished_at_s
    assert immediate.tasks["second"].started_at_s == pytest.approx(first_end)
    assert delayed.tasks["second"].started_at_s == pytest.approx(first_end + 2.5)
    assert delayed.makespan_s == pytest.approx(immediate.makespan_s + 2.5)


def test_release_delay_must_not_be_negative():
    with pytest.raises(ModelValidationError):
        _one_slot_cluster(-1.0)


def _calibration_document():
    """Two warm-up waves on one node: binds at 10 s, then 2.5 s after ends."""
    tasks = []
    binds = []
    for index, (bound, finished) in enumerate(
            ((10.0, 20.0), (10.1, 21.0), (22.5, 30.0), (23.7, 31.0))):
        pod = f"cal-{index}"
        binds.append({"pod": pod, "node": "w1", "at": bound})
        tasks.append({
            "name": pod, "pod_name": pod, "node_name": "w1",
            "template": "experiment-cpu", "pod_created_at": None,
            "metrics": {"runtime_seconds": 3.0, "transfer_seconds": 0.0,
                        "started_at_epoch": bound + 1.0,
                        "finished_at_epoch": finished,
                        "emulation": {"speed_factor": 1.0}},
        })
    return {
        "calibration": {
            "workflows": [{"admitted_at": None, "tasks": tasks}],
            "scheduler_events": {"binds": binds},
        },
        "cluster": {"nodes": [{"name": "w1", "capacity_cpu": 1.0,
                               "bandwidth_mib_s_by_peer": {}}]},
    }


def test_handover_latency_uses_only_binds_that_reuse_a_freed_slot():
    document = _calibration_document()

    latencies = slot_handover_latencies(document["calibration"])

    # First-wave binds have no earlier finish on the node and are skipped.
    assert latencies == pytest.approx([1.5, 2.7])


def test_release_delay_is_applied_only_when_requested():
    document = _calibration_document()

    plain, diagnostics = derive_calibration(document)
    measured, _ = derive_calibration(document, slot_release=True)

    assert plain.slot_release_delay_s == 0.0
    assert measured.slot_release_delay_s == pytest.approx(2.1)
    assert diagnostics["slot_handover_latency_median_s"] == pytest.approx(2.1)
    assert diagnostics["slot_handovers"] == 2
