"""Scheduling policies for controlled simulator comparisons."""

from __future__ import annotations

import random
from dataclasses import dataclass

from models.enums import NodeType, TaskClass
from simulation.engine import NodeView, Placement, SchedulerSnapshot, TaskPhase
from simulation.estimation import (
    average_communication_s,
    average_execution_s,
    estimate_task,
)
from simulation.schema import ClusterSpec, TaskSpec, WorkflowSpec


def _fits(task: TaskSpec, node: NodeView, free_cpu: dict[str, float],
          free_memory: dict[str, float]) -> bool:
    return (
    node.available
    and node.node.cpu.architecture in task.supported_architectures
        and task.cpu_cores <= free_cpu[node.node.node_id] + 1e-9
        and task.memory_mib <= free_memory[node.node.node_id] + 1e-9
    )


def _reserve(task: TaskSpec, node_id: str, free_cpu: dict[str, float],
             free_memory: dict[str, float]) -> None:
    free_cpu[node_id] -= task.cpu_cores
    free_memory[node_id] -= task.memory_mib


def _available(snapshot: SchedulerSnapshot):
    free_cpu = {
        node_id: view.free_logical_cpus for node_id, view in snapshot.nodes.items()
    }
    free_memory = {
        node_id: view.free_memory_mib for node_id, view in snapshot.nodes.items()
    }
    return free_cpu, free_memory


def _planned_tasks(snapshot: SchedulerSnapshot) -> dict[str, list[TaskSpec]]:
    return {
        node_id: [snapshot.tasks[task_id].task for task_id in view.running_task_ids]
        for node_id, view in snapshot.nodes.items()
    }


def _scheduling_units(snapshot: SchedulerSnapshot,
                      ordered_tasks: list[TaskSpec]) -> list[list[TaskSpec]]:
    ready_ids = {task.task_id for task in snapshot.ready_tasks}
    groups: dict[str, list[TaskSpec]] = {}
    for task in snapshot.workflow.tasks:
        if (task.gang_group_id
            and snapshot.tasks[task.task_id].phase != TaskPhase.FINISHED):
            groups.setdefault(task.gang_group_id, []).append(task)
    units = []
    emitted_groups: set[str] = set()
    for task in ordered_tasks:
        if not task.gang_group_id:
            units.append([task])
            continue
        if task.gang_group_id in emitted_groups:
            continue
        emitted_groups.add(task.gang_group_id)
        members = groups[task.gang_group_id]
        if all(member.task_id in ready_ids for member in members):
            units.append(members)
    return units


def _best_unit_placement(
    snapshot: SchedulerSnapshot,
    tasks: list[TaskSpec],
    free_cpu: dict[str, float],
    free_memory: dict[str, float],
    planned_tasks: dict[str, list[TaskSpec]],
    score,
) -> list[Placement] | None:
    best_score = float("inf")
    best: list[Placement] | None = None
    working_cpu = dict(free_cpu)
    working_memory = dict(free_memory)
    working_planned = {
        node_id: list(tasks) for node_id, tasks in planned_tasks.items()
    }

    def search(index: int, total_score: float, placements: list[Placement]) -> None:
        nonlocal best_score, best
        if total_score >= best_score:
            return
        if index == len(tasks):
            best_score = total_score
            best = list(placements)
            return
        task = tasks[index]
        candidates = sorted(
            (
                score(task, node, tuple(working_planned[node_id])),
                node_id,
            )
            for node_id, node in snapshot.nodes.items()
            if _fits(task, node, working_cpu, working_memory)
        )
        for candidate_score, node_id in candidates:
            _reserve(task, node_id, working_cpu, working_memory)
            working_planned[node_id].append(task)
            placements.append(Placement(task.task_id, node_id))
            search(index + 1, total_score + candidate_score, placements)
            placements.pop()
            working_planned[node_id].pop()
            working_cpu[node_id] += task.cpu_cores
            working_memory[node_id] += task.memory_mib

    search(0, 0.0, [])
    return best


def _commit(placements: list[Placement], tasks_by_id: dict[str, TaskSpec],
            free_cpu: dict[str, float], free_memory: dict[str, float],
            planned_tasks: dict[str, list[TaskSpec]]) -> None:
    for placement in placements:
        task = tasks_by_id[placement.task_id]
        _reserve(task, placement.node_id, free_cpu, free_memory)
        planned_tasks[placement.node_id].append(task)


