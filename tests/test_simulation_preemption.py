import pytest

from simulation.engine import Simulator
from simulation.policies import AdaptivePolicy, FCFSPolicy
from simulation.schema import ClusterSpec, WorkflowSpec
from tests.test_simulation_engine import work
from tests.test_simulation_schema import node


def priority_contention_case():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    low = work("low", compute=416)
    low.update({
        "cpu_cores": 8,
        "parallel_fraction": 1,
        "priority": "BATCH",
        "preemptable": True,
        "checkpointable": True,
        "checkpoint_interval_s": 1,
        "checkpoint_size_mib": 0,
    })
    urgent = work("urgent", compute=41.6)
    urgent.update({
        "cpu_cores": 8,
        "parallel_fraction": 1,
        "priority": "CRITICAL",
        "preemptable": False,
        "release_s": 2.1,
    })
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "priority", "nodes": [node_data]},
    })
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "contention", "tasks": [low, urgent]},
    })
    return cluster, workflow


def test_adaptive_preempts_checkpointable_batch_for_critical_arrival():
    cluster, workflow = priority_contention_case()

    adaptive = Simulator(cluster, workflow, AdaptivePolicy()).run()
    fcfs = Simulator(cluster, workflow, FCFSPolicy()).run()

    assert adaptive.tasks["urgent"].started_at_s == pytest.approx(2.1)
    assert adaptive.tasks["low"].preemptions == 1
    assert adaptive.tasks["low"].attempts == 2
    assert fcfs.tasks["urgent"].started_at_s > adaptive.tasks["urgent"].finished_at_s