from models.enums import NodeType
from models.profile_store import ProfileStore
from models.workload import TaskInstance
from services.observer import ExecutionObserver


def test_observer_persists_thermal_and_load_context():
    store = ProfileStore()
    observer = ExecutionObserver(store)
    task = TaskInstance(
        task_instance_id="task-run-1",
        workflow_instance_id="workflow-1",
        task_template_id="task-template",
    )

    observer.record_task_completion(
        task,
        actual_runtime=3.5,
        actual_startup=0.4,
        node_id="node-a",
        node_type=NodeType.CPU_OPT,
        node_cpu_at_start=0.65,
        node_memory_at_start=0.4,
        temperature_at_start=72.0,
        temperature_at_end=78.5,
        io_bytes_read=1024,
        io_bytes_written=2048,
        output_bytes_by_field={"result": 4096},
        transfer_seconds=1.25,
    )

    observation = store.get_profile("task-template").metrics_by_node[
        "node-a"
    ].observations[0]
    assert observation.node_cpu_at_start == 0.65
    assert observation.node_memory_at_start == 0.4
    assert observation.temperature_at_start == 72.0
    assert observation.temperature_at_end == 78.5
    assert observation.io_bytes_read == 1024
    assert observation.io_bytes_written == 2048
    assert observation.output_bytes_by_field == {"result": 4096}
    assert observation.transfer_seconds == 1.25