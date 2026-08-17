import pytest

from simulation.engine import Simulator
from simulation.policies import EFTPolicy, FCFSPolicy, HEFTPolicy, RandomPolicy
from simulation.schema import ClusterSpec, WorkflowSpec
from tests.test_simulation_engine import work
from tests.test_simulation_schema import node


def heterogeneous_cluster():
    slow = node("slow")
    fast = node("fast")
    slow["background_cpu_fraction"] = 0
    fast["background_cpu_fraction"] = 0
    slow["cpu"]["calibrated_gflops_per_core"] = 4
    fast["cpu"]["calibrated_gflops_per_core"] = 16
    slow["initial_warm_images"] = ["work:v1"]
    fast["initial_warm_images"] = ["work:v1"]
    return ClusterSpec.from_mapping({
        "cluster": {
            "id": "heterogeneous",
            "network_vertices": ["switch"],
            "nodes": [slow, fast],
            "links": [
                {"id": "slow-link", "endpoints": ["slow", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
                {"id": "fast-link", "endpoints": ["fast", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
            ],
        },
    })


def one_task_workflow(compute=16):
    return WorkflowSpec.from_mapping({
        "workflow": {"id": "one", "tasks": [work("only", compute=compute)]},
    })


def test_eft_chooses_faster_node_than_fcfs():
    cluster = heterogeneous_cluster()
    workflow = one_task_workflow()

    fcfs = Simulator(cluster, workflow, FCFSPolicy()).run()
    eft = Simulator(cluster, workflow, EFTPolicy()).run()

    assert fcfs.tasks["only"].node_id == "slow"
    assert eft.tasks["only"].node_id == "fast"
    assert eft.makespan_s == pytest.approx(fcfs.makespan_s / 4, rel=1e-3)


def test_heft_keeps_large_dependency_local_when_transfer_dominates():
    cluster = heterogeneous_cluster()
    workflow = WorkflowSpec.from_mapping({
        "workflow": {
            "id": "data-chain",
            "tasks": [work("parent", compute=1), work("child", compute=1)],
            "dependencies": [
                {"parent": "parent", "child": "child", "data_mib": 10_000},
            ],
        },
    })

    result = Simulator(cluster, workflow, HEFTPolicy()).run()

    assert result.tasks["parent"].node_id == "fast"
    assert result.tasks["child"].node_id == "fast"
    assert result.network_transfer_mib == 0


def test_seeded_random_policy_is_reproducible():
    cluster = heterogeneous_cluster()
    workflow = one_task_workflow()

    first = Simulator(cluster, workflow, RandomPolicy(seed=42)).run()
    second = Simulator(cluster, workflow, RandomPolicy(seed=42)).run()

    assert first.tasks["only"].node_id == second.tasks["only"].node_id
    assert first.makespan_s == second.makespan_s


def test_fcfs_places_an_entire_gang_atomically_across_nodes():
    cluster = heterogeneous_cluster()
    members = [work(f"member-{index}", compute=8) for index in range(1, 4)]
    for member in members:
        member["cpu_cores"] = 4
        member["gang_group_id"] = "workers"
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "gang", "tasks": members},
    })

    result = Simulator(cluster, workflow, FCFSPolicy()).run()

    assert {task.placed_at_s for task in result.tasks.values()} == {0.0}
    assert {task.started_at_s for task in result.tasks.values()} == {0.0}
    assert len({task.node_id for task in result.tasks.values()}) == 2


def test_eft_spreads_io_contenders_across_identical_nodes():
    from pathlib import Path

    from simulation.schema import load_cluster, load_workflow

    root = Path(__file__).parents[1]
    cluster = load_cluster(root / "scenarios" / "clusters" / "homogeneous-4.yaml")
    workflow = load_workflow(root / "scenarios" / "workflows" / "io-heavy-6.yaml")

    result = Simulator(cluster, workflow, EFTPolicy()).run()

    assert len({task.node_id for task in result.tasks.values()}) == 4