"""Regression tests for simulator defects found during the correction audit."""

import itertools
import math
from dataclasses import replace

import pytest

from models.enums import PriorityClass
from run_experiment_lab import _seeds
from simulation.engine import Simulator
from simulation.experiments import (
    WorkloadExperimentRecord,
    _percentile,
    analyze_workload_records,
    sample_cluster_uncertainty,
    workflow_lower_bound_s,
)
from simulation.legacy_weights import LegacyScoringPolicy
from simulation.policies import (
    KubeDefaultPolicy,
    _available,
    _best_unit_placement,
    _fits,
    _planned_tasks,
    create_policy,
)
from simulation.schema import (
    ClusterSpec,
    ModelValidationError,
    TaskSpec,
    WorkflowSpec,
    load_cluster,
    load_workflow,
)
from simulation.workloads import WorkflowSubmission, WorkloadSpec
from tests.test_simulation_engine import work
from tests.test_simulation_schema import node, task


def scenario(project_root, kind, name):
    return project_root / "scenarios" / kind / f"{name}.yaml"


def test_lower_bound_accounts_for_cache_reuse(project_root):
    cluster = load_cluster(scenario(project_root, "clusters", "local-i5-8300h"))
    workflow = load_workflow(scenario(project_root, "workflows", "cache-sensitive"))

    bound = workflow_lower_bound_s(cluster, workflow)
    result = Simulator(cluster, workflow, create_policy("eft"), seed=0).run()

    # The previous bound charged raw memory traffic and exceeded the schedule.
    assert bound <= result.makespan_s + 1e-9


@pytest.mark.parametrize("cluster_name,workflow_name", [
    ("local-i5-8300h", "cache-sensitive"),
    ("balanced-heterogeneous-4", "memory-heavy-6"),
    ("asymmetric-edge-5", "data-heavy-pipeline"),
    ("homogeneous-4", "wide-12"),
    ("unreliable-thermal-3", "checkpoint-long"),
])
def test_lower_bound_never_exceeds_any_policy(project_root, cluster_name,
                                              workflow_name):
    cluster = load_cluster(scenario(project_root, "clusters", cluster_name))
    workflow = load_workflow(scenario(project_root, "workflows", workflow_name))
    bound = workflow_lower_bound_s(cluster, workflow)
    for policy in ("eft", "heft", "fcfs"):
        result = Simulator(cluster, workflow, create_policy(policy), seed=3).run()
        assert bound <= result.makespan_s + 1e-9


def _failure_times(cluster, workflow, policy_name):
    simulator = Simulator(
        cluster, workflow, create_policy(policy_name, seed=3), seed=3)
    times = []
    original = simulator._fail_node

    def record(node_id):
        if simulator.nodes[node_id].available:
            times.append((round(simulator.clock.now, 9), node_id))
        original(node_id)

    simulator._fail_node = record
    simulator.run()
    return times


def test_stochastic_failures_are_identical_across_policies(project_root):
    base = load_cluster(scenario(project_root, "clusters", "unreliable-thermal-3"))
    cluster = replace(base, nodes=tuple(
        replace(node_spec, failure_rate_per_hour=240.0, mean_repair_s=2.0)
        if node_spec.node_id == "flaky-edge" else node_spec
        for node_spec in base.nodes))
    workflow = load_workflow(scenario(project_root, "workflows", "mixed-etl"))

    fcfs = _failure_times(cluster, workflow, "fcfs")
    heft = _failure_times(cluster, workflow, "heft")

    # Failures inside the shorter run must coincide exactly (common random
    # numbers); previously jitter draws shifted the failure stream.
    horizon = min(fcfs[-1][0], heft[-1][0])
    assert [item for item in fcfs if item[0] <= horizon] == [
        item for item in heft if item[0] <= horizon]
    assert len(fcfs) >= 2


