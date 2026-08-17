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


def test_failed_gang_member_can_retry_after_peers_finish(project_root):
    cluster = load_cluster(
        project_root / "scenarios" / "clusters" / "unreliable-thermal-3.yaml")
    workflow = load_workflow(
        project_root / "scenarios" / "workflows" / "gang-4.yaml")

    result = Simulator(
        cluster, workflow, RandomPolicy(seed=15), seed=15,
    ).run(max_virtual_time_s=300)

    assert result.makespan_s < 300
    assert any(task.attempts > 1 for task in result.tasks.values())