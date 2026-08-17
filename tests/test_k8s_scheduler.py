from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace

from controlplane.scheduler.main import TsScheduler
from models.cluster import ClusterScenario, Node
from models.enums import (
    NodeType,
    PriorityClass,
    TaskState,
    WorkflowClass,
)
from models.profile_store import ProfileStore
from models.workload import TaskInstance, WorkflowInstance
from services.data_placement import DataPlacement
from services.observer import ExecutionObserver
from services.queue_manager import QueueManager


def test_completion_releases_capacity_and_updates_original_task():
    scheduler = TsScheduler.__new__(TsScheduler)
    scheduler.store = ProfileStore()
    scheduler.observer = ExecutionObserver(scheduler.store)
    scheduler.data_placement = DataPlacement()
    scheduler.core = SimpleNamespace(
        read_namespaced_pod_log=lambda **kwargs: (
            '__TS_METRICS__={"io_bytes_read": 1024, '
            '"io_bytes_written": 2048, "output_bytes_by_field": '
            '{"result": 4096}, "transfer_seconds": 1.25}')
    )
    scheduler.queue = QueueManager()
    scheduler._cluster_lock = Lock()
    scheduler._placement_context = {
        "pod-1": {
            "cpu_usage_ratio": 0.5,
            "memory_usage_ratio": 0.25,
            "cpu_temperature": 60.0,
        }
    }
    scheduler._dirty = False

    node = Node(
        node_id="node-a",
        node_type=NodeType.CPU_OPT,
        total_cpu=4,
        total_memory=4096,
        free_cpu=4,
        free_memory=4096,
        cpu_temperature=66.0,
    )
    node.register_task(
        "template-1", "pod-1", cpu_request=1, memory_request=512)
    scheduler._cluster = ClusterScenario(
        scenario_id="test",
        name="test",
        description="",
        nodes=[node],
    )

    task = TaskInstance(
        task_instance_id="pod-1",
        workflow_instance_id="workflow-1",
        task_template_id="template-1",
        state=TaskState.RUNNING,
        assigned_node_id="node-a",
    )
    workflow = WorkflowInstance(
        workflow_instance_id="workflow-1",
        workflow_template_id="workflow-1",
        workflow_class=WorkflowClass.BATCH,
        priority=PriorityClass.BATCH,
        preemptable=True,
        task_instances={"pod-1": task},
    )
    scheduler.queue.admitted_workflows["workflow-1"] = workflow

    started = datetime.now(timezone.utc)
    terminated = SimpleNamespace(
        started_at=started,
        finished_at=started + timedelta(seconds=3),
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="pod-1",
            annotations={
                "ts.scheduler/task_template_id": "template-1",
                "ts.scheduler/workflow_instance_id": "workflow-1",
                "ts.scheduler/task_name": "task-1",
            },
            labels={},
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase="Succeeded",
            start_time=started - timedelta(seconds=1),
            container_statuses=[SimpleNamespace(
                state=SimpleNamespace(terminated=terminated))],
        ),
    )

    scheduler._record_completion(pod)

    assert task.state == TaskState.FINISHED
    assert node.active_tasks == {}
    assert node.free_cpu == 4
    assert node.free_memory == 4096
    observation = scheduler.store.get_profile(
        "template-1").metrics_by_node["node-a"].observations[0]
    assert observation.runtime == 3
    assert observation.startup == 1
    assert observation.temperature_at_start == 60
    assert observation.temperature_at_end == 66
    assert observation.io_bytes_read == 1024
    assert observation.io_bytes_written == 2048
    assert observation.output_bytes_by_field == {"result": 4096}
    assert observation.transfer_seconds == 1.25
    assert scheduler.data_placement.get_output_size(
        "workflow-1", "task-1", "result") == 4096


def test_preempt_pod_clears_tracking_and_deletes_immediately():
    deleted = []
    scheduler = TsScheduler.__new__(TsScheduler)
    scheduler.core = SimpleNamespace(
        delete_namespaced_pod=lambda **kwargs: deleted.append(kwargs))
    scheduler._state_lock = Lock()
    scheduler._bound_pods = {"pod-1"}
    scheduler._enqueued_pods = {"pod-1"}
    scheduler._pending_pods = {"pod-1": object()}
    scheduler._placement_context = {"pod-1": {"cpu_temperature": 60.0}}

    accepted = scheduler.preempt_pod("pod-1")

    assert accepted is True
    assert deleted[0]["name"] == "pod-1"
    assert deleted[0]["grace_period_seconds"] == 0
    assert "pod-1" not in scheduler._bound_pods
    assert "pod-1" not in scheduler._enqueued_pods
    assert "pod-1" not in scheduler._pending_pods
    assert "pod-1" not in scheduler._placement_context


def test_pod_policy_fields_build_matching_workflow_model():
    scheduler = TsScheduler.__new__(TsScheduler)
    scheduler.queue = QueueManager()
    scheduler.templates = {}
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="pod-1",
            annotations={
                "ts.scheduler/task_template_id": "template-1",
                "ts.scheduler/task_class": "CPU_BOUND",
                "ts.scheduler/compatible_node_types": "CPU_OPT,MEM_OPT",
                "ts.scheduler/workflow_instance_id": "workflow-1",
                "ts.scheduler/priority": "CRITICAL",
                "ts.scheduler/workflow_class": "BURST",
                "ts.scheduler/preemptable": "false",
                "ts.scheduler/data_dependencies": (
                    '[{"parent":"ingest","node":"node-io",'
                    '"bytes":4096}]'),
            },
            labels={},
        ),
        spec=SimpleNamespace(containers=[SimpleNamespace(
            image="task:v1",
            resources=SimpleNamespace(requests={
                "cpu": "0.5", "memory": "256Mi"}),
        )]),
    )

    task, _, workflow_id, priority, workflow_class, preemptable = (
        scheduler._pod_to_models(pod))
    workflow = scheduler._ensure_workflow(
        workflow_id, priority, workflow_class, preemptable)

    assert workflow.priority == PriorityClass.CRITICAL
    assert workflow.workflow_class == WorkflowClass.BURST
    assert workflow.preemptable is False
    assert task.parent_node_ids == ["node-io"]
    assert task.input_bytes_by_node == {"node-io": 4096}