from threading import Lock
from types import SimpleNamespace
import json

from controlplane.controller.main import (
    LABEL_TASK_NAME,
    LABEL_WORKFLOW,
    WorkflowController,
    _WorkflowState,
    _runtime_s,
)


def test_deleted_running_pod_is_reset_for_retry():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {
            "tasks": [{"name": "task-1", "template": "template-1"}],
        },
    })
    task = workflow.tasks["task-1"]
    task.state = "RUNNING"
    task.pod_name = "pod-1"
    task.node_name = "node-a"
    task.started_at = "2026-01-01T00:00:00Z"

    controller = WorkflowController.__new__(WorkflowController)
    controller._lock = Lock()
    controller.workflows = {workflow.uid: workflow}
    pod = SimpleNamespace(
        metadata=SimpleNamespace(labels={
            LABEL_WORKFLOW: "workflow-1",
            LABEL_TASK_NAME: "task-1",
        }),
    )

    controller._on_pod_event(pod, "DELETED")

    assert task.state == "WAITING"
    assert task.pod_name is None
    assert task.node_name is None
    assert task.started_at is None


def test_parent_fileserver_url_uses_routable_host_ip():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {
            "tasks": [
                {"name": "ingest", "template": "task-io"},
                {
                    "name": "stage",
                    "template": "task-mem",
                    "dependsOn": [{
                        "parent": "ingest",
                        "dependencyType": "DATA",
                        "dataFields": ["generated_file_path"],
                    }],
                },
            ],
        },
    })
    parent = workflow.tasks["ingest"]
    parent.state = "FINISHED"
    parent.pod_name = "ingest-pod"
    parent.node_name = "worker-a"
    parent.node_ip = "10.0.0.12"
    parent.outputs = {"generated_file_path": "generated_file.dat"}

    env = WorkflowController._gather_env(workflow.tasks["stage"], workflow)

    assert env["generated_file_path"] == "generated_file.dat"
    assert env["TS_PARENT_INGEST_FILESERVER_URL"] == (
        "http://10.0.0.12:8080/workflow-1/ingest-pod")


def test_runtime_handles_kubernetes_timestamp_offset():
    task = SimpleNamespace(
        started_at="2026-08-16T20:20:35+03:00",
        finished_at="2026-08-16T17:20:39.7283Z",
    )

    assert _runtime_s(task) == 4.7283


def test_workflow_policy_fields_are_forwarded_to_task_pod():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {
            "priority": "CRITICAL",
            "preemptable": False,
            "workflowClass": "BURST",
            "tasks": [{"name": "task-1", "template": "template-1"}],
        },
    })
    template = {
        "image": "task:v1",
        "taskClass": "CPU_BOUND",
        "cpuRequest": "0.5",
        "memoryRequest": "256Mi",
        "compatibleNodeTypes": ["CPU_OPT", "MEM_OPT"],
    }
    controller = WorkflowController.__new__(WorkflowController)

    pod = controller._build_pod(
        workflow, workflow.tasks["task-1"], template, {})

    assert pod.metadata.annotations["ts.scheduler/priority"] == "CRITICAL"
    assert pod.metadata.annotations["ts.scheduler/workflow_class"] == "BURST"
    assert pod.metadata.annotations["ts.scheduler/preemptable"] == "false"


def test_data_dependency_annotation_carries_parent_location_and_size():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {"tasks": [
            {"name": "ingest", "template": "template-io"},
            {
                "name": "stage",
                "template": "template-mem",
                "dependsOn": [{
                    "parent": "ingest",
                    "dependencyType": "DATA",
                    "dataFields": ["result"],
                }],
            },
        ]},
    })
    parent = workflow.tasks["ingest"]
    parent.state = "FINISHED"
    parent.node_name = "node-io"
    parent.outputs = {"bytes_written": 4096}
    controller = WorkflowController.__new__(WorkflowController)
    controller.template_cache = {}
    template = {
        "image": "task:v1",
        "taskClass": "MEMORY_BOUND",
        "cpuRequest": "0.5",
        "memoryRequest": "256Mi",
        "compatibleNodeTypes": ["CPU_OPT", "MEM_OPT", "IO_OPT"],
    }

    pod = controller._build_pod(
        workflow, workflow.tasks["stage"], template, {})

    dependencies = json.loads(
        pod.metadata.annotations["ts.scheduler/data_dependencies"])
    assert dependencies == [{
        "parent": "ingest", "node": "node-io", "bytes": 4096}]


def test_failed_parent_marks_waiting_descendants_failed():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {"tasks": [
            {"name": "source", "template": "template-1"},
            {
                "name": "child",
                "template": "template-1",
                "dependsOn": [{"parent": "source"}],
            },
            {
                "name": "grandchild",
                "template": "template-1",
                "dependsOn": [{"parent": "child"}],
            },
        ]},
    })
    workflow.tasks["source"].state = "FAILED"
    controller = WorkflowController.__new__(WorkflowController)

    controller._check_terminal(workflow)

    assert workflow.tasks["child"].state == "FAILED"
    assert workflow.tasks["grandchild"].state == "FAILED"
    assert workflow.state == "FAILED"