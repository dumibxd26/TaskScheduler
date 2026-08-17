from pathlib import Path

from analyze_policy_tradeoffs import pareto_policies
from simulation.engine import Simulator
from simulation.experiments import (
    ExperimentRecord,
    analyze_records,
    analyze_workload_records,
    run_experiment_matrix,
    run_workload_matrix,
    sample_cluster_uncertainty,
    static_assignment_oracle,
    workflow_lower_bound_s,
    write_experiment_outputs,
    write_workload_outputs,
)
from simulation.policies import FCFSPolicy
from simulation.schema import ClusterSpec, WorkflowSpec, load_cluster, load_workflow
from tests.test_simulation_engine import work
from tests.test_simulation_policies import heterogeneous_cluster


ROOT = Path(__file__).parents[1]


def test_lower_bound_never_exceeds_a_simulated_schedule():
    cluster = load_cluster(ROOT / "scenarios" / "clusters" / "homogeneous-4.yaml")
    workflow = load_workflow(ROOT / "scenarios" / "workflows" / "diamond.yaml")

    result = Simulator(cluster, workflow, FCFSPolicy()).run()

    assert workflow_lower_bound_s(cluster, workflow) <= result.makespan_s


def test_static_oracle_finds_the_fast_node_for_one_task():
    cluster = heterogeneous_cluster()
    workflow = WorkflowSpec.from_mapping({
        "workflow": {"id": "one", "tasks": [work("only", compute=16)]},
    })

    result = static_assignment_oracle(cluster, workflow)

    assert result is not None
    assert result.tasks["only"].node_id == "fast"


def test_matrix_outputs_are_machine_readable(tmp_path):
    records = run_experiment_matrix(
        [ROOT / "scenarios" / "clusters" / "homogeneous-4.yaml"],
        [ROOT / "scenarios" / "workflows" / "linear-5.yaml"],
        ["fcfs", "eft"],
        [7],
    )

    summary = analyze_records(records)
    paths = write_experiment_outputs(tmp_path, records)

    assert len(records) == 2
    assert summary["record_count"] == 2
    assert {row["policy"] for row in summary["ranking"]} == {"fcfs", "eft"}
    assert all(path.exists() for path in paths.values())


def test_calibration_uncertainty_is_seeded_and_preserves_structure():
    cluster = load_cluster(
        ROOT / "scenarios" / "clusters" / "local-i5-8300h.yaml")

    first = sample_cluster_uncertainty(cluster, seed=4)
    replay = sample_cluster_uncertainty(cluster, seed=4)
    other = sample_cluster_uncertainty(cluster, seed=5)

    assert first == replay
    assert first.nodes[0].cpu.physical_cores == cluster.nodes[0].cpu.physical_cores
    assert first.nodes[0].cpu.calibrated_gflops_per_core != (
        other.nodes[0].cpu.calibrated_gflops_per_core)


def test_multi_workflow_matrix_reports_preemption_ablation(tmp_path):
    records = run_workload_matrix(
        [ROOT / "scenarios" / "clusters" / "local-i5-8300h.yaml"],
        [ROOT / "scenarios" / "workloads" / "priority-burst.yaml"],
        ["adaptive-no-preempt", "adaptive"],
        [0],
    )

    summary = analyze_workload_records(records)
    paths = write_workload_outputs(tmp_path, records)

    by_policy = {record.policy: record for record in records}
    assert by_policy["adaptive"].task_preemptions >= 1
    assert by_policy["adaptive-no-preempt"].task_preemptions == 0
    assert summary["record_count"] == 2
    assert all(path.exists() for path in paths.values())


def test_oracle_is_a_reference_not_a_deployable_recommendation():
    records = run_experiment_matrix(
        [ROOT / "scenarios" / "clusters" / "homogeneous-4.yaml"],
        [ROOT / "scenarios" / "workflows" / "critical-deadline.yaml"],
        ["fcfs", "eft"],
        [0],
        oracle_max_tasks=3,
    )

    summary = analyze_records(records)

    assert {row["policy"] for row in summary["ranking"]} == {"fcfs", "eft"}
    assert [row["policy"] for row in summary["reference_results"]] == [
        "oracle-static"
    ]
    recommended = summary["workflow_family_recommendations"]["priority"]
    assert "oracle-static" not in recommended["policies"]


def test_oracle_does_not_change_deployable_regret_denominator():
    common = {
        "cluster_id": "cluster",
        "cluster_family": "cluster-family",
        "workflow_id": "workflow",
        "workflow_family": "workflow-family",
        "seed": 0,
        "lower_bound_s": 1.0,
        "energy_j": 10.0,
        "network_transfer_mib": 0.0,
        "cold_starts": 0,
        "task_retries": 0,
        "node_failures": 0,
        "peak_temperature_c": 40.0,
        "deadline_s": None,
        "deadline_met": None,
        "lateness_s": 0.0,
    }
    records = [
        ExperimentRecord(
            **common, policy="heft", makespan_s=10.0, bound_ratio=10.0),
        ExperimentRecord(
            **common, policy="eft", makespan_s=12.0, bound_ratio=12.0),
        ExperimentRecord(
            **common, policy="oracle-static", makespan_s=1.0, bound_ratio=1.0),
    ]

    summary = analyze_records(records)
    by_policy = {row["policy"]: row for row in summary["ranking"]}

    assert by_policy["heft"]["mean_regret"] == 0
    assert abs(by_policy["eft"]["mean_regret"] - 0.2) < 1e-12
    assert summary["reference_results"][0]["mean_regret"] == 0


def test_tradeoff_frontier_includes_mean_response_objective():
    common = {
        "p95_response_regret": 0.2,
        "median_makespan_s": 30.0,
        "median_energy_j": 100.0,
        "deadline_hit_rate": 0.8,
    }
    summary = {
        "lower-mean": {
            **common,
            "mean_mean_response_s": 10.0,
            "median_mean_response_s": 20.0,
        },
        "lower-median": {
            **common,
            "mean_mean_response_s": 12.0,
            "median_mean_response_s": 15.0,
        },
    }

    assert pareto_policies(summary) == ["lower-mean", "lower-median"]