import copy
import json

import pytest
import yaml

import analyze_kind_validation as analyzer
import kind_lab

PROFILE = "homogeneous"
POLICIES = ["heft", "kube-default"]


def _templates(project_root):
    return {
        item["metadata"]["name"]: item
        for item in yaml.safe_load_all(
            (project_root / "experiments" / "k8s" / "tasktemplates.yaml").read_text(
                encoding="utf-8"))
        if item
    }


def _task(name, template, node, templates, *, read=0, written=None, mode=None):
    kind = analyzer.SPEED_KEYS[templates[template]["spec"]["taskClass"]]
    metrics = {
        "runtime_seconds": 3.0,
        "node_name": node,
        "io_bytes_read": read,
        "output_bytes_by_field": (
            {"generated_file_path": written} if written else {}),
        "transfer_seconds": 0.1 if mode == "remote" else 0.0,
        "emulation": {"speed_factor": 1.0, "speed_kind": kind},
    }
    return {"name": name, "template": template, "state": "FINISHED",
            "pod_phase": "Succeeded", "restart_count": 0, "node_name": node,
            "pod_name": f"pod-{name}", "metrics": metrics, "transfer_mode": mode}


def _workflows(definition, workers, templates, shift=0.0):
    workflows = []
    for index, workflow in enumerate(definition["workflows"]):
        tasks = {}
        for position, spec in enumerate(workflow["tasks"]):
            node = workers[(index + position) % len(workers)]
            written = 1000 if spec["template"].startswith("experiment-io") else None
            tasks[spec["name"]] = _task(spec["name"], spec["template"], node,
                                        templates, written=written)
        for spec in workflow["tasks"]:
            for dependency in spec.get("dependsOn", []):
                if "generated_file_path" in dependency.get("dataFields", []):
                    producer = tasks[dependency["parent"]]
                    consumer = tasks[spec["name"]]
                    consumer["metrics"]["io_bytes_read"] = 1000
                    consumer["transfer_mode"] = (
                        "local" if producer["node_name"] == consumer["node_name"]
                        else "remote")
        workflows.append({
            "shape": workflow["name"], "state": "FINISHED",
            "response_seconds": 10.0 + index + shift,
            "tasks": list(tasks.values()),
        })
    return workflows


def build_evidence(project_root):
    document = kind_lab.load_document()
    workers = [worker.name for worker in kind_lab.workers(document, PROFILE)]
    templates = _templates(project_root)
    batches = yaml.safe_load(
        (project_root / "experiments" / "k8s" / "batches.yaml").read_text(encoding="utf-8"))
    profiles = {"_schema_version": 2, "profiles": {
        template: {"nodes": {worker: {"observations": [{}, {}, {}]} for worker in workers}}
        for template in templates}}
    calibration_workflow = {
        "name": "cal", "state": "FINISHED",
        "tasks": [_task(f"s-{worker}", "experiment-cpu", worker, templates)
                  for worker in workers]}
    schedule, trials = [], []
    for batch in ("priority", "dag"):
        for policy in POLICIES:
            schedule.append([1, batch, policy])
            definition = batches["batches"][batch]
            workflows = _workflows(definition, workers, templates,
                                   shift=0.0 if policy == "heft" else 5.0)
            task_count = sum(len(item["tasks"]) for item in definition["workflows"])
            trials.append({
                "trial_id": f"t-{batch}-{policy}", "repetition": 1, "batch": batch,
                "policy": policy,
                "scheduler": {"policy": policy, "warm_profile_loaded": True},
                "scheduler_events": {
                    "binds": [{"pod": f"p{i}", "at": 1.0} for i in range(task_count)],
                    "preemptions": [], "error_lines": []},
                "preemptions": 0,
                "workflows": workflows,
                "makespan_seconds": 30.0 if policy == "heft" else 40.0,
                "mean_response_seconds": 12.0 if policy == "heft" else 17.0,
                "p95_response_seconds": 14.0 if policy == "heft" else 19.0,
            })
    nodes = []
    for worker in kind_lab.workers(document, PROFILE):
        nodes.append({"name": worker.name, "capacity_cpu": f"{worker.capacity_cpu:g}",
                      "netem_delay_ms": f"{worker.delay_ms:g}",
                      "netem_rate_mbit": f"{worker.rate_mbit:g}",
                      "bandwidth_mib_s_by_peer": {}})
    live = {
        "schema_version": 2, "profile": PROFILE, "completed_at": "now",
        "templates": list(templates), "schedule": schedule,
        "batch_definitions": batches["batches"],
        "cluster": {"nodes": nodes},
        "calibration": {"profiles_json": json.dumps(profiles),
                        "workflows": [calibration_workflow]},
        "trials": trials,
    }
    evidence = {
        "profile": PROFILE,
        "speeds": {worker.name: dict(worker.speed)
                   for worker in kind_lab.workers(document, PROFILE)},
        "network": [
            {"node": worker.name, "observed_qdisc":
             f"qdisc netem 8001: root refcnt 9 limit 1000 delay {worker.delay_ms:g}ms "
             f"rate {worker.rate_mbit:g}Mbit seed 1"}
            for worker in kind_lab.workers(document, PROFILE)],
    }
    twin = {"calibration": {}, "predictions": {
        batch: {policy: {"summary": {
            "makespan_seconds": 31.0 if policy == "heft" else 39.0,
            "mean_response_seconds": 12.5 if policy == "heft" else 16.0}}
            for policy in POLICIES}
        for batch in ("priority", "dag")}}
    return live, evidence, twin


