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


def test_output_extraction_retries_a_transient_empty_log():
    class CoreApi:
        def __init__(self):
            self.logs = ["task still flushing", '__TS_OUTPUT__={"result": "ready"}']

        def read_namespaced_pod_log(self, *, name, namespace):
            assert name == "pod-1"
            assert namespace == "default"
            return self.logs.pop(0)

    controller = WorkflowController.__new__(WorkflowController)
    controller.core = CoreApi()

    outputs = controller._extract_outputs(
        "pod-1", "default", attempts=2, retry_seconds=0)

    assert outputs == {"result": "ready"}


def test_missing_declared_parent_output_fails_the_data_contract():
    workflow = _WorkflowState({
        "metadata": {
            "uid": "workflow-uid",
            "name": "workflow-1",
            "namespace": "default",
        },
        "spec": {"tasks": [
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
        ]},
    })
    workflow.tasks["ingest"].state = "RUNNING"
    controller = WorkflowController.__new__(WorkflowController)
    controller._lock = Lock()
    controller.workflows = {workflow.uid: workflow}
    controller._extract_outputs = lambda *args, **kwargs: {}
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="ingest-pod",
            labels={
                LABEL_WORKFLOW: "workflow-1",
                LABEL_TASK_NAME: "ingest",
            },
        ),
        spec=SimpleNamespace(node_name="worker-a"),
        status=SimpleNamespace(
            phase="Succeeded",
            host_ip="10.0.0.12",
            start_time=None,
        ),
    )

    controller._on_pod_event(pod, "MODIFIED")

    assert workflow.tasks["ingest"].state == "FAILED"
    assert workflow.message == (
        "task 'ingest' omitted required outputs: generated_file_path")


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


def test_logical_resources_are_separate_from_physical_emulation_envelope():
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
    template = {
        "image": "task:v1",
        "taskClass": "CPU_BOUND",
        "cpuRequest": "2",
        "memoryRequest": "2Gi",
        "physicalResources": {
            "cpuRequest": "500m",
            "memoryRequest": "384Mi",
            "cpuLimit": "750m",
            "memoryLimit": "512Mi",
        },
        "emulation": {"workScale": 0.05, "timeScale": 0.1},
        "compatibleNodeTypes": ["CPU_OPT", "GENERAL"],
    }
    controller = WorkflowController.__new__(WorkflowController)

    pod = controller._build_pod(
        workflow, workflow.tasks["task-1"], template, {})

    annotations = pod.metadata.annotations
    resources = pod.spec.containers[0].resources
    env = {item.name: item.value for item in pod.spec.containers[0].env}
    assert annotations["ts.scheduler/logical_cpu_request"] == "2"
    assert annotations["ts.scheduler/logical_memory_request"] == "2Gi"
    assert resources.requests == {"cpu": "500m", "memory": "384Mi"}
    assert resources.limits == {"cpu": "750m", "memory": "512Mi"}
    assert env["TS_WORK_SCALE"] == "0.05"
    assert env["TS_TIME_SCALE"] == "0.1"


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


def test_long_workflow_names_still_produce_unique_valid_pod_names():
    import re

    name = "nightly-genomics-alignment-pipeline-for-cohort-2026-batch"
    uid = "3f2a9c1e-0000-4000-8000-123456789abc"
    first = WorkflowController._safe_pod_name(name, "align-sample-a", uid)
    second = WorkflowController._safe_pod_name(name, "align-sample-b", uid)
    other_workflow = WorkflowController._safe_pod_name(
        name, "align-sample-a", "different-uid")

    assert first != second != other_workflow
    for pod_name in (first, second, other_workflow):
        assert len(pod_name) <= 63
        assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", pod_name)
    # Retries must reuse the same name so the controller can adopt the Pod.
    assert first == WorkflowController._safe_pod_name(name, "align-sample-a", uid)
    assert re.fullmatch(
        r"[a-z0-9]([-a-z0-9]*[a-z0-9])?",
        WorkflowController._safe_pod_name("Bad_Name", "Task.One", uid))


