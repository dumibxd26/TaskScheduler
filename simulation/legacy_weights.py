"""Learning-aware adapter for tuning the live Legacy placement weights."""

from __future__ import annotations

import random
from dataclasses import dataclass
from hashlib import sha256

from models.cluster import Node
from models.profile_store import ProfileStore
from models.workload import TaskInstance, TaskTemplate
from services.scheduler import LegacyWeights, PlacementAlgorithm
from simulation.engine import NodeView, Placement, SchedulerSnapshot
from simulation.estimation import isolated_execution_s
from simulation.policies import (
    _available,
    _best_unit_placement,
    _commit,
    _planned_tasks,
    _scheduling_units,
)
from simulation.schema import ClusterSpec, TaskSpec, WorkflowSpec


PROFILE_CALIBRATION_SEED = 99173


@dataclass(frozen=True)
class _DeterministicRunningTask:
    expected_runtime: float | None
    elapsed_s: float

    @property
    def estimated_remaining(self) -> float | None:
        if self.expected_runtime is None:
            return None
        return max(0.0, self.expected_runtime - self.elapsed_s)


def _compatible(task: TaskSpec, node) -> bool:
    return (
        node.cpu.architecture in task.supported_architectures
        and node.cpu.logical_cpus >= task.cpu_cores
        and node.memory.capacity_mib - node.background_memory_mib
        >= task.memory_mib
    )


def build_warm_profile(cluster: ClusterSpec, workflow: WorkflowSpec, *,
                       samples_per_node: int = 12,
                       seed: int = 0) -> ProfileStore:
    """Calibrate recurring task/node profiles independently of evaluation runs."""
    if samples_per_node < 3:
        raise ValueError("samples_per_node must be at least 3")
    store = ProfileStore()
    for task in workflow.tasks:
        for node in cluster.nodes:
            if not _compatible(task, node):
                continue
            digest = sha256(
                f"{seed}:{cluster.cluster_id}:{workflow.workflow_id}:"
                f"{task.task_id}:{node.node_id}".encode("utf-8")
            ).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
            base_runtime = isolated_execution_s(task, node)
            for _ in range(samples_per_node):
                store.record_observation(
                    task.task_id,
                    node.node_id,
                    node.node_type,
                    actual_runtime=max(0.0, base_runtime * rng.uniform(0.95, 1.05)),
                    actual_startup=max(
                        0.0, task.startup_s * rng.uniform(0.95, 1.05)),
                )
            failure_probability = min(
                1.0,
                node.failure_rate_per_hour
                * (base_runtime + task.startup_s)
                / 3600.0,
            )
            expected_failures = round(samples_per_node * failure_probability)
            for _ in range(expected_failures):
                store.record_failure(task.task_id, node.node_id)
    return store


class LegacyScoringPolicy:
    """Use the live eight-factor scorer inside the discrete-event simulator."""

    preemptive = False

    def __init__(self, weights: LegacyWeights,
                 profile_store: ProfileStore | None = None,
                 name: str = "legacy-eight-factor"):
        self.name = name
        self.weights = weights
        self.profile_store = profile_store or ProfileStore()
        self.algorithm = PlacementAlgorithm(self.profile_store, weights)
        self._placed_at: dict[str, float] = {}

    @staticmethod
    def _template(snapshot: SchedulerSnapshot, task: TaskSpec) -> TaskTemplate:
        compatible_types = []
        for node in snapshot.cluster.nodes:
            if _compatible(task, node) and node.node_type not in compatible_types:
                compatible_types.append(node.node_type)
        return TaskTemplate(
            task_template_id=task.task_id,
            name=task.task_id,
            task_class=task.task_class,
            cpu_request=task.cpu_cores,
            memory_request=task.memory_mib,
            image_name=task.image,
            compatible_node_types=compatible_types,
            min_cores=max(1, int(task.cpu_cores)),
            max_cores=max(1, int(task.cpu_cores)),
            checkpointable=task.checkpointable,
            checkpoint_interval_s=task.checkpoint_interval_s,
            gang_group_id=task.gang_group_id,
            expected_output_bytes=int(sum(task.output_mib.values()) * 1024 * 1024),
        )

    @staticmethod
    def _parent_nodes(snapshot: SchedulerSnapshot, task_id: str) -> list[str]:
        return [
            snapshot.tasks[dependency.parent].assigned_node_id
            for dependency in snapshot.workflow.dependencies
            if dependency.child == task_id
            and snapshot.tasks[dependency.parent].assigned_node_id is not None
        ]

    def _live_node(self, snapshot: SchedulerSnapshot, task: TaskSpec,
                   node_view: NodeView,
                   co_runners: tuple[TaskSpec, ...]) -> Node:
        existing_count = len(node_view.running_task_ids)
        planned = co_runners[existing_count:]
        total_memory = (
            node_view.node.memory.capacity_mib
            - node_view.node.background_memory_mib
        )
        live_node = Node(
            node_id=node_view.node.node_id,
            node_type=node_view.node.node_type,
            total_cpu=float(node_view.node.cpu.logical_cpus),
            total_memory=total_memory,
            free_cpu=(
                node_view.free_logical_cpus
                - sum(item.cpu_cores for item in planned)
            ),
            free_memory=(
                node_view.free_memory_mib
                - sum(item.memory_mib for item in planned)
            ),
            running_tasks=len(co_runners),
            warm_images=set(node_view.warm_images),
        )
        for runner in co_runners:
            expected = self.profile_store.get_expected_runtime(
                runner.task_id, node_view.node.node_id)
            elapsed = max(
                0.0,
                snapshot.now_s - self._placed_at.get(runner.task_id, snapshot.now_s),
            )
            live_node.active_tasks[runner.task_id] = _DeterministicRunningTask(
                expected_runtime=expected,
                elapsed_s=elapsed,
            )
        return live_node

    def _score(self, snapshot: SchedulerSnapshot, task: TaskSpec,
               node_view: NodeView,
               co_runners: tuple[TaskSpec, ...]) -> float:
        template = self._template(snapshot, task)
        live_task = TaskInstance(
            task_instance_id=task.task_id,
            workflow_instance_id=snapshot.workflow.workflow_id,
            task_template_id=task.task_id,
            parent_node_ids=self._parent_nodes(snapshot, task.task_id),
        )
        node = self._live_node(snapshot, task, node_view, co_runners)
        detail = self.algorithm.score_node(
            live_task, template, node, live_task.parent_node_ids)
        return -detail["total"]

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        placements: list[Placement] = []
        workflow_order = {
            task.task_id: index
            for index, task in enumerate(snapshot.workflow.tasks)
        }
        ordered = sorted(
            snapshot.ready_tasks,
            key=lambda task: (
                -(task.priority or snapshot.workflow.priority).value,
                workflow_order[task.task_id],
            ),
        )
        for unit in _scheduling_units(snapshot, ordered):
            selected = _best_unit_placement(
                snapshot,
                unit,
                free_cpu,
                free_memory,
                planned_tasks,
                lambda task, node, co_runners: self._score(
                    snapshot, task, node, co_runners),
            )
            if not selected:
                continue
            placements.extend(selected)
            _commit(
                selected,
                snapshot.workflow.tasks_by_id,
                free_cpu,
                free_memory,
                planned_tasks,
            )
            for placement in selected:
                self._placed_at[placement.task_id] = snapshot.now_s
        return placements