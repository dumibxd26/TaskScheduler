import pytest

from simulation.schema import (
    ClusterSpec,
    ModelValidationError,
    WorkflowSpec,
    validate_task_compatibility,
)


def node(node_id="node-a", architecture="amd64", memory_mib=12_288):
    return {
        "id": node_id,
        "type": "CPU_OPT",
        "cooling_class": "STANDARD",
        "cpu": {
            "architecture": architecture,
            "microarchitecture": "Coffee Lake",
            "physical_cores": 4,
            "threads_per_core": 2,
            "base_frequency_ghz": 2.3,
            "max_frequency_ghz": 4.0,
            "calibrated_gflops_per_core": 8.0,
            "l3_cache_mib": 8,
            "idle_power_w": 8,
            "max_power_w": 45,
            "vector_extensions": ["AVX2"],
        },
        "memory": {
            "capacity_mib": memory_mib,
            "calibrated_bandwidth_mib_s": 18_000,
            "latency_ns": 85,
            "channels": 2,
        },
        "storage": {
            "capacity_mib": 476_000,
            "sequential_read_mib_s": 2_500,
            "sequential_write_mib_s": 2_000,
            "random_read_iops": 250_000,
            "random_write_iops": 200_000,
        },
        "thermal": {
            "ambient_c": 23,
            "initial_c": 42,
            "throttle_c": 100,
            "shutdown_c": 105,
            "thermal_resistance_c_per_w": 1.35,
            "thermal_capacitance_j_per_c": 45,
        },
    }


def task(task_id="work", architecture="amd64", memory_mib=512):
    return {
        "id": task_id,
        "class": "CPU_BOUND",
        "image": "example:v1",
        "supported_architectures": [architecture],
        "cpu_cores": 1,
        "memory_mib": memory_mib,
        "working_set_mib": 256,
        "compute_gflop": 40,
    }


def test_loads_connected_heterogeneous_cluster_and_dag():
    cluster = ClusterSpec.from_mapping({
        "schema_version": 1,
        "cluster": {
            "id": "mixed-two",
            "network_vertices": ["switch"],
            "nodes": [node(), node("node-b", architecture="arm64", memory_mib=8192)],
            "links": [
                {"id": "a-switch", "endpoints": ["node-a", "switch"],
                 "bandwidth_mib_s": 110, "latency_ms": 0.3},
                {"id": "b-switch", "endpoints": ["node-b", "switch"],
                 "bandwidth_mib_s": 80, "latency_ms": 1.2},
            ],
        },
    })
    workflow = WorkflowSpec.from_mapping({
        "workflow": {
            "id": "pipeline",
            "tasks": [task("extract"), task("report")],
            "dependencies": [
                {"parent": "extract", "child": "report", "data_mib": 100},
            ],
        },
    })

    assert cluster.nodes_by_id["node-a"].cpu.logical_cpus == 8
    assert cluster.nodes_by_id["node-a"].thermal.time_constant_s == pytest.approx(60.75)
    assert workflow.tasks_by_id["report"].compute_gflop == 40
    validate_task_compatibility(cluster, workflow)


def test_rejects_unknown_network_endpoint():
    with pytest.raises(ModelValidationError, match="unknown endpoints"):
        ClusterSpec.from_mapping({
            "cluster": {
                "id": "bad-network",
                "nodes": [node()],
                "links": [{"id": "bad", "endpoints": ["node-a", "missing"],
                           "bandwidth_mib_s": 10}],
            },
        })


def test_rejects_cyclic_workflow():
    with pytest.raises(ModelValidationError, match="contains a cycle"):
        WorkflowSpec.from_mapping({
            "workflow": {
                "id": "cycle",
                "tasks": [task("a"), task("b")],
                "dependencies": [
                    {"parent": "a", "child": "b"},
                    {"parent": "b", "child": "a"},
                ],
            },
        })


def test_rejects_workflow_without_compatible_capacity():
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "tiny", "nodes": [node()]}})
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "too-large", "tasks": [task(memory_mib=20_000)]},
    })

    with pytest.raises(ModelValidationError, match="no architecture/capacity-compatible"):
        validate_task_compatibility(cluster, workflow)