import pytest

from simulation.engine import Simulator
from simulation.policies import FCFSPolicy, RandomPolicy
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster, load_workflow
from tests.test_simulation_engine import work
from tests.test_simulation_schema import node


def test_checkpoint_restores_saved_work_after_deterministic_node_failure():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    node_data["failure_schedule_s"] = [5]
    node_data["mean_repair_s"] = 1
    task_data = work("long", compute=80)
    task_data.update({
        "checkpointable": True,
        "checkpoint_interval_s": 2,
        "checkpoint_size_mib": 0,
    })
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "failure", "nodes": [node_data]},
    })
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "retry", "tasks": [task_data]},
    })

    result = Simulator(cluster, workflow, FCFSPolicy()).run()

    assert result.nodes["only"].failures == 1
    assert result.tasks["long"].attempts == 2
    assert result.makespan_s == pytest.approx(12.0, rel=1e-3)


def test_node_failure_restarts_every_member_of_a_running_gang():
    first = node("first")
    second = node("second")
    for data in (first, second):
        data["background_cpu_fraction"] = 0
        data["initial_warm_images"] = ["work:v1"]
    first["failure_schedule_s"] = [5]
    first["mean_repair_s"] = 1
    cluster = ClusterSpec.from_mapping({
        "cluster": {
            "id": "gang-failure",
            "network_vertices": ["switch"],
            "nodes": [first, second],
            "links": [
                {"id": "first-link", "endpoints": ["first", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 0},
                {"id": "second-link", "endpoints": ["second", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 0},
            ],
        },
    })
    members = []
    for task_id in ("rank-0", "rank-1"):
        member = work(task_id, compute=64)
        member["cpu_cores"] = 8
        member["gang_group_id"] = "mpi"
        members.append(member)
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "gang", "tasks": members},
    })

    result = Simulator(cluster, workflow, FCFSPolicy()).run()

    survivor, failed = result.tasks["rank-1"], result.tasks["rank-0"]
    assert result.nodes["first"].failures == 1
    # The member on the healthy node is interrupted too and both restart
    # together once the failed node is repaired.
    assert failed.attempts == survivor.attempts == 2
    assert failed.started_at_s == pytest.approx(survivor.started_at_s)
    assert failed.started_at_s == pytest.approx(6.0)


def test_scenario_gang_recovers_after_failures(project_root):
    cluster = load_cluster(
        project_root / "scenarios" / "clusters" / "unreliable-thermal-3.yaml")
    workflow = load_workflow(
        project_root / "scenarios" / "workflows" / "gang-4.yaml")

    # Seed 27 fails a node while all four members are in flight.
    result = Simulator(
        cluster, workflow, RandomPolicy(seed=27), seed=27,
    ).run(max_virtual_time_s=300)

    assert result.makespan_s < 300
    attempts = {task.attempts for task in result.tasks.values()}
    placements = {round(task.placed_at_s, 9) for task in result.tasks.values()}
    assert attempts == {2}
    # Members are re-placed as one unit; execution starts may still differ
    # because image pulls and startup run per node.
    assert len(placements) == 1