import pytest

from simulation.characterization import characterize_workflow
from simulation.schema import load_workflow


def test_characterizes_independent_and_linear_workflows():
    wide = characterize_workflow(
        load_workflow("scenarios/workflows/wide-12.yaml"))
    deep = characterize_workflow(
        load_workflow("scenarios/workflows/deep-10.yaml"))

    assert wide.topology == "independent"
    assert wide.critical_path_tasks == 1
    assert wide.max_parallelism == 12
    assert deep.topology == "linear"
    assert deep.critical_path_tasks == 10
    assert deep.max_parallelism == 1


def test_characterizes_mixed_task_classes_and_dag_data():
    features = characterize_workflow(
        load_workflow("scenarios/workflows/mixed-etl.yaml"))

    assert sum(dict(features.task_class_fractions).values()) == pytest.approx(1.0)
    assert features.task_class_fraction("CPU_BOUND") > 0
    assert features.task_class_fraction("MEMORY_BOUND") > 0
    assert features.task_class_fraction("IO_BOUND") > 0
    assert features.dependency_count > 0
    assert features.data_dependency_mib > 0