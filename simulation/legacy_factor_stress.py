"""Controlled decision stresses for Legacy factors rarely active in corpora."""

from __future__ import annotations

from dataclasses import asdict, replace

from models.cluster import Node
from models.enums import NodeType, TaskClass
from models.profile import TaskProfile
from models.profile_store import ProfileStore
from models.workload import TaskInstance, TaskTemplate
from services.scheduler import LEGACY_TUNED_WEIGHTS, PlacementAlgorithm


def _task(compatible_types: list[NodeType], *, memory: float = 128) -> tuple[
        TaskInstance, TaskTemplate]:
    return (
        TaskInstance("task", "workflow", "template"),
        TaskTemplate(
            "template",
            "task",
            TaskClass.CPU_BOUND,
            1,
            memory,
            "task:v1",
            compatible_types,
        ),
    )


def _evaluate(store: ProfileStore, task: TaskInstance, template: TaskTemplate,
              nodes: list[Node], omitted: str) -> dict:
    full = PlacementAlgorithm(store, LEGACY_TUNED_WEIGHTS)
    ablated_weights = replace(LEGACY_TUNED_WEIGHTS, **{omitted: 0.0})
    ablated = PlacementAlgorithm(store, ablated_weights)
    return {
        "factor": omitted,
        "full_choice": full.compute_placement(task, template, nodes).node_id,
        "ablated_choice": ablated.compute_placement(task, template, nodes).node_id,
        "full_scores": {
            node.node_id: full.score_node(task, template, node)
            for node in nodes
        },
        "ablated_scores": {
            node.node_id: ablated.score_node(task, template, node)
            for node in nodes
        },
    }


def run_factor_stresses() -> dict:
    task, template = _task([NodeType.GENERAL])
    cold = Node("cold", NodeType.GENERAL, 4, 4096, 4, 4096)
    warm = Node("warm", NodeType.GENERAL, 4, 4096, 4, 4096)
    warm.warm_images.add("task:v1")
    warm_image = _evaluate(
        ProfileStore(), task, template, [cold, warm], "warm_image")

    failure_store = ProfileStore()
    risky = Node("risky", NodeType.GENERAL, 4, 4096, 4, 4096)
    healthy = Node("healthy", NodeType.GENERAL, 4, 4096, 4, 4096)
    for node in (risky, healthy):
        for _ in range(3):
            failure_store.record_observation(
                "template", node.node_id, NodeType.GENERAL, 10, 1)
    for _ in range(5):
        failure_store.record_failure("template", "risky")
    failure = _evaluate(
        failure_store, task, template, [risky, healthy], "failure_penalty")

    pressure_store = ProfileStore()
    pressure_store.profiles["template"] = TaskProfile(
        task_template_id="template",
        preferred_node_order=[
            NodeType.CPU_OPT,
            NodeType.MEM_OPT,
            NodeType.IO_OPT,
            NodeType.GENERAL,
        ],
    )
    pressure_task, pressure_template = _task(
        [NodeType.CPU_OPT, NodeType.MEM_OPT], memory=64)
    pressured = Node("pressured", NodeType.CPU_OPT, 4, 4096, 4, 64)
    safe = Node("safe", NodeType.MEM_OPT, 4, 400, 4, 64)
    memory_pressure = _evaluate(
        pressure_store,
        pressure_task,
        pressure_template,
        [pressured, safe],
        "memory_pressure",
    )

    stresses = {
        "warm_image": warm_image,
        "failure_penalty": failure,
        "memory_pressure": memory_pressure,
    }
    return {
        "schema_version": 1,
        "method": (
            "Controlled one-factor intervention. Each ablated vector equals "
            "the tuned vector except that the named factor is set to zero."
        ),
        "tuned_weights": asdict(LEGACY_TUNED_WEIGHTS),
        "stresses": stresses,
        "all_factors_change_choice": all(
            row["full_choice"] != row["ablated_choice"]
            for row in stresses.values()
        ),
    }