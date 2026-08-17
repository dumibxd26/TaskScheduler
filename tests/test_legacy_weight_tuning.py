import pytest

from models.enums import NodeType, TaskClass
from models.profile_store import ProfileStore
from models.cluster import Node
from models.workload import TaskInstance, TaskTemplate
from services.scheduler import (
    LEGACY_TUNED_WEIGHTS,
    LEGACY_V1_WEIGHTS,
    LegacyWeights,
    PlacementAlgorithm,
    legacy_weights_for_policy,
)
from simulation.engine import Simulator
from simulation.legacy_factor_stress import run_factor_stresses
from simulation.legacy_weights import LegacyScoringPolicy, build_warm_profile
from simulation.policies import LegacyWeightedPolicy, create_policy
from simulation.schema import load_cluster, load_workflow
from simulation.weight_search import (
    ABSOLUTE_WEIGHT_SCALE,
    BASELINE_WEIGHTS,
    RunMetrics,
    generate_candidates,
    leave_one_factor_out,
    normalize_weights,
    score_metrics,
)


def test_default_weight_object_preserves_existing_score():
    store = ProfileStore()
    node = Node("node", NodeType.CPU_OPT, 4, 4096, 4, 4096)
    node.warm_images.add("task:v1")
    task = TaskInstance("task", "workflow", "template")
    template = TaskTemplate(
        "template", "task", TaskClass.CPU_BOUND,
        1, 128, "task:v1", [NodeType.CPU_OPT],
    )

    score = PlacementAlgorithm(store, LegacyWeights()).score_node(
        task, template, node)

    assert score["type_affinity"] == 15
    assert score["warm_image"] == 10
    assert score["availability"] == 25


def test_custom_weights_change_individual_factors():
    store = ProfileStore()
    node = Node("node", NodeType.CPU_OPT, 4, 4096, 4, 4096)
    node.warm_images.add("task:v1")
    task = TaskInstance("task", "workflow", "template")
    template = TaskTemplate(
        "template", "task", TaskClass.CPU_BOUND,
        1, 128, "task:v1", [NodeType.CPU_OPT],
    )
    weights = LegacyWeights(type_affinity=8, warm_image=3)

    score = PlacementAlgorithm(store, weights).score_node(
        task, template, node)

    assert score["type_affinity"] == 4
    assert score["warm_image"] == 3


def test_named_weight_variants_preserve_v1_and_expose_tuned_vector():
    assert legacy_weights_for_policy("legacy") == LEGACY_V1_WEIGHTS
    assert legacy_weights_for_policy("legacy-v1") == LEGACY_V1_WEIGHTS
    assert legacy_weights_for_policy("legacy-tuned") == LEGACY_TUNED_WEIGHTS
    assert LEGACY_TUNED_WEIGHTS != LEGACY_V1_WEIGHTS


def test_simulation_factory_keeps_old_legacy_and_adds_exact_variants():
    cluster = load_cluster("scenarios/clusters/balanced-heterogeneous-4.yaml")
    workflow = load_workflow("scenarios/workflows/linear-5.yaml")

    historical = create_policy("legacy")
    original = create_policy(
        "legacy-v1", cluster=cluster, workflow=workflow)
    tuned = create_policy(
        "legacy-tuned", cluster=cluster, workflow=workflow)

    assert isinstance(historical, LegacyWeightedPolicy)
    assert isinstance(original, LegacyScoringPolicy)
    assert original.weights == LEGACY_V1_WEIGHTS
    assert tuned.weights == LEGACY_TUNED_WEIGHTS


def test_legacy_scoring_policy_completes_existing_scenario():
    cluster = load_cluster("scenarios/clusters/balanced-heterogeneous-4.yaml")
    workflow = load_workflow("scenarios/workflows/linear-5.yaml")
    profile = build_warm_profile(cluster, workflow, seed=7)

    def run():
        return Simulator(
            cluster,
            workflow,
            LegacyScoringPolicy(LegacyWeights(), profile),
            seed=7,
        ).run()

    result = run()
    replay = run()

    assert result.makespan_s > 0
    assert len(result.tasks) == len(workflow.tasks)
    assert replay.makespan_s == result.makespan_s
    assert {
        task_id: task.node_id for task_id, task in replay.tasks.items()
    } == {
        task_id: task.node_id for task_id, task in result.tasks.items()
    }


def test_candidates_are_seeded_and_scale_normalized():
    first = generate_candidates(8, seed=11)
    replay = generate_candidates(8, seed=11)

    assert first == replay
    assert first[0] == BASELINE_WEIGHTS
    assert all(
        abs(sum(abs(value) for value in weights.__dict__.values())
            - ABSOLUTE_WEIGHT_SCALE) < 1e-9
        for weights in first
    )
    assert normalize_weights(BASELINE_WEIGHTS.__dict__) == BASELINE_WEIGHTS


def test_factor_ablations_remove_one_factor_and_preserve_scale():
    variants = leave_one_factor_out(LEGACY_TUNED_WEIGHTS)

    assert set(variants) == {
        f"without-{field.replace('_', '-')}"
        for field in LEGACY_TUNED_WEIGHTS.__dict__
    }
    for name, weights in variants.items():
        omitted = name.removeprefix("without-").replace("-", "_")
        assert getattr(weights, omitted) == 0
        assert sum(abs(value) for value in weights.__dict__.values()) == pytest.approx(
            ABSOLUTE_WEIGHT_SCALE)


def test_dormant_factor_stresses_change_placement_decisions():
    report = run_factor_stresses()

    assert report["all_factors_change_choice"] is True
    assert report["stresses"]["warm_image"]["full_choice"] == "warm"
    assert report["stresses"]["failure_penalty"]["full_choice"] == "healthy"
    assert report["stresses"]["memory_pressure"]["full_choice"] == "safe"


def test_baseline_objective_is_one():
    metrics = [RunMetrics(
        case="case",
        makespan_s=10,
        mean_response_s=7,
        energy_j=100,
        network_transfer_mib=0,
        deadline_s=12,
        lateness_s=0,
        deadline_missed=False,
        task_retries=0,
        node_failures=0,
        peak_temperature_c=50,
    )]

    score = score_metrics(BASELINE_WEIGHTS, metrics, metrics)

    assert abs(score.objective - 1.0) < 1e-12