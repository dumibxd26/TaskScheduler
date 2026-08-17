from pathlib import Path

import pytest

from analyze_kind_topologies import _comparison, _policy_metrics, _validate_trial


def test_comparison_reports_left_over_right_ratios():
    left = {
        "median_workflow_response_s": 8,
        "p95_workflow_response_s": 10,
        "median_critical_response_s": 6,
        "median_trial_makespan_s": 12,
    }
    right = {
        "median_workflow_response_s": 10,
        "p95_workflow_response_s": 10,
        "median_critical_response_s": 8,
        "median_trial_makespan_s": 15,
    }

    assert _comparison(left, right) == {
        "median_workflow_response_s": 0.8,
        "p95_workflow_response_s": 1.0,
        "median_critical_response_s": 0.75,
        "median_trial_makespan_s": 0.8,
    }


def test_policy_metrics_aggregates_live_trials():
    trial = {
        "repetition": 1,
        "makespan_seconds": 12.0,
        "mean_response_seconds": 8.0,
        "preemptions": 1,
        "scheduler_events": {"error_lines": []},
        "workflows": [{
            "shape": "critical",
            "state": "FINISHED",
            "response_seconds": 8.0,
            "tasks_failed": 0,
            "tasks": [{
                "transfer_mode": "remote",
                "state": "FINISHED",
                "pod_phase": "Succeeded",
                "restart_count": 0,
                "metrics": {"transfer_seconds": 0.5},
            }],
        }],
    }

    result = _policy_metrics([trial])

    assert result["completion_rate"] == 1
    assert result["median_critical_response_s"] == 8
    assert result["median_response_by_shape_s"] == {"critical": 8}
    assert result["median_trial_makespan_s"] == 12
    assert result["by_repetition"] == {
        "1": {
            "trials": 1,
            "median_trial_makespan_s": 12,
            "median_trial_mean_response_s": 8,
        }
    }
    assert result["preemptions"] == 1
    assert result["remote_fetches"] == 1
    assert result["transfer_seconds"] == 0.5
    assert result["container_restarts"] == 0


def test_trial_validation_rejects_failed_final_task():
    tasks = [{
        "state": "FINISHED",
        "pod_phase": "Succeeded",
        "restart_count": 0,
    } for _ in range(20)]
    tasks[0]["state"] = "FAILED"
    shapes = ["pressure", "critical", "pipeline", "fanout", "parallel"]
    trial = {
        "policy": "adaptive",
        "repetition": 1,
        "scheduler_events": {
            "binding_lines": ["bind"] * 20,
            "error_lines": [],
        },
        "workflows": [
            {
                "shape": shape,
                "state": "FINISHED",
                "tasks_failed": 0,
                "tasks": tasks[index * 4:(index + 1) * 4],
            }
            for index, shape in enumerate(shapes)
        ],
    }

    with pytest.raises(ValueError, match="non-successful final task"):
        _validate_trial(Path("result.json"), "adaptive", 1, trial)