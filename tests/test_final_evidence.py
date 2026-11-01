import pytest

from analyze_final_evidence import (
    _bootstrap_mean_ci,
    _closed_summary,
    _contrasts,
    _cross_leaders,
    _distinct_outcomes,
    _metric_leaders,
    _paired,
)


def test_paired_comparison_counts_wins_ties_and_losses():
    rows = [
        {"policy": "left", "cluster_id": "c", "workflow_id": "a", "seed": 0,
         "makespan_s": 8},
        {"policy": "right", "cluster_id": "c", "workflow_id": "a", "seed": 0,
         "makespan_s": 10},
        {"policy": "left", "cluster_id": "c", "workflow_id": "b", "seed": 0,
         "makespan_s": 10},
        {"policy": "right", "cluster_id": "c", "workflow_id": "b", "seed": 0,
         "makespan_s": 10},
        {"policy": "left", "cluster_id": "c", "workflow_id": "d", "seed": 0,
         "makespan_s": 12},
        {"policy": "right", "cluster_id": "c", "workflow_id": "d", "seed": 0,
         "makespan_s": 10},
    ]

    result = _paired(
        rows, "makespan_s", "left", "right",
        ("cluster_id", "workflow_id", "seed"))

    assert result["left_wins"] == 1
    assert result["ties"] == 1
    assert result["left_losses"] == 1


def test_bootstrap_interval_is_seeded_and_ordered():
    first = _bootstrap_mean_ci([0.8, 0.9, 1.0, 1.1])
    replay = _bootstrap_mean_ci([0.8, 0.9, 1.0, 1.1])

    assert first == replay
    assert first[0] <= first[1]


def test_closed_summary_keeps_oracle_out_of_deployable_regret():
    common = {
        "cluster_id": "cluster",
        "workflow_id": "workflow",
        "seed": 0,
        "energy_j": 10,
        "network_transfer_mib": 0,
        "deadline_met": None,
    }
    rows = [
        {**common, "policy": "heft", "makespan_s": 10},
        {**common, "policy": "eft", "makespan_s": 12},
        {**common, "policy": "oracle-static", "makespan_s": 1},
    ]

    summary = _closed_summary(rows)

    assert summary["reference_records"] == 1
    assert [row["policy"] for row in summary["ranking"]] == ["heft", "eft"]
    assert summary["ranking"][0]["mean_regret"] == 0
    assert abs(summary["ranking"][1]["mean_regret"] - 0.2) < 1e-12
    assert summary["reference_comparison"][0]["policy"] == "heft"
    assert summary["reference_comparison"][0]["mean_regret"] == 9


def test_metric_leaders_preserve_exact_ties():
    ranking = [
        {"policy": "a", "cost": 1.0, "score": 0.5},
        {"policy": "b", "cost": 1.0, "score": 0.7},
        {"policy": "c", "cost": 2.0, "score": 0.6},
    ]

    leaders = _metric_leaders(ranking, {"cost": False, "score": True})

    assert leaders["cost"] == {"policies": ["a", "b"], "value": 1.0}
    assert leaders["score"] == {"policies": ["b"], "value": 0.7}

def test_distinct_outcomes_detects_seeds_that_change_nothing():
    rows = [
        {"policy": "heft", "cluster_id": "fixed", "workflow_id": "w", "seed": seed,
         "makespan_s": 10.0}
        for seed in range(3)
    ] + [
        {"policy": "heft", "cluster_id": "sampled", "workflow_id": "w", "seed": seed,
         "makespan_s": 10.0 + seed}
        for seed in range(3)
    ]

    result = _distinct_outcomes(rows, ("cluster_id", "workflow_id"), "makespan_s")

    assert result["fixed"]["mean_distinct"] == 1
    assert result["sampled"]["mean_distinct"] == 3


def test_contrasts_skip_policies_missing_from_a_phase():
    rows = [
        {"policy": "heft", "cluster_id": "c", "workflow_id": "w", "seed": 0,
         "makespan_s": 9.0},
        {"policy": "eft", "cluster_id": "c", "workflow_id": "w", "seed": 0,
         "makespan_s": 10.0},
    ]

    result = _contrasts(rows, (("heft", "eft"), ("heft-ra", "heft")), "makespan_s",
                        ("cluster_id", "workflow_id", "seed"))

    assert [(row["left"], row["right"]) for row in result] == [("heft", "eft")]
    assert result[0]["mean_ratio"] == 0.9


def test_cross_leaders_use_per_case_regret_and_keep_ties():
    rows = []
    for seed, (fast, slow) in enumerate(((10.0, 12.0), (8.0, 8.0))):
        for policy, makespan in (("heft-ra", fast), ("heft", slow),
                                 ("oracle-static", 1.0)):
            rows.append({
                "cluster_id": "c", "workflow_id": "w", "seed": seed,
                "workflow_family": "fan-out", "cluster_family": "edge",
                "policy": policy, "makespan_s": makespan,
            })
    rows.append({
        "cluster_id": "d", "workflow_id": "w", "seed": 0,
        "workflow_family": "fan-out", "cluster_family": "homogeneous",
        "policy": "heft", "makespan_s": 5.0,
    })
    rows.append({
        "cluster_id": "d", "workflow_id": "w", "seed": 0,
        "workflow_family": "fan-out", "cluster_family": "homogeneous",
        "policy": "heft-ra", "makespan_s": 5.0,
    })

    table = _cross_leaders(
        rows, ("cluster_id", "workflow_id", "seed"), "makespan_s",
        "workflow_family", "cluster_family")

    edge = table["fan-out"]["edge"]
    assert edge["policies"] == ["heft-ra"]
    assert edge["mean_regret"] == 0.0
    assert edge["runner_up_mean_regret"] == pytest.approx(0.1)
    assert edge["cases"] == 2
    assert table["fan-out"]["homogeneous"]["policies"] == ["heft", "heft-ra"]
    assert table["fan-out"]["homogeneous"]["runner_up_mean_regret"] is None
