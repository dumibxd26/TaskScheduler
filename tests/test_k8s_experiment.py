import pytest
from kubernetes.client.rest import ApiException

from run_k8s_experiment import (
    KubernetesExperiment,
    SUPPORTED_POLICIES,
    policy_order_for_repetition,
    summarize_policy,
    task_metrics,
    workflow_batch,
)


def test_workflow_batch_has_expected_shapes_and_pressure_ordering():
    pressure, critical, regular = workflow_batch("adaptive", 1, "run-1")
    workflows = [pressure, critical] + regular
    names = [workflow["metadata"]["name"] for workflow in workflows]

    assert len(workflows) == 5
    assert len(names) == len(set(names))
    assert max(map(len, names)) <= 63
    assert sum(len(workflow["spec"]["tasks"]) for workflow in workflows) == 20
    assert pressure["spec"]["priority"] == "BEST_EFFORT"
    assert pressure["spec"]["preemptable"] is True
    assert critical["spec"]["priority"] == "CRITICAL"
    assert critical["spec"]["preemptable"] is False


def test_task_metrics_extracts_remote_transfer():
    metrics, mode, source = task_metrics(
        "[FETCH] http://node:8080/result -> /data/result\n"
        '__TS_METRICS__={"io_bytes_read": 1024, '
        '"transfer_seconds": 1.25}\n')

    assert metrics["io_bytes_read"] == 1024
    assert metrics["transfer_seconds"] == 1.25
    assert mode == "remote"
    assert source.startswith("[FETCH]")


def test_policy_summary_aggregates_response_and_preemptions():
    trial = {
        "makespan_seconds": 12.0,
        "mean_response_seconds": 8.0,
        "preemptions": 2,
        "workflows": [{
            "shape": "critical",
            "state": "FINISHED",
            "response_seconds": 8.0,
            "tasks": [{
                "node_name": "node-a",
                "container_runtime_seconds": 3.0,
                "metrics": {"transfer_seconds": 0.5},
                "transfer_mode": "remote",
            }],
        }],
    }

    summary = summarize_policy([trial])

    assert summary["completion_rate"] == 1
    assert summary["median_critical_response_seconds"] == 8
    assert summary["preemptions"] == 2
    assert summary["total_transfer_seconds"] == 0.5
    assert summary["placements_by_node"] == {"node-a": 1}


def test_policy_order_alternates_between_repetitions():
    policies = ["legacy", "adaptive"]

    assert policy_order_for_repetition(policies, 1) == ["legacy", "adaptive"]
    assert policy_order_for_repetition(policies, 2) == ["adaptive", "legacy"]
    assert policy_order_for_repetition(policies, 3) == ["legacy", "adaptive"]
    assert policies == ["legacy", "adaptive"]


def test_three_policy_order_is_latin_rotation():
    policies = ["legacy-v1", "legacy-tuned", "adaptive"]
    orders = [
        policy_order_for_repetition(policies, repetition)
        for repetition in range(1, 4)
    ]

    assert orders == [
        ["legacy-v1", "legacy-tuned", "adaptive"],
        ["legacy-tuned", "adaptive", "legacy-v1"],
        ["adaptive", "legacy-v1", "legacy-tuned"],
    ]
    assert all(
        {order[position] for order in orders} == set(policies)
        for position in range(3)
    )


def test_experiment_accepts_explicit_legacy_variants():
    assert "legacy-v1" in SUPPORTED_POLICIES
    assert "legacy-tuned" in SUPPORTED_POLICIES
    pressure, critical, regular = workflow_batch("legacy-tuned", 1, "run-1")
    assert len([pressure, critical, *regular]) == 5


def test_cleanup_ignores_only_not_found():
    def missing():
        raise ApiException(status=404, reason="Not Found")

    def forbidden():
        raise ApiException(status=403, reason="Forbidden")

    assert KubernetesExperiment.delete_ignoring_not_found(missing) is None
    with pytest.raises(ApiException, match="Forbidden"):
        KubernetesExperiment.delete_ignoring_not_found(forbidden)