def test_non_object_output_marker_is_rejected_not_crashing():
    class CoreApi:
        def read_namespaced_pod_log(self, *, name, namespace):
            return '__TS_OUTPUT__=["not", "an", "object"]'

    controller = WorkflowController.__new__(WorkflowController)
    controller.core = CoreApi()

    assert controller._extract_outputs("pod-1", "default", attempts=1) == {}


def test_terminal_workflow_status_is_pushed_once():
    workflow = _WorkflowState({
        "metadata": {"uid": "uid-1", "name": "wf", "namespace": "default"},
        "spec": {"tasks": [{"name": "only", "template": "t"}]},
    })
    workflow.tasks["only"].state = "FINISHED"
    workflow.tasks["only"].finished_at = "2026-01-01T00:00:05Z"
    controller = WorkflowController.__new__(WorkflowController)
    controller._lock = Lock()
    controller.workflows = {workflow.uid: workflow}
    patches = []
    controller.custom = SimpleNamespace(
        patch_namespaced_custom_object_status=lambda **kwargs: patches.append(kwargs))
    controller._check_terminal(workflow)
    assert workflow.state == "FINISHED"
    assert workflow.finished_at == "2026-01-01T00:00:05Z"

    import controlplane.controller.main as controller_module

    original_sleep = controller_module.time.sleep
    calls = {"count": 0}

    def stop_after_three(_seconds):
        calls["count"] += 1
        if calls["count"] >= 3:
            raise KeyboardInterrupt

    controller_module.time.sleep = stop_after_three
    try:
        controller._reconcile_loop()
    except KeyboardInterrupt:
        pass
    finally:
        controller_module.time.sleep = original_sleep

    assert len(patches) == 1
    assert workflow.final_status_pushed is True


def test_pod_carries_workflow_dag_gang_size_and_emulation_env():
    workflow = _WorkflowState({
        "metadata": {"uid": "uid-1", "name": "wf", "namespace": "default"},
        "spec": {"tasks": [
            {"name": "source", "template": "io"},
            {"name": "rank-0", "template": "mpi",
             "dependsOn": [{"parent": "source", "dependencyType": "DATA",
                            "dataFields": ["generated_file_path"]}]},
            {"name": "rank-1", "template": "mpi",
             "dependsOn": [{"parent": "source", "dependencyType": "EXECUTION"}]},
        ]},
    })
    controller = WorkflowController.__new__(WorkflowController)
    controller.template_cache = {
        "io": {"expectedOutputBytes": 4096},
        "mpi": {"gangGroupId": "ranks"},
    }
    template = {
        "image": "task:v1",
        "taskClass": "CPU_BOUND",
        "cpuRequest": "0.5",
        "memoryRequest": "256Mi",
        "compatibleNodeTypes": ["GENERAL"],
        "gangGroupId": "ranks",
        "env": {"TS_CPU_ITERATIONS": "1000"},
    }

    pod = controller._build_pod(workflow, workflow.tasks["rank-0"], template, {})

    annotations = pod.metadata.annotations
    dag = json.loads(annotations["ts.scheduler/workflow_dag"])
    assert dag["tasks"] == {"source": "io", "rank-0": "mpi", "rank-1": "mpi"}
    assert ["source", "rank-0", "DATA", 4096] in dag["edges"]
    assert ["source", "rank-1", "EXECUTION", 0] in dag["edges"]
    assert annotations["ts.scheduler/gang_size"] == "2"
    env = {item.name: item for item in pod.spec.containers[0].env}
    assert env["TS_CPU_ITERATIONS"].value == "1000"
    assert env["TS_TASK_CLASS"].value == "CPU_BOUND"
    assert env["TS_NODE_NAME"].value_from.field_ref.field_path == "spec.nodeName"
    selector = env["TS_NODE_SPEEDS"].value_from.config_map_key_ref
    assert (selector.name, selector.key, selector.optional) == (
        "ts-emulation", "node-speeds", True)