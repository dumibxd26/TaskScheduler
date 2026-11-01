from collections import Counter

import pytest
from kubernetes.client.rest import ApiException

from run_k8s_experiment import (
    KubernetesExperiment,
    SUPPORTED_POLICIES,
    batch_bodies,
    calibration_bodies,
    load_batches,
    parse_scheduler_events,
    task_metrics,
    trial_schedule,
    trial_summary,
)


def test_trial_schedule_runs_each_pair_once_per_repetition_reproducibly():
    first = trial_schedule(["priority", "dag"], list(SUPPORTED_POLICIES), 3, 2026)
    again = trial_schedule(["priority", "dag"], list(SUPPORTED_POLICIES), 3, 2026)
    other = trial_schedule(["priority", "dag"], list(SUPPORTED_POLICIES), 3, 7)

    assert first == again and first != other
    counts = Counter((batch, policy) for _, batch, policy in first)
    assert set(counts.values()) == {3}
    for repetition in (1, 2, 3):
        pairs = [(b, p) for r, b, p in first if r == repetition]
        assert len(pairs) == len(set(pairs)) == 2 * len(SUPPORTED_POLICIES)


def test_batch_bodies_follow_the_shared_definition(project_root):
    batches = load_batches(project_root / "experiments" / "k8s" / "batches.yaml")

    priority = batch_bodies(batches, "priority", "run", "t007", "adaptive")
    dag = batch_bodies(batches, "dag", "run", "t008", "heft")

    assert [arrival for arrival, _ in priority] == [
        "start", "delayed", "delayed", "delayed", "delayed"]
    assert {arrival for arrival, _ in dag} == {"start"}
    for _, body in priority + dag:
        assert len(body["metadata"]["name"]) <= 63
        assert body["metadata"]["labels"]["ts.io/experiment-trial"] in {"t007", "t008"}
    pressure = priority[0][1]
    assert pressure["spec"]["priority"] == "BEST_EFFORT"
    assert sum(len(body["spec"]["tasks"]) for _, body in priority) == 20
    assert sum(len(body["spec"]["tasks"]) for _, body in dag) == 17


def test_calibration_requests_three_samples_per_worker_and_template():
    bodies = calibration_bodies(["experiment-cpu", "experiment-io"], 4, "run")

    assert [len(body["spec"]["tasks"]) for body in bodies] == [12, 12]
    assert {task["template"] for task in bodies[0]["spec"]["tasks"]} == {"experiment-cpu"}


def test_task_metrics_extracts_remote_transfer_and_metrics():
    metrics, mode, source = task_metrics(
        "[LOCAL] /data/outputs/a -> /data/inputs/a\n"
        "[FETCH] http://node/a -> /data/inputs/b\n"
        '__TS_METRICS__={"runtime_seconds": 1.5}\n')

    assert metrics == {"runtime_seconds": 1.5}
    assert mode == "remote"
    assert source.startswith("[FETCH]")


def test_scheduler_events_keep_cluster_clock_timestamps():
    log = "\n".join([
        "[BIND] 'ts-exp-t001-pipeline-ingest-1' -> 'ts-node-io' at=100.250",
        "[BIND] 'ts-exp-t002-other' -> 'ts-node-io' at=101.000",
        "[PREEMPT] 'ts-exp-t001-pressure-long-1' from ts-node-cpu (kill_restart) at=102.5",
        "[SCHED] tick error: boom ts-exp-t001-x",
    ])

    events = parse_scheduler_events(log, "ts-exp-t001-")

    assert events["binds"] == [{
        "pod": "ts-exp-t001-pipeline-ingest-1", "node": "ts-node-io", "at": 100.25}]
    assert events["preemptions"][0]["at"] == 102.5
    assert len(events["error_lines"]) == 1


def test_scheduler_wide_errors_are_kept_without_a_trial_prefix():
    log = "\n".join([
        "[SCHED] tick error: 'NoneType' object is not subscriptable",
        "[SCHED] cluster refresh failed: timed out",
        "[BIND] 'ts-exp-t001-a' -> 'ts-node-io' at=1.0",
        "[DONE] 'ts-exp-t001-a' Succeeded on ts-node-io "
        "(runtime=3.000s, startup=1.00s) at=5.5",
    ])

    events = parse_scheduler_events(log, "ts-exp-t001-")

    assert events["completions"] == [{"pod": "ts-exp-t001-a", "at": 5.5}]
    assert events["error_lines"] == []
    assert len(events["scheduler_errors"]) == 2
    assert events["scheduler_errors"][0].startswith("[SCHED] tick error")


def test_trial_summary_uses_admission_and_latest_task_finish():
    workflows = [
        {"response_seconds": 10.0, "completion_epoch": 110.0, "admitted_epoch": 100.0},
        {"response_seconds": 4.0, "completion_epoch": 111.0, "admitted_epoch": 107.0},
    ]

    summary = trial_summary(workflows)

    assert summary["makespan_seconds"] == 11.0
    assert summary["mean_response_seconds"] == 7.0
    assert summary["p95_response_seconds"] == 10.0


def test_delete_ignoring_not_found_only_ignores_404():
    def missing():
        raise ApiException(status=404, reason="Not Found")

    def forbidden():
        raise ApiException(status=403, reason="Forbidden")

    assert KubernetesExperiment.delete_ignoring_not_found(missing) is None
    with pytest.raises(ApiException):
        KubernetesExperiment.delete_ignoring_not_found(forbidden)
