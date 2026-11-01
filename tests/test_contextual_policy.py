from dataclasses import replace
from pathlib import Path

from simulation.benchmark_suite import load_benchmark_suite
from simulation.contextual_policy import (
    ContextualPolicyProfile,
    ContextualPortfolioPolicy,
    PolicyChoice,
    scheduling_context,
)
from simulation.engine import Simulator
from simulation.policies import AdaptiveWeights, HEFTPolicy
from simulation.schema import load_cluster


ROOT = Path(__file__).parents[1]


def _case(resource, topology):
    return next(
        case for case in load_benchmark_suite(
            ROOT / "scenarios" / "benchmark-suite.yaml")
        if case.resource_pattern == resource and case.topology == topology
    )


def test_context_combines_resource_mix_and_dag_shape():
    assert scheduling_context(_case("cpu", "independent").workflow).key == (
        "cpu:independent")
    assert scheduling_context(_case("memory", "linear").workflow).key == (
        "memory:linear")
    assert scheduling_context(_case("mixed", "forkjoin").workflow).key == (
        "mixed:forkjoin")


def test_contextual_profile_round_trips_adaptive_hyperparameters():
    profile = ContextualPolicyProfile(
        name="test-profile",
        default=PolicyChoice("heft"),
        contexts={
            "cpu:fanout": PolicyChoice(
                "adaptive",
                AdaptiveWeights(energy_s_per_j=0.0, transfer_s_per_mib=0.01),
            ),
        },
        training_suite="generic-v1",
    )

    restored = ContextualPolicyProfile.from_mapping(profile.to_mapping())

    assert restored == profile


def test_contextual_policy_uses_configured_specialist_and_completes():
    cluster = load_cluster(
        ROOT / "scenarios" / "clusters" / "balanced-heterogeneous-4.yaml")
    generated = _case("cpu", "linear").workflow
    workflow = replace(
        generated,
        tasks=tuple(replace(
            task,
            compute_gflop=task.compute_gflop * 0.0001,
            memory_read_mib=task.memory_read_mib * 0.0001,
            memory_write_mib=task.memory_write_mib * 0.0001,
            disk_read_mib=task.disk_read_mib * 0.0001,
            disk_write_mib=task.disk_write_mib * 0.0001,
            startup_s=0.01,
        ) for task in generated.tasks),
        dependencies=tuple(replace(dependency, data_mib=0.01)
                           for dependency in generated.dependencies),
    )
    profile = ContextualPolicyProfile(
        name="test-profile",
        default=PolicyChoice("eft"),
        contexts={"cpu:linear": PolicyChoice("heft")},
    )
    policy = ContextualPortfolioPolicy(profile, cluster, workflow, seed=7)

    result = Simulator(cluster, workflow, policy, seed=7).run()

    assert isinstance(policy.delegate, HEFTPolicy)
    assert result.makespan_s > 0
    assert len(result.tasks) == len(workflow.tasks)