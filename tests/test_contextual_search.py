from pathlib import Path

from simulation.contextual_search import (
    EvaluationMetric,
    ObjectiveScore,
    PolicyCandidate,
    _select_validated_specialist,
    evaluate_candidate,
    generate_adaptive_candidates,
    score_metrics,
)
from simulation.benchmark_suite import load_benchmark_suite
from simulation.schema import load_cluster


ROOT = Path(__file__).parents[1]


def test_adaptive_candidates_are_seeded_and_include_safe_anchors():
    first = generate_adaptive_candidates(12, seed=9)
    replay = generate_adaptive_candidates(12, seed=9)

    assert first == replay
    assert first[0].name == "adaptive-heft-equivalent"
    assert all(value == 0.0 for value in first[0].adaptive_weights.__dict__.values())
    assert first[1].name == "adaptive-default"
    assert len({candidate.adaptive_weights for candidate in first}) == 12


def test_objective_is_one_for_identical_aligned_metrics():
    metrics = [EvaluationMetric(
        key="case",
        context="cpu:linear",
        makespan_s=100,
        energy_j=500,
        transfer_mib=20,
    )]

    score = score_metrics(metrics, metrics)

    assert score.objective == 1.0
    assert set(score.components.values()) == {1.0}


def test_heft_equivalent_adaptive_matches_heft_on_small_generated_slice():
    cases = load_benchmark_suite(ROOT / "scenarios" / "benchmark-suite.yaml")
    case = next(
        case for case in cases
        if case.split == "train" and case.resource_pattern == "cpu"
        and case.topology == "fanout" and case.task_count == 4
        and case.work_scale == 0.5
    )
    cluster = load_cluster(
        ROOT / "scenarios" / "clusters" / "balanced-heterogeneous-4.yaml")
    candidates = generate_adaptive_candidates(2)

    adaptive = evaluate_candidate(
        candidates[0], [cluster], [case], [3], thermal_step_s=120.0)
    heft = evaluate_candidate(
        PolicyCandidate("heft", "heft"),
        [cluster], [case], [3], thermal_step_s=120.0)

    assert adaptive[0].makespan_s == heft[0].makespan_s


def test_validation_ties_and_tail_regressions_keep_baseline():
    baseline = PolicyCandidate("heft", "heft")
    tied = PolicyCandidate("adaptive", "adaptive")
    risky = PolicyCandidate("eft", "eft")
    components = {
        "mean_makespan_ratio": 1.0,
        "p95_makespan_ratio": 1.0,
        "mean_energy_ratio": 1.0,
        "mean_transfer_ratio": 1.0,
    }
    scores = {
        "heft": ObjectiveScore(1.0, components, 10),
        "adaptive": ObjectiveScore(1.0, components, 10),
        "eft": ObjectiveScore(0.98, {
            **components,
            "mean_makespan_ratio": 0.97,
            "p95_makespan_ratio": 1.01,
        }, 10),
    }

    selected = _select_validated_specialist(
        [baseline, tied, risky], scores, "heft")

    assert selected == baseline