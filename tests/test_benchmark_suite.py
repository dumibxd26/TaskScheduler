from pathlib import Path

from simulation.benchmark_suite import load_benchmark_suite
from simulation.characterization import characterize_workflow
from simulation.schema import load_cluster, validate_task_compatibility


ROOT = Path(__file__).parents[1]


def test_factorial_suite_has_disjoint_fixed_splits_and_full_coverage():
    cases = load_benchmark_suite(ROOT / "scenarios" / "benchmark-suite.yaml")
    by_split = {
        split: [case for case in cases if case.split == split]
        for split in ("train", "validation", "holdout")
    }

    assert {split: len(rows) for split, rows in by_split.items()} == {
        "train": 80,
        "validation": 40,
        "holdout": 40,
    }
    assert len({case.case_id for case in cases}) == 160
    assert {case.resource_pattern for case in cases} == {
        "cpu", "memory", "io", "mixed",
    }
    assert {case.topology for case in cases} == {
        "independent", "linear", "fanout", "fanin", "forkjoin",
    }
    assert not (
        {case.task_count for case in by_split["train"]}
        & {case.task_count for case in by_split["holdout"]}
    )


def test_generated_topologies_match_their_characterization():
    cases = load_benchmark_suite(ROOT / "scenarios" / "benchmark-suite.yaml")
    examples = {}
    for case in cases:
        examples.setdefault(case.topology, case.workflow)

    assert characterize_workflow(examples["independent"]).topology == "independent"
    assert characterize_workflow(examples["linear"]).topology == "linear"
    assert characterize_workflow(examples["fanout"]).topology == "fanout"
    assert characterize_workflow(examples["fanin"]).topology == "fanin"
    forkjoin = characterize_workflow(examples["forkjoin"])
    assert forkjoin.topology == "dag"
    assert forkjoin.max_fan_in > 1
    assert forkjoin.max_fan_out > 1


def test_generated_workflows_fit_every_research_cluster():
    cases = load_benchmark_suite(ROOT / "scenarios" / "benchmark-suite.yaml")
    clusters = [
        load_cluster(path)
        for path in sorted((ROOT / "scenarios" / "clusters").glob("*.yaml"))
    ]

    for cluster in clusters:
        for case in cases:
            validate_task_compatibility(cluster, case.workflow)

    assert all(
        task.supported_architectures == frozenset({"amd64", "arm64"})
        for case in cases for task in case.workflow.tasks
    )