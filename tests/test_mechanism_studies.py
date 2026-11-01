from pathlib import Path

import pytest

from run_mechanism_studies import (
    ImageBlindHEFTRA,
    image_blind_snapshot,
    prewarmed_cluster,
    priority_records,
    summarize_cold_start,
    summarize_priority,
    workflow_images,
    cold_start_records,
)
from simulation.engine import Simulator
from simulation.policies import ResourceAwareHEFTPolicy, create_policy
from simulation.schema import load_cluster, load_workflow
from simulation.workloads import load_workload


ROOT = Path(__file__).resolve().parents[1]


def _scenario(kind: str, name: str) -> Path:
    return ROOT / "scenarios" / kind / f"{name}.yaml"


class _Capture(ResourceAwareHEFTPolicy):
    def __init__(self):
        self.snapshots = []

    def schedule(self, snapshot):
        self.snapshots.append(snapshot)
        return super().schedule(snapshot)


def test_image_blind_snapshot_marks_every_image_warm_without_mutation():
    cluster = load_cluster(_scenario("clusters", "balanced-heterogeneous-4"))
    workflow = load_workflow(_scenario("workflows", "fanout-1to8"))
    capture = _Capture()
    Simulator(cluster, workflow, capture, seed=1).run()
    original = capture.snapshots[0]

    blind = image_blind_snapshot(original)

    images = workflow_images(workflow)
    assert all(images <= view.warm_images for view in blind.nodes.values())
    assert any(not images <= view.warm_images
               for view in original.nodes.values())


def test_prewarmed_cluster_removes_cold_starts():
    cluster = load_cluster(_scenario("clusters", "asymmetric-edge-5"))
    workflow = load_workflow(_scenario("workflows", "diamond"))
    warm = prewarmed_cluster(cluster, workflow)

    cold_result = Simulator(cluster, workflow, create_policy("heft-ra"),
                            seed=2).run()
    warm_result = Simulator(warm, workflow, create_policy("heft-ra"),
                            seed=2).run()

    assert cold_result.cold_starts > 0
    assert warm_result.cold_starts == 0


def test_image_blind_policy_completes_and_cold_start_summary_pairs_runs():
    cluster = load_cluster(_scenario("clusters", "asymmetric-edge-5"))
    workflow = load_workflow(_scenario("workflows", "fanout-1to8"))
    assert Simulator(cluster, workflow, ImageBlindHEFTRA(),
                     seed=0).run().makespan_s > 0

    records = cold_start_records([cluster], [workflow], [400], 0.1)
    summary = summarize_cold_start(records)

    assert set(summary["variants"]) == {
        "heft-ra", "heft-ra-image-blind", "heft-ra-prewarmed", "k8s-default"}
    assert all(row["cases"] == 1 for row in summary["contrasts"])


def test_priority_records_split_urgent_and_other_responses():
    cluster = load_cluster(_scenario("clusters", "homogeneous-4"))
    workload = load_workload(_scenario("workloads", "priority-burst"))
    other = load_workload(_scenario("workloads", "data-locality-wave"))

    records = priority_records([cluster], [workload, other], [400], 0.1,
                               policies=("adaptive", "adaptive-no-preempt"))

    assert {row["workload_id"] for row in records} == {"priority-burst"}
    assert len(records) == 2
    for row in records:
        assert row["urgent_response_s"] > 0
        assert row["other_response_s"] > 0
        assert row["urgent_deadline_met"] in (True, False)
    no_preempt = next(row for row in records
                      if row["policy"] == "adaptive-no-preempt")
    assert no_preempt["preemptions"] == 0

    summary = summarize_priority(records)
    contrast = next(row for row in summary["contrasts"]
                    if row["left"] == "adaptive"
                    and row["right"] == "adaptive-no-preempt"
                    and row["metric"] == "urgent_response_s")
    assert contrast["cases"] == 1
    assert contrast["mean_ratio"] == pytest.approx(
        records[0]["urgent_response_s"] / records[1]["urgent_response_s"])
