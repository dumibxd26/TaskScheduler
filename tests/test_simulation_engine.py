import pytest

from simulation.benchmark_suite import load_benchmark_suite
from simulation.engine import Placement, Simulator
from simulation.policies import create_policy
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster
from tests.test_simulation_schema import node, task


class AssignedPolicy:
    name = "assigned"

    def __init__(self, assignments):
        self.assignments = assignments

    def schedule(self, snapshot):
        placements = []
        free_cpu = {
            node_id: view.free_logical_cpus for node_id, view in snapshot.nodes.items()
        }
        free_memory = {
            node_id: view.free_memory_mib for node_id, view in snapshot.nodes.items()
        }
        for ready in snapshot.ready_tasks:
            node_id = self.assignments[ready.task_id]
            if (ready.cpu_cores <= free_cpu[node_id]
                    and ready.memory_mib <= free_memory[node_id]):
                placements.append(Placement(ready.task_id, node_id))
                free_cpu[node_id] -= ready.cpu_cores
                free_memory[node_id] -= ready.memory_mib
        return placements


def work(task_id, *, image="work:v1", image_size=0, startup=0,
         compute=8, memory_read=0):
    data = task(task_id)
    data.update({
        "image": image,
        "image_size_mib": image_size,
        "startup_s": startup,
        "compute_gflop": compute,
        "memory_read_mib": memory_read,
    })
    return data


def test_serial_dag_uses_virtual_time_and_releases_capacity():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "single", "nodes": [node_data]},
    })
    workflow = WorkflowSpec.from_mapping({
        "workflow": {
            "id": "chain",
            "tasks": [work("first"), work("second")],
            "dependencies": [{"parent": "first", "child": "second"}],
        },
    })

    result = Simulator(
        cluster, workflow, AssignedPolicy({"first": "only", "second": "only"})
    ).run()

    assert result.makespan_s == pytest.approx(2.0)
    assert result.tasks["first"].finished_at_s == pytest.approx(1.0)
    assert result.tasks["second"].started_at_s == pytest.approx(1.0)
    assert result.cold_starts == 0


def test_dependency_transfer_and_shared_cold_image_are_modeled_once():
    source = node("source")
    target = node("target")
    source["background_cpu_fraction"] = 0
    target["background_cpu_fraction"] = 0
    cluster = ClusterSpec.from_mapping({
        "cluster": {
            "id": "networked",
            "registry_endpoint": "registry",
            "network_vertices": ["switch"],
            "nodes": [source, target],
            "links": [
                {"id": "source-link", "endpoints": ["source", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
                {"id": "target-link", "endpoints": ["target", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
                {"id": "registry-link", "endpoints": ["registry", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 1},
            ],
        },
    })
    workflow = WorkflowSpec.from_mapping({
        "workflow": {
            "id": "transfer",
            "tasks": [
                work("producer", image="shared:v1", image_size=97),
                work("consumer", image="shared:v1", image_size=97),
            ],
            "dependencies": [
                {"parent": "producer", "child": "consumer", "data_mib": 97},
            ],
        },
    })

    result = Simulator(
        cluster,
        workflow,
        AssignedPolicy({"producer": "source", "consumer": "target"}),
    ).run()

    assert result.network_transfer_mib == pytest.approx(291)
    assert result.cold_starts == 2
    assert result.tasks["consumer"].preparation_s > 1.0
    assert result.tasks["consumer"].started_at_s > result.tasks["producer"].finished_at_s


def test_cpu_contenders_share_smt_aware_node_capacity():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "single", "nodes": [node_data]},
    })
    first = work("first", compute=32)
    second = work("second", compute=32)
    first["cpu_cores"] = 4
    second["cpu_cores"] = 4
    first["parallel_fraction"] = 1
    second["parallel_fraction"] = 1
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "parallel", "tasks": [first, second]},
    })

    result = Simulator(
        cluster, workflow, AssignedPolicy({"first": "only", "second": "only"})
    ).run()

    # Eight busy threads on 4 cores with 30% SMT gain deliver 4 + 4 * 0.3
    # core-equivalents of 8 GFLOP/s, shared equally by the two tasks.
    node_gflops = 8.0 * (4 + 4 * 0.30)
    expected = 64.0 / node_gflops
    assert result.tasks["first"].finished_at_s == pytest.approx(expected, rel=1e-3)
    assert result.tasks["second"].finished_at_s == pytest.approx(expected, rel=1e-3)


def test_non_throttling_long_work_skips_irrelevant_thermal_ticks():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "cool-long", "nodes": [node_data]},
    })
    long_work = work("long", compute=3200)
    long_work["cpu_cores"] = 8
    long_work["parallel_fraction"] = 1
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "long", "tasks": [long_work]},
    })

    fine = Simulator(
        cluster, workflow, AssignedPolicy({"long": "only"}),
        thermal_step_s=0.01,
    ).run()
    coarse = Simulator(
        cluster, workflow, AssignedPolicy({"long": "only"}),
        thermal_step_s=120.0,
    ).run()

    assert fine.makespan_s == coarse.makespan_s
    assert fine.nodes["only"] == coarse.nodes["only"]


def test_throttling_work_retains_thermal_integration_accuracy():
    node_data = node("only")
    node_data["background_cpu_fraction"] = 0
    node_data["initial_warm_images"] = ["work:v1"]
    node_data["thermal"].update({
        "ambient_c": 23,
        "initial_c": 30,
        "throttle_c": 40,
        "shutdown_c": 60,
        "thermal_resistance_c_per_w": 1.0,
        "thermal_capacitance_j_per_c": 10.0,
    })
    cluster = ClusterSpec.from_mapping({
        "cluster": {"id": "hot-long", "nodes": [node_data]},
    })
    long_work = work("long", compute=3200)
    long_work["cpu_cores"] = 8
    long_work["parallel_fraction"] = 1
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "long", "tasks": [long_work]},
    })

    fine = Simulator(
        cluster, workflow, AssignedPolicy({"long": "only"}),
        thermal_step_s=0.05,
    ).run()
    coarse = Simulator(
        cluster, workflow, AssignedPolicy({"long": "only"}),
        thermal_step_s=0.5,
    ).run()

    assert coarse.makespan_s == pytest.approx(fine.makespan_s, rel=0.01)
    assert coarse.nodes["only"].peak_temperature_c == pytest.approx(
        fine.nodes["only"].peak_temperature_c, rel=0.01)


def test_long_contended_work_makes_representable_event_progress():
    cases = load_benchmark_suite("scenarios/benchmark-suite.yaml")
    workflow = next(
        case.workflow for case in cases
        if case.split == "holdout"
        and case.resource_pattern == "cpu"
        and case.topology == "independent"
        and case.work_scale == 4.0
    )
    cluster = load_cluster("scenarios/clusters/local-i5-8300h.yaml")

    result = Simulator(
        cluster,
        workflow,
        create_policy("heft"),
        thermal_step_s=60.0,
        seed=101,
    ).run()

    assert len(result.tasks) == 10
    assert result.makespan_s > 4 * 3600