def test_branch_and_bound_rejects_negative_scores(project_root):
    cluster = load_cluster(scenario(project_root, "clusters", "homogeneous-4"))
    workflow = load_workflow(scenario(project_root, "workflows", "gang-4"))
    simulator = Simulator(cluster, workflow, create_policy("heft"))
    for task_id in simulator.tasks:
        simulator._mark_ready(task_id)
    snapshot = simulator._snapshot()
    free_cpu, free_memory = _available(snapshot)

    with pytest.raises(ValueError, match="non-negative"):
        _best_unit_placement(
            snapshot, list(workflow.tasks), free_cpu, free_memory,
            _planned_tasks(snapshot), lambda *_: -1.0)


def test_exact_legacy_gang_placement_matches_exhaustive_optimum(project_root):
    cluster = load_cluster(scenario(project_root, "clusters", "asymmetric-edge-5"))
    workflow = load_workflow(scenario(project_root, "workflows", "gang-4"))
    policy = create_policy("legacy-v1", cluster=cluster, workflow=workflow)
    assert isinstance(policy, LegacyScoringPolicy)
    simulator = Simulator(cluster, workflow, policy)
    for task_id in simulator.tasks:
        simulator._mark_ready(task_id)
    snapshot = simulator._snapshot()
    free_cpu, free_memory = _available(snapshot)
    planned = _planned_tasks(snapshot)
    unit = list(workflow.tasks)

    def score(task_spec, node_view, co_runners):
        return policy._score(snapshot, task_spec, node_view, co_runners)

    def total(assignment):
        cpu, memory = dict(free_cpu), dict(free_memory)
        plan = {key: list(value) for key, value in planned.items()}
        value = 0.0
        for task_spec, node_id in zip(unit, assignment):
            node_view = snapshot.nodes[node_id]
            if not _fits(task_spec, node_view, cpu, memory):
                return None
            value += score(task_spec, node_view, tuple(plan[node_id]))
            cpu[node_id] -= task_spec.cpu_cores
            memory[node_id] -= task_spec.memory_mib
            plan[node_id].append(task_spec)
        return value

    chosen = _best_unit_placement(
        snapshot, unit, free_cpu, free_memory, planned, score)
    exhaustive = min(
        value for assignment in itertools.product(snapshot.nodes, repeat=len(unit))
        if (value := total(assignment)) is not None)

    assert total([placement.node_id for placement in chosen]) == pytest.approx(
        exhaustive)


def test_best_effort_priority_is_not_replaced_by_workflow_priority():
    best_effort = task("background")
    best_effort["priority"] = "BEST_EFFORT"
    spec = TaskSpec.from_mapping(best_effort)
    workflow = WorkflowSpec(
        workflow_id="critical", tasks=(spec,), priority=PriorityClass.CRITICAL)
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "one", "nodes": [node("a")]}})

    simulator = Simulator(cluster, workflow, create_policy("adaptive"))
    runtime = simulator.tasks["background"]

    assert spec.priority is PriorityClass.BEST_EFFORT
    assert simulator._task_priority(runtime) is PriorityClass.BEST_EFFORT


def test_workload_compile_keeps_best_effort_submission_priority():
    workflow = WorkflowSpec(
        workflow_id="wf",
        tasks=(TaskSpec.from_mapping(task("only")),),
        priority=PriorityClass.CRITICAL,
    )
    workload = WorkloadSpec(
        workload_id="mix",
        submissions=(WorkflowSubmission(
            "low", workflow, priority=PriorityClass.BEST_EFFORT),),
    )

    compiled = workload.compile()

    assert compiled.tasks[0].priority is PriorityClass.BEST_EFFORT


@pytest.mark.parametrize("raw,expected", [
    (False, False), ("false", False), ("No", False), (True, True), ("true", True)])
def test_boolean_fields_parse_strings_explicitly(raw, expected):
    data = task("t")
    data["preemptable"] = raw
    data["checkpointable"] = raw

    spec = TaskSpec.from_mapping(data)

    assert spec.preemptable is expected
    assert spec.checkpointable is expected


def test_invalid_boolean_values_are_rejected():
    data = task("t")
    data["preemptable"] = "sometimes"

    with pytest.raises(ModelValidationError, match="boolean"):
        TaskSpec.from_mapping(data)