def write(tmp_path, live, evidence, twin):
    (tmp_path / f"{PROFILE}.json").write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / f"{PROFILE}-cluster.json").write_text(json.dumps(evidence), encoding="utf-8")
    (tmp_path / f"{PROFILE}-twin.json").write_text(json.dumps(twin), encoding="utf-8")


def test_complete_evidence_passes_and_is_summarised(tmp_path, project_root):
    write(tmp_path, *build_evidence(project_root))

    summary = analyzer.analyze(tmp_path, [PROFILE])

    assert summary["integrity"] == "passed"
    dag = summary["pooled"]["dag"]
    assert dag["heft"]["makespan_seconds"]["median"] == 30.0
    contrast = next(row for row in summary["contrasts"]
                    if row["batch"] == "dag" and row["policy"] == "heft"
                    and row["baseline"] == "kube-default"
                    and row["metric"] == "makespan_seconds")
    assert contrast["mean_ratio"] == pytest.approx(0.75)
    agreement = summary["twin_agreement"][PROFILE]["dag"]["makespan_seconds"]
    assert agreement["live_best"] == agreement["twin_best"] == ["heft"]
    assert "Kind validation summary" in analyzer.markdown(summary)


@pytest.mark.parametrize("tamper,message", [
    (lambda live, evidence: evidence["network"][0].update(
        observed_qdisc="qdisc netem 1: root delay 4ms rate 1Mbit"), "does not match"),
    (lambda live, evidence: live["trials"][0]["workflows"][0]["tasks"][0][
        "metrics"].update(node_name="elsewhere"), "ran on"),
    (lambda live, evidence: live["trials"][0]["workflows"][0]["tasks"][0][
        "metrics"]["emulation"].update(speed_factor=0.5), "emulated"),
    (lambda live, evidence: live["trials"].pop(), "missing trials"),
    (lambda live, evidence: live["trials"][0]["scheduler"].update(
        warm_profile_loaded=False), "warm profile"),
    (lambda live, evidence: live["trials"][0]["scheduler_events"]["binds"].pop(),
     "binds"),
])
def test_tampered_evidence_is_rejected(tmp_path, project_root, tamper, message):
    live, evidence, twin = build_evidence(project_root)
    tamper(live, evidence)
    write(tmp_path, live, evidence, twin)

    with pytest.raises(analyzer.IntegrityError, match=message):
        analyzer.analyze(tmp_path, [PROFILE])


def test_data_contract_requires_the_produced_bytes(tmp_path, project_root):
    live, evidence, twin = build_evidence(project_root)
    dag = next(trial for trial in live["trials"] if trial["batch"] == "dag")
    fanout = next(item for item in dag["workflows"] if item["shape"] == "dag-fanout")
    leaf = next(task for task in fanout["tasks"] if task["name"] == "leaf-1")
    leaf["metrics"]["io_bytes_read"] = 10
    write(tmp_path, live, evidence, twin)

    with pytest.raises(analyzer.IntegrityError, match="read 10"):
        analyzer.analyze(tmp_path, [PROFILE])


def test_spearman_handles_ties_and_perfect_order():
    assert analyzer.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert analyzer.spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert analyzer.spearman([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833)
