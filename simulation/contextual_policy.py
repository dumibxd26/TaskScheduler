"""Validated contextual policy selection over explainable scheduler specialists."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from simulation.characterization import characterize_workflow
from simulation.policies import AdaptivePolicy, AdaptiveWeights, create_policy
from simulation.schema import ClusterSpec, ModelValidationError, WorkflowSpec


@dataclass(frozen=True)
class SchedulingContext:
    resource: str
    topology: str

    @property
    def key(self) -> str:
        return f"{self.resource}:{self.topology}"


def scheduling_context(workflow: WorkflowSpec) -> SchedulingContext:
    features = characterize_workflow(workflow)
    fractions = dict(features.task_class_fractions)
    dominant_name, dominant_fraction = max(
        fractions.items(), key=lambda item: (item[1], item[0]))
    resources = {
        "CPU_BOUND": "cpu",
        "MEMORY_BOUND": "memory",
        "IO_BOUND": "io",
    }
    resource = resources[dominant_name] if dominant_fraction >= 0.75 else "mixed"
    topology = features.topology
    if topology == "dag" and features.max_fan_in > 1 and features.max_fan_out > 1:
        topology = "forkjoin"
    return SchedulingContext(resource=resource, topology=topology)


@dataclass(frozen=True)
class PolicyChoice:
    algorithm: str
    adaptive_weights: AdaptiveWeights | None = None

    def __post_init__(self):
        supported = {
            "eft", "heft", "heft-ra", "legacy", "adaptive", "adaptive-no-preempt"}
        if self.algorithm not in supported:
            raise ModelValidationError(
                f"unsupported contextual policy algorithm '{self.algorithm}'")
        if self.adaptive_weights is not None and not self.algorithm.startswith("adaptive"):
            raise ModelValidationError(
                "adaptive_weights are only valid for an adaptive algorithm")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PolicyChoice":
        raw_weights = data.get("adaptive_weights")
        return cls(
            algorithm=str(data["algorithm"]),
            adaptive_weights=(AdaptiveWeights(**{
                key: float(value) for key, value in raw_weights.items()
            }) if raw_weights is not None else None),
        )

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"algorithm": self.algorithm}
        if self.adaptive_weights is not None:
            result["adaptive_weights"] = asdict(self.adaptive_weights)
        return result


@dataclass(frozen=True)
class ContextualPolicyProfile:
    name: str
    default: PolicyChoice
    contexts: Mapping[str, PolicyChoice]
    training_suite: str = "unspecified"

    def __post_init__(self):
        if not self.name:
            raise ModelValidationError("contextual policy profile name is required")
        for key in self.contexts:
            parts = key.split(":")
            if len(parts) != 2 or not all(parts):
                raise ModelValidationError(f"invalid scheduling context key '{key}'")

    def choice_for(self, workflow: WorkflowSpec) -> PolicyChoice:
        return self.contexts.get(scheduling_context(workflow).key, self.default)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ContextualPolicyProfile":
        if int(data.get("schema_version", 1)) != 1:
            raise ModelValidationError("unsupported contextual profile schema_version")
        profile = data.get("profile", data)
        return cls(
            name=str(profile["name"]),
            default=PolicyChoice.from_mapping(profile["default"]),
            contexts={
                str(key): PolicyChoice.from_mapping(value)
                for key, value in profile.get("contexts", {}).items()
            },
            training_suite=str(profile.get("training_suite", "unspecified")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "profile": {
                "name": self.name,
                "training_suite": self.training_suite,
                "default": self.default.to_mapping(),
                "contexts": {
                    key: choice.to_mapping()
                    for key, choice in sorted(self.contexts.items())
                },
            },
        }


class ContextualPortfolioPolicy:
    """Choose one validated scheduling specialist before a workflow starts."""

    def __init__(self, profile: ContextualPolicyProfile,
                 cluster: ClusterSpec, workflow: WorkflowSpec, *, seed: int = 0):
        self.profile = profile
        self.context = scheduling_context(workflow)
        self.choice = profile.choice_for(workflow)
        if self.choice.algorithm.startswith("adaptive"):
            self.delegate = AdaptivePolicy(self.choice.adaptive_weights)
            if self.choice.algorithm == "adaptive-no-preempt":
                self.delegate.preemptive = False
        else:
            self.delegate = create_policy(
                self.choice.algorithm,
                seed=seed,
                cluster=cluster,
                workflow=workflow,
            )
        self.name = profile.name
        self.preemptive = getattr(self.delegate, "preemptive", False)

    def schedule(self, snapshot):
        return self.delegate.schedule(snapshot)