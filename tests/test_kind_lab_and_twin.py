import json

import pytest
import yaml

import kind_lab
from simulation.engine import Simulator
from simulation.kind_twin import (
    Calibration,
    load_batches,
    load_profiles,
    twin_cluster,
    twin_workload,
)
from simulation.policies import create_policy
from simulation.workloads import submission_results


def calibration(**overrides):
    data = {
        "base_runtime_s": {"experiment-cpu": 4.0, "experiment-cpu-long": 12.0,
                           "experiment-mem": 3.0, "experiment-io": 3.0,
                           "experiment-io-large": 3.0},
        "startup_s": {"experiment-cpu": 1.0},
        "control_plane_delay_s": 0.0,
        "output_mib": {"experiment-io": 24, "experiment-io-large": 96},
        "egress_mib_s": {},
    }
    data.update(overrides)
    return Calibration.from_mapping(data)


def test_every_profile_mirrors_a_simulator_family_and_fits_the_host(project_root):
    document = kind_lab.load_document()
    clusters = {path.stem for path in (project_root / "scenarios" / "clusters").glob("*.yaml")}
    for name, profile in document["profiles"].items():
        assert profile["mirrors"] in clusters
        workers = kind_lab.workers(document, name)
        assert sum(worker.slots for worker in workers) == 6
        cpus = sum(kind_lab.docker_caps(document, worker)[0] for worker in workers)
        assert cpus <= 5.0


def test_rendered_kind_config_labels_logical_capacity(project_root):
    document = kind_lab.load_document()
    config = yaml.safe_load(kind_lab.render_kind_config(document, "balanced"))
    workers = [node for node in config["nodes"] if node["role"] == "worker"]

    assert [node["labels"]["node-type"] for node in workers] == [
        "CPU_OPT", "MEM_OPT", "IO_OPT", "GENERAL"]
    assert [node["labels"]["ts.capacity/cpu"] for node in workers] == ["1", "1", "0.5", "0.5"]
    assert "name: ts-node-edge" in workers[-1]["kubeadmConfigPatches"][0]


def test_netem_command_and_qdisc_parsing_round_trip():
    worker = kind_lab.Worker("w", "GENERAL", 1, {"cpu": 1, "memory": 1, "io": 1},
                             delay_ms=15, rate_mbit=40)

    assert kind_lab.netem_command("ts-cluster-worker4", worker) == [
        "docker", "exec", "ts-cluster-worker4", "tc", "qdisc", "replace", "dev",
        "eth0", "root", "netem", "delay", "15ms", "rate", "40mbit"]
    assert kind_lab.parse_qdisc(
        "qdisc netem 8001: root refcnt 9 limit 1000 delay 15ms rate 40Mbit seed 7"
    ) == {"delay_ms": 15.0, "rate_mbit": 40.0}
    assert kind_lab.parse_qdisc("qdisc netem 1: root delay 500us rate 1Gbit") == {
        "delay_ms": 0.5, "rate_mbit": 1000.0}
    assert kind_lab.parse_qdisc("qdisc noqueue 0: root refcnt 2")["rate_mbit"] is None


def test_profile_validation_rejects_unknown_profiles_and_bad_speeds():
    document = kind_lab.load_document()
    with pytest.raises(ValueError, match="unknown profile"):
        kind_lab.workers(document, "missing")
    broken = json.loads(json.dumps(document))
    broken["profiles"]["edge"]["workers"][0]["speed"]["cpu"] = 0
    with pytest.raises(ValueError, match="positive"):
        kind_lab.workers(broken, "edge")


def test_twin_turns_slots_speeds_and_netem_into_model_parameters(project_root):
    document = load_profiles(project_root / "clusters" / "kind_profiles.yaml")
    cluster = twin_cluster("balanced", document, calibration(
        egress_mib_s={"ts-node-cpu": 48.5}))
    nodes = cluster.nodes_by_id
    links = {link.link_id: link for link in cluster.links}

    assert nodes["ts-node-cpu"].cpu.logical_cpus == 2
    assert nodes["ts-node-mem"].cpu.calibrated_gflops_per_core == pytest.approx(0.45)
    assert nodes["ts-node-io"].storage.per_task_write_mib_s == pytest.approx(100.0)
    # A measured application rate is used and protocol efficiency undone.
    assert links["ts-node-cpu-link"].bandwidth_mib_s == pytest.approx(48.5 / 0.97, rel=1e-5)
    edge = links["ts-node-edge-link"]
    assert edge.bandwidth_mib_s == pytest.approx(60e6 / 8 / 2 ** 20, rel=1e-5)
    assert edge.latency_ms == pytest.approx(10.05)
    assert edge.reverse_bandwidth_mib_s > 1000


def test_twin_workloads_match_the_live_batches(project_root):
    batches = load_batches(project_root / "experiments" / "k8s" / "batches.yaml")
    priority = twin_workload(batches, "priority", calibration())
    dag = twin_workload(batches, "dag", calibration())

    assert sum(len(item.workflow.tasks) for item in priority.submissions) == 20
    assert sum(len(item.workflow.tasks) for item in dag.submissions) == 17
    arrivals = {item.submission_id: item.arrival_s for item in priority.submissions}
    assert arrivals["pressure"] == 0 and arrivals["critical"] == 7
    fanout = next(item.workflow for item in dag.submissions
                  if item.submission_id == "dag-fanout")
    assert {dependency.data_mib for dependency in fanout.dependencies} == {96}
    forkjoin = next(item.workflow for item in dag.submissions
                    if item.submission_id == "dag-forkjoin")
    sizes = {(d.parent, d.child): d.data_mib for d in forkjoin.dependencies}
    assert sizes[("split", "branch-cpu")] == 24 and sizes[("split", "branch-io")] == 0


def test_twin_runtime_scales_with_the_emulated_speed(project_root):
    document = load_profiles(project_root / "clusters" / "kind_profiles.yaml")
    cluster = twin_cluster("balanced", document, calibration())
    batches = {
        "schema_version": 1, "priority_delay_s": 0,
        "batches": {"one": {"workflows": [{
            "name": "solo", "priority": "BATCH", "arrival": "start",
            "tasks": [{"name": "work", "template": "experiment-cpu"}]}]}},
    }
    workload = twin_workload(batches, "one", calibration())
    compiled = workload.compile()

    class OnEdge:
        name = "on-edge"

        def schedule(self, snapshot):
            from simulation.engine import Placement
            return [Placement(task.task_id, "ts-node-edge") for task in snapshot.ready_tasks]

    result = Simulator(cluster, compiled, OnEdge()).run()
    response = submission_results(workload, result)["solo"].response_s

    # 4 s of work at CPU speed 0.3 plus 1 s container startup.
    assert response == pytest.approx(4.0 / 0.3 + 1.0, rel=1e-6)
    heft = Simulator(cluster, compiled, create_policy("heft")).run()
    assert heft.tasks["solo::work"].node_id == "ts-node-cpu"