class FCFSPolicy:
    name = "fcfs"

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        placements = []
        node_order = {node_id: index for index, node_id in enumerate(snapshot.nodes)}
        for unit in _scheduling_units(snapshot, list(snapshot.ready_tasks)):
            selected = _best_unit_placement(
                snapshot, unit, free_cpu, free_memory,
                planned_tasks,
                lambda task, node, co_runners: node_order[node.node.node_id],
            )
            if selected:
                placements.extend(selected)
                _commit(selected, snapshot.workflow.tasks_by_id,
                    free_cpu, free_memory, planned_tasks)
        return placements


class RandomPolicy:
    name = "random"

    def __init__(self, seed: int = 0):
        self._random = random.Random(seed)

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        tasks = list(snapshot.ready_tasks)
        self._random.shuffle(tasks)
        placements = []
        random_scores = {
            (task.task_id, node_id): self._random.random()
            for task in snapshot.ready_tasks for node_id in snapshot.nodes
        }
        for unit in _scheduling_units(snapshot, tasks):
            selected = _best_unit_placement(
                snapshot, unit, free_cpu, free_memory,
                planned_tasks,
                lambda task, node, co_runners: random_scores[
                    (task.task_id, node.node.node_id)],
            )
            if selected:
                placements.extend(selected)
                _commit(selected, snapshot.workflow.tasks_by_id,
                    free_cpu, free_memory, planned_tasks)
        return placements


class EFTPolicy:
    name = "eft"

    def task_order(self, snapshot: SchedulerSnapshot) -> list[TaskSpec]:
        return list(snapshot.ready_tasks)

    def score(self, snapshot: SchedulerSnapshot, task: TaskSpec,
              node: NodeView,
              co_runners: tuple[TaskSpec, ...] = ()) -> float:
          return estimate_task(snapshot, task, node, co_runners).finish_s

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        placements = []
        for unit in _scheduling_units(snapshot, self.task_order(snapshot)):
            selected = _best_unit_placement(
                snapshot, unit, free_cpu, free_memory,
                planned_tasks,
                lambda task, node, co_runners: self.score(
                    snapshot, task, node, co_runners),
            )
            if selected:
                placements.extend(selected)
                _commit(selected, snapshot.workflow.tasks_by_id,
                    free_cpu, free_memory, planned_tasks)
        return placements


class HEFTPolicy(EFTPolicy):
    name = "heft"

    def _upward_ranks(self, snapshot: SchedulerSnapshot) -> dict[str, float]:
        children: dict[str, list[tuple[str, float]]] = {
            task.task_id: [] for task in snapshot.workflow.tasks
        }
        for dependency in snapshot.workflow.dependencies:
            children[dependency.parent].append((dependency.child, dependency.data_mib))
        tasks = snapshot.workflow.tasks_by_id
        ranks: dict[str, float] = {}

        def rank(task_id: str) -> float:
            if task_id in ranks:
                return ranks[task_id]
            successor_cost = max((
                average_communication_s(snapshot, data_mib) + rank(child_id)
                for child_id, data_mib in children[task_id]
            ), default=0.0)
            ranks[task_id] = average_execution_s(snapshot, tasks[task_id]) + successor_cost
            return ranks[task_id]

        for task_id in tasks:
            rank(task_id)
        return ranks

    def task_order(self, snapshot: SchedulerSnapshot) -> list[TaskSpec]:
        ranks = self._upward_ranks(snapshot)
        workflow_order = {
            task.task_id: index for index, task in enumerate(snapshot.workflow.tasks)
        }
        return sorted(
            snapshot.ready_tasks,
            key=lambda task: (-ranks[task.task_id], workflow_order[task.task_id]),
        )


class LegacyWeightedPolicy(EFTPolicy):
    """Deterministic translation of the original heterogeneous weighted scorer."""

    name = "legacy"
    preferred_node_type = {
        TaskClass.CPU_BOUND: NodeType.CPU_OPT,
        TaskClass.MEMORY_BOUND: NodeType.MEM_OPT,
        TaskClass.IO_BOUND: NodeType.IO_OPT,
    }

    def score(self, snapshot: SchedulerSnapshot, task: TaskSpec,
              node: NodeView,
              co_runners: tuple[TaskSpec, ...] = ()) -> float:
        estimate = estimate_task(snapshot, task, node, co_runners)
        specialization_penalty = (
            0.0 if node.node.node_type == self.preferred_node_type[task.task_class]
            else 0.15 * estimate.execution_s
        )
        cpu_slack = node.free_logical_cpus - task.cpu_cores
        memory_slack = node.free_memory_mib - task.memory_mib
        fragmentation_penalty = 0.001 / max(cpu_slack + 1.0, 1e-9)
        fragmentation_penalty += 0.001 / max(memory_slack + 1.0, 1e-9)
        return estimate.finish_s + specialization_penalty + fragmentation_penalty


@dataclass(frozen=True)
class AdaptiveWeights:
    energy_s_per_j: float = 0.002
    transfer_s_per_mib: float = 0.001
    thermal_penalty_s: float = 10.0
    failure_penalty_s: float = 100.0


class AdaptivePolicy(HEFTPolicy):
    name = "adaptive"
    preemptive = True

    def __init__(self, weights: AdaptiveWeights | None = None):
        self.weights = weights or AdaptiveWeights()

    def task_order(self, snapshot: SchedulerSnapshot) -> list[TaskSpec]:
        heft_order = super().task_order(snapshot)
        return sorted(
            heft_order,
            key=lambda task: -(
                task.priority or snapshot.workflow.priority).value,
        )

    def score(self, snapshot: SchedulerSnapshot, task: TaskSpec,
              node: NodeView,
              co_runners: tuple[TaskSpec, ...] = ()) -> float:
        estimate = estimate_task(snapshot, task, node, co_runners)
        thermal = node.node.thermal
        headroom = thermal.throttle_c - node.temperature_c
        thermal_pressure = max(0.0, 1.0 - headroom / 20.0)
        failure_probability = min(
            1.0,
            node.node.failure_rate_per_hour * estimate.execution_s / 3600.0,
        )
        return (
            estimate.finish_s
            + estimate.active_energy_j * self.weights.energy_s_per_j
            + estimate.transfer_mib * self.weights.transfer_s_per_mib
            + thermal_pressure * self.weights.thermal_penalty_s
            + failure_probability * self.weights.failure_penalty_s
        )


class AdaptiveNoPreemptionPolicy(AdaptivePolicy):
    name = "adaptive-no-preempt"
    preemptive = False


class StaticAssignmentPolicy:
    name = "static-assignment"

    def __init__(self, assignments: dict[str, str]):
        self.assignments = dict(assignments)

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        placements = []
        for unit in _scheduling_units(snapshot, list(snapshot.ready_tasks)):
            selected = [
                Placement(task.task_id, self.assignments[task.task_id])
                for task in unit
            ]
            trial_cpu = dict(free_cpu)
            trial_memory = dict(free_memory)
            feasible = True
            for task, placement in zip(unit, selected):
                node = snapshot.nodes[placement.node_id]
                if not _fits(task, node, trial_cpu, trial_memory):
                    feasible = False
                    break
                _reserve(task, placement.node_id, trial_cpu, trial_memory)
            if feasible:
                placements.extend(selected)
                _commit(selected, snapshot.workflow.tasks_by_id,
                    free_cpu, free_memory, planned_tasks)
        return placements


def create_policy(name: str, *, seed: int = 0,
                  cluster: ClusterSpec | None = None,
                  workflow: WorkflowSpec | None = None):
    normalized = name.lower()
    if normalized in {"legacy-v1", "legacy-tuned"}:
        if cluster is None or workflow is None:
            raise ValueError(
                f"simulation policy '{name}' requires cluster= and workflow=")
        from services.scheduler import legacy_weights_for_policy
        from simulation.legacy_weights import (
            PROFILE_CALIBRATION_SEED,
            LegacyScoringPolicy,
            build_warm_profile,
        )
        profile = build_warm_profile(
            cluster, workflow, seed=PROFILE_CALIBRATION_SEED)
        return LegacyScoringPolicy(
            legacy_weights_for_policy(normalized),
            profile,
            name=normalized,
        )

    policies = {
        "fcfs": FCFSPolicy,
        "random": lambda: RandomPolicy(seed),
        "eft": EFTPolicy,
        "heft": HEFTPolicy,
        "legacy": LegacyWeightedPolicy,
        "adaptive": AdaptivePolicy,
        "adaptive-no-preempt": AdaptiveNoPreemptionPolicy,
    }
    if normalized not in policies:
        raise ValueError(
            f"unknown simulation policy '{name}'; expected one of {sorted(policies)}"
        )
    return policies[normalized]()