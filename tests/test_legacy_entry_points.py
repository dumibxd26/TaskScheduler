"""Regression tests for the inherited root-level entry points."""

from types import SimpleNamespace

from kubernetes.client.rest import ApiException

import submit_workflows
from models.cluster import ClusterScenario, Node
from models.enums import NodeType, PriorityClass
from server import SchedulerServer, _parse_template, create_app


def _server():
    node = Node(node_id="n1", node_type=NodeType.GENERAL, total_cpu=4,
                total_memory=4096, free_cpu=4, free_memory=4096)
    server = SchedulerServer(
        ClusterScenario("s", "s", "", nodes=[node]), simulate=True)
    server.register_template(_parse_template({
        "workflow_template_id": "wf",
        "default_priority": "CRITICAL",
        "tasks": {"t": {
            "name": "t", "task_class": "CPU_BOUND", "cpu_request": 1,
            "memory_request": 128, "image_name": "img",
            "compatible_node_types": ["GENERAL"]}},
        "edges": [],
    }))
    return server


def test_profiles_endpoint_returns_learned_profiles():
    server = _server()
    server.store.record_observation("t", "n1", NodeType.GENERAL, 3.0, 1.0)
    client = create_app(server).test_client()

    response = client.get("/profiles")

    assert response.status_code == 200
    assert "t" in response.get_json()


def test_unknown_priority_is_rejected_and_best_effort_is_honoured():
    server = _server()
    client = create_app(server).test_client()

    rejected = client.post("/workflows", json={"template_id": "wf", "priority": "URGENTISH"})
    accepted = client.post("/workflows", json={"template_id": "wf", "priority": "best_effort"})

    assert rejected.status_code == 400
    assert accepted.status_code == 201
    workflow = server.all_workflows[accepted.get_json()["workflow_instance_id"]]
    assert workflow.priority is PriorityClass.BEST_EFFORT


def test_submitter_stops_a_chain_when_a_parent_times_out(monkeypatch):
    created = []
    monkeypatch.setattr(submit_workflows, "create_workflow_pod",
                        lambda v1, wf, task, suffix, env: created.append(task) or task)
    monkeypatch.setattr(submit_workflows, "wait_for_pod",
                        lambda v1, pod: ("Timeout", "(unknown)"))
    monkeypatch.setattr(submit_workflows, "extract_output", lambda v1, pod: {})
    results = {}

    submit_workflows.run_workflow(SimpleNamespace(), "wf-1", results)

    assert created == [submit_workflows.TASK_ORDER[0]]
    assert results["wf-1"][0]["status"] == "Timeout"


def test_legacy_scheduler_releases_capacity_when_binding_fails():
    import k8s_scheduler

    scheduler = k8s_scheduler.K8sScheduler.__new__(k8s_scheduler.K8sScheduler)

    def reject(**kwargs):
        raise ApiException(status=409, reason="Conflict")

    scheduler.v1 = SimpleNamespace(create_namespaced_pod_binding=reject)

    assert scheduler.bind_pod("pod", "node") is False