def test_thermal_and_timer_events_always_advance_the_clock():
    cluster = ClusterSpec.from_mapping({"cluster": {"id": "one", "nodes": [node("a")]}})
    workflow = WorkflowSpec.from_mapping({"workflow": {"id": "w", "tasks": [work("t")]}})
    simulator = Simulator(cluster, workflow, create_policy("fcfs"))
    simulator.clock.advance_to(5.0e7)

    assert simulator._future(1e-9) > simulator.clock.now
    assert simulator._future(0.0) > simulator.clock.now


def test_uncertainty_sampling_keeps_power_curve_ordered():
    data = node("tight")
    data["cpu"]["idle_power_w"] = 40
    data["cpu"]["max_power_w"] = 41
    cluster = ClusterSpec.from_mapping({
        "cluster": {
            "id": "tight",
            "calibration": {"status": "estimated", "uncertainty_fraction": 0.5},
            "nodes": [data],
        },
    })

    for seed in range(200):
        sampled = sample_cluster_uncertainty(cluster, seed).nodes[0].cpu
        assert sampled.max_power_w >= sampled.idle_power_w


def test_seed_ranges_reject_empty_selections():
    assert _seeds("0:3") == [0, 1, 2]
    with pytest.raises(ValueError, match="empty"):
        _seeds("5:5")


def test_percentile_validates_inputs():
    assert _percentile([3.0, 1.0, 2.0], 0.5) == 2.0
    with pytest.raises(ValueError):
        _percentile([], 0.5)
    with pytest.raises(ValueError):
        _percentile([1.0], 1.5)


def test_kube_default_prefers_least_allocated_node_and_ignores_speed():
    fast = node("fast")
    slow = node("slow")
    fast["cpu"]["calibrated_gflops_per_core"] = 40
    slow["cpu"]["calibrated_gflops_per_core"] = 4
    for data in (fast, slow):
        data["initial_warm_images"] = ["work:v1"]
    cluster = ClusterSpec.from_mapping({
        "cluster": {
            "id": "speed",
            "network_vertices": ["switch"],
            "nodes": [fast, slow],
            "links": [
                {"id": "fast-link", "endpoints": ["fast", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 0},
                {"id": "slow-link", "endpoints": ["slow", "switch"],
                 "bandwidth_mib_s": 100, "latency_ms": 0},
            ],
        },
    })
    tasks = [work(f"t{index}", compute=40) for index in range(2)]
    workflow = WorkflowSpec.from_mapping({"workflow": {"id": "w", "tasks": tasks}})

    result = Simulator(cluster, workflow, KubeDefaultPolicy(seed=1)).run()

    # LeastAllocated spreads two identical pods across both nodes even though
    # one node is ten times slower; EFT would put both on the fast node.
    assert {task_result.node_id for task_result in result.tasks.values()} == {
        "fast", "slow"}
    eft = Simulator(cluster, workflow, create_policy("eft")).run()
    assert {task_result.node_id for task_result in eft.tasks.values()} == {"fast"}


def _workload_record(policy, seed, response, deadline):
    return WorkloadExperimentRecord(
        cluster_id="c", cluster_family="f", workload_id="w", policy=policy,
        seed=seed, makespan_s=10.0, mean_response_s=response,
        p95_response_s=response, deadline_hit_rate=deadline, deadline_misses=0,
        task_preemptions=0, task_retries=0, node_failures=0, energy_j=1.0,
        network_transfer_mib=0.0)


def test_workload_ranking_uses_response_regret_and_reports_leaders():
    records = [
        _workload_record("fast", 0, 10.0, 0.5),
        _workload_record("fast", 1, 10.0, 0.5),
        _workload_record("punctual", 0, 20.0, 1.0),
        _workload_record("punctual", 1, 20.0, 1.0),
    ]

    summary = analyze_workload_records(records)

    assert [row["policy"] for row in summary["ranking"]] == ["fast", "punctual"]
    assert summary["objective_leaders"]["deadline_hit_rate"]["policies"] == [
        "punctual"]
    assert math.isclose(summary["ranking"][1]["mean_response_regret"], 1.0)
