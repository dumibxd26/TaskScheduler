from pathlib import Path

from simulation.schema import load_cluster, load_workflow, validate_task_compatibility
from simulation.workloads import load_workload


ROOT = Path(__file__).parents[1] / "scenarios"


def test_all_cluster_scenarios_are_valid_and_provenanced():
    paths = sorted((ROOT / "clusters").glob("*.yaml"))

    assert len(paths) >= 4
    for path in paths:
        cluster = load_cluster(path)
        assert cluster.family != "custom"
        assert cluster.description
        assert cluster.calibration.status in {"estimated", "measured", "synthetic"}
        assert cluster.calibration.source != "unspecified"


def test_all_workflow_scenarios_are_valid_and_described():
    paths = sorted((ROOT / "workflows").glob("*.yaml"))

    assert len(paths) >= 15
    for path in paths:
        workflow = load_workflow(path)
        assert workflow.family != "custom"
        assert workflow.tags
        assert workflow.description


def test_every_workflow_has_compatible_capacity_in_every_cluster():
    clusters = [
        load_cluster(path) for path in sorted((ROOT / "clusters").glob("*.yaml"))
    ]
    workflows = [
        load_workflow(path) for path in sorted((ROOT / "workflows").glob("*.yaml"))
    ]

    for cluster in clusters:
        for workflow in workflows:
            validate_task_compatibility(cluster, workflow)


def test_all_multi_workflow_scenarios_compile():
    paths = sorted((ROOT / "workloads").glob("*.yaml"))

    assert len(paths) >= 2
    for path in paths:
        workload = load_workload(path)
        compiled = workload.compile()
        assert workload.description
        assert compiled.family == "multi-workflow"