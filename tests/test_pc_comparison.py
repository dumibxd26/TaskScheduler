import json

import pytest

from compare_pc_results import compare_records, compare_values, live_rows, size_label


def test_comparison_distinguishes_roundoff_structural_and_boolean_changes():
    result = compare_values({"x": 1.0}, {"x": 1.0 + 1e-12})
    assert result["exact_differences"] == 1
    assert result["outside_tolerance"] == 0
    assert compare_values({"x": True}, {"x": 1})["outside_tolerance"] == 1
    assert compare_values({"x": 1}, {})["structural_differences"] == 1
    assert compare_values([1], [1, 2])["structural_differences"] == 1
    assert compare_values(float("nan"), float("nan"))["outside_tolerance"] == 1


def test_records_are_paired_by_case_not_file_order_and_missing_cases_fail(tmp_path):
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    rows = [{"cluster_id": "c", "workflow_id": "w", "seed": s,
             "policy": "heft", "makespan_s": 10.0 + s} for s in range(2)]
    old.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    new.write_text("\n".join(json.dumps(row) for row in reversed(rows)), encoding="utf-8")
    assert compare_records(old, new)["exact_records"] == 2
    new.write_text(json.dumps(rows[0]), encoding="utf-8")
    result = compare_records(old, new)
    assert result["missing_keys"] == 1
    assert not result["complete_equivalent"]
    new.write_text("\n".join([json.dumps(rows[0])] * 2), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate record"):
        compare_records(old, new)


def test_live_comparison_requires_matched_schedule_and_repetitions():
    trial = {"batch": "dag", "policy": "heft", "repetition": 1,
             "makespan_seconds": 10, "mean_response_seconds": 8,
             "p95_response_seconds": 10, "workflows": []}
    old = {"profile": "balanced", "batches": ["dag"], "policies": ["heft"],
           "batch_definitions": {}, "schedule": [[1, "dag", "heft"]], "trials": [trial]}
    new = {**old, "trials": [{**trial, "makespan_seconds": 9}]}
    result = live_rows(old, new)
    assert result[0]["delta_percent"] == pytest.approx(-10)
    assert result[0]["paired_ratios"] == [0.9]
    assert size_label(-5) == "small (<=5%)"
    assert size_label(15) == "moderate (5-15%)"
    assert size_label(15.01) == "large (>15%)"
    with pytest.raises(ValueError, match="schedules differ"):
        live_rows(old, {**new, "schedule": []})
    with pytest.raises(ValueError, match="Incomplete repetitions"):
        live_rows(old, {**new, "trials": []})
