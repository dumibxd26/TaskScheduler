"""Scheduling policies for controlled simulator comparisons."""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import inf

from models.enums import NodeType, PriorityClass, TaskClass
from simulation.engine import NodeView, Placement, SchedulerSnapshot, TaskPhase
from simulation.estimation import (
    average_communication_s,
    average_execution_s,
    contended_execution_s,
    contended_transfer_s,
    estimate_task,
    planned_input_flows,
    resource_aware_estimate,
)
from simulation.resources import EPSILON
from simulation.schema import ClusterSpec, TaskSpec, WorkflowSpec


def _fits(task: TaskSpec, node: NodeView, free_cpu: dict[str, float],
          free_memory: dict[str, float]) -> bool:
    return (
        node.available
        and node.node.cpu.architecture in task.supported_architectures
        and task.cpu_cores <= free_cpu[node.node.node_id] + 1e-9
        and task.memory_mib <= free_memory[node.node.node_id] + 1e-9
    )


def task_priority(task: TaskSpec, workflow: WorkflowSpec) -> PriorityClass:
    """Task priority with workflow fallback; BEST_EFFORT (0) is a real value."""
    return task.priority if task.priority is not None else workflow.priority


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
    """Exhaustive branch-and-bound over a scheduling unit (task or gang).

    Pruning on ``partial >= best`` is only sound when every score is
    non-negative, so the contract is enforced instead of silently returning a
    greedy answer for negative scores.
    """
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
        if candidates and candidates[0][0] < 0:
            raise ValueError(
                "placement scores must be non-negative for branch-and-bound; "
                f"got {candidates[0][0]} for task '{task.task_id}'")
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


class ResourceAwareHEFTPolicy(HEFTPolicy):
    """HEFT with resource-aware finish-time estimates (HEFT-RA).

    Task order is HEFT's upward rank. Placement minimizes an EFT estimate that
    corrects the three assumptions behind classic HEFT's failures in the
    scenario corpus:

    * communication is contention-free: input transfers instead receive a
      fair share of every channel they cross, counting flows already in
      flight and flows planned earlier in the same scheduling round;
    * processor speed is constant: CPU time integrates the RC thermal model
      over the predicted duration, so a hot node's throttling is anticipated;
    * nodes never fail: the expected loss of a Poisson failure at the node's
      declared rate is added.

    No priority classes, preemption, energy terms, or tuned weights are added,
    so differences from HEFT are attributable to the estimator alone.
    """

    name = "heft-ra"

    def score(self, snapshot: SchedulerSnapshot, task: TaskSpec,
              node: NodeView,
              co_runners: tuple[TaskSpec, ...] = ()) -> float:
        own = resource_aware_estimate(
            snapshot, task, node, co_runners, self._planned_flows,
            self._planned_pulls).finish_s
        return max(own, self._lookahead_finish(snapshot, task, node, own))

    def _lookahead_finish(self, snapshot: SchedulerSnapshot, task: TaskSpec,
                          node: NodeView, own_finish_s: float) -> float:
        """Latest child finish if each child then picks its best node.

        One-level lookahead in the spirit of Bittencourt et al. (2010): a
        producer is scored by when its consumers could finish, so data-heavy
        fan-outs are not stranded behind a link that every child must cross.
        Siblings are assumed to pull concurrently from the producer.
        """
        children = self._children.get(task.task_id, ())
        if not children:
            return own_finish_s
        source = node.node.node_id
        siblings = sum(1 for _, data_mib in children if data_mib > EPSILON)
        latest = own_finish_s
        for child_id, data_mib in children:
            child = snapshot.workflow.tasks_by_id[child_id]
            best = inf
            for view in snapshot.nodes.values():
                if not view.available or not self._can_host(child, view):
                    continue
                transfer = 0.0
                if view.node.node_id != source and data_mib > EPSILON:
                    transfer = contended_transfer_s(
                        snapshot.cluster, source, view.node.node_id, data_mib,
                        [(source, view.node.node_id)] * max(0, siblings - 1))
                best = min(best, transfer + self._child_execution_s(child, view))
            if best < inf:
                latest = max(latest, own_finish_s + best)
        return latest

    @staticmethod
    def _can_host(task: TaskSpec, view: NodeView) -> bool:
        node = view.node
        return (
            node.cpu.architecture in task.supported_architectures
            and node.cpu.logical_cpus >= task.cpu_cores
            and node.memory.capacity_mib - node.background_memory_mib
            >= task.memory_mib
        )

    def _child_execution_s(self, child: TaskSpec, view: NodeView) -> float:
        key = (child.task_id, view.node.node_id)
        cached = self._child_cache.get(key)
        if cached is None:
            cached = contended_execution_s(child, view.node, view.temperature_c, ())
            self._child_cache[key] = cached
        return cached

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        # Mutated by _commit, so it always reflects this round's commitments.
        self._free_cpu = free_cpu
        planned_tasks = _planned_tasks(snapshot)
        self._planned_flows = [
            (transfer.source, transfer.destination)
            for transfer in snapshot.active_transfers
        ]
        self._planned_pulls = {
            (transfer.destination, transfer.image)
            for transfer in snapshot.active_transfers if transfer.image
        }
        self._children: dict[str, list[tuple[str, float]]] = {}
        for dependency in snapshot.workflow.dependencies:
            self._children.setdefault(dependency.parent, []).append(
                (dependency.child, dependency.data_mib))
        self._child_cache: dict[tuple[str, str], float] = {}
        placements = []
        for unit in _scheduling_units(snapshot, self.task_order(snapshot)):
            selected = _best_unit_placement(
                snapshot, unit, free_cpu, free_memory,
                planned_tasks,
                lambda task, node, co_runners: self.score(
                    snapshot, task, node, co_runners),
            )
            if not selected:
                continue
            placements.extend(selected)
            _commit(selected, snapshot.workflow.tasks_by_id,
                    free_cpu, free_memory, planned_tasks)
            for placement in selected:
                placed = snapshot.workflow.tasks_by_id[placement.task_id]
                view = snapshot.nodes[placement.node_id]
                self._planned_flows.extend(planned_input_flows(
                    snapshot, placed, view, self._planned_pulls))
                if placed.image not in view.warm_images:
                    self._planned_pulls.add((placement.node_id, placed.image))
        return placements


class CapacityAwareHEFTRAPolicy(ResourceAwareHEFTPolicy):
    """HEFT-RA whose lookahead respects the capacity the children will find.

    The first HEFT-RA lookahead lets every child use its best node, which is
    harmless on wide nodes but wrong when a node runs only one or two tasks
    at a time: a producer is then co-located with children that cannot all
    run there. Here the children are assigned the way the (non-delay) list
    scheduler will place them: each goes to the best node that still has a
    free slot in the CPU capacity left after this round's commitments; it
    waits for the earliest sibling to finish only when every node is full,
    and only when no node has capacity at all does the estimate fall back to
    the optimistic bound.
    """

    name = "heft-ra-cap"

    def _lookahead_finish(self, snapshot: SchedulerSnapshot, task: TaskSpec,
                          node: NodeView, own_finish_s: float) -> float:
        children = self._children.get(task.task_id, ())
        if not children:
            return own_finish_s
        source = node.node.node_id
        siblings = sum(1 for _, data_mib in children if data_mib > EPSILON)
        assigned: dict[str, list[float]] = {}
        latest = own_finish_s
        for child_id, data_mib in sorted(children):
            child = snapshot.workflow.tasks_by_id[child_id]
            free, waiting, optimistic = [], [], inf
            for view in snapshot.nodes.values():
                if not view.available or not self._can_host(child, view):
                    continue
                node_id = view.node.node_id
                arrival = own_finish_s
                if node_id != source and data_mib > EPSILON:
                    arrival += contended_transfer_s(
                        snapshot.cluster, source, node_id, data_mib,
                        [(source, node_id)] * max(0, siblings - 1))
                duration = self._child_execution_s(child, view)
                optimistic = min(optimistic, arrival + duration)
                slots = _slots(self._free_cpu.get(node_id, 0.0),
                               child.cpu_cores, len(children))
                if slots <= 0:
                    continue
                taken = assigned.get(node_id, [])
                if len(taken) < slots:
                    free.append((arrival + duration, node_id))
                else:
                    released = sorted(taken)[len(taken) - slots]
                    waiting.append((max(arrival, released) + duration, node_id))
            options = free or waiting
            if options:
                finish, chosen = min(options)
                assigned.setdefault(chosen, []).append(finish)
            else:
                finish = optimistic
            if finish < inf:
                latest = max(latest, finish)
        return latest


def _slots(free_cores: float, request: float, unlimited: int) -> int:
    """How many tasks of ``request`` cores fit in ``free_cores``."""
    if request <= EPSILON:
        return unlimited
    return int((free_cores + EPSILON) // request)


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
            key=lambda task: -task_priority(task, snapshot.workflow).value,
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


# kube-scheduler ImageLocality thresholds (per container), in MiB.
IMAGE_LOCALITY_MIN_MIB = 23.0
IMAGE_LOCALITY_MAX_MIB = 1000.0
KUBE_SCORE_CEILING = 300.0


def kube_default_node_score(task: TaskSpec, node: NodeView,
                            free_cpu: float, free_memory: float,
                            image_spread: float) -> float:
    """Sum of kube-scheduler's default scoring plugins for one node (0-300).

    Reproduces NodeResourcesFit (LeastAllocated), NodeResourcesBalancedAllocation
    and ImageLocality with their default weights. The plugins see requested
    resources and image presence only; they are blind to processor speed,
    DAG structure, data locality, temperature, and failure history.
    """
    allocatable_cpu = float(node.node.cpu.logical_cpus)
    allocatable_memory = node.node.memory.capacity_mib - node.node.background_memory_mib
    requested_cpu = allocatable_cpu - free_cpu + task.cpu_cores
    requested_memory = allocatable_memory - free_memory + task.memory_mib
    cpu_fraction = min(1.0, max(0.0, requested_cpu / allocatable_cpu))
    memory_fraction = min(1.0, max(0.0, requested_memory / allocatable_memory))
    least_allocated = 100.0 * (
        (1.0 - cpu_fraction) + (1.0 - memory_fraction)) / 2.0
    balanced = 100.0 * (1.0 - abs(cpu_fraction - memory_fraction) / 2.0)
    image_locality = 0.0
    if task.image in node.warm_images:
        scaled_mib = task.image_size_mib * image_spread
        image_locality = 100.0 * min(1.0, max(0.0, (
            scaled_mib - IMAGE_LOCALITY_MIN_MIB)
            / (IMAGE_LOCALITY_MAX_MIB - IMAGE_LOCALITY_MIN_MIB)))
    return least_allocated + balanced + image_locality


class KubeDefaultPolicy:
    """Baseline modelled on the default Kubernetes scheduler profile.

    Pods are popped in PrioritySort order (priority, then FIFO by readiness)
    and each is bound to the highest-scoring feasible node; ties are broken by
    a seeded random draw, as kube-scheduler selects uniformly among the best.
    """

    name = "k8s-default"

    def __init__(self, seed: int = 0):
        self._random = random.Random(seed)

    def schedule(self, snapshot: SchedulerSnapshot) -> list[Placement]:
        free_cpu, free_memory = _available(snapshot)
        planned_tasks = _planned_tasks(snapshot)
        total_nodes = max(1, len(snapshot.nodes))
        workflow_order = {
            task.task_id: index for index, task in enumerate(snapshot.workflow.tasks)
        }
        ordered = sorted(
            snapshot.ready_tasks,
            key=lambda task: (
                -task_priority(task, snapshot.workflow).value,
                snapshot.tasks[task.task_id].ready_at_s or 0.0,
                workflow_order[task.task_id],
            ),
        )
        tie_break = {
            (task.task_id, node_id): self._random.random() * 1e-6
            for task in ordered for node_id in snapshot.nodes
        }
        placements = []
        for unit in _scheduling_units(snapshot, ordered):
            def cost(task, node, co_runners):
                spread = sum(
                    task.image in view.warm_images
                    for view in snapshot.nodes.values()) / total_nodes
                node_id = node.node.node_id
                # Tasks planned in this round (committed or tentative gang
                # members) follow the node's running tasks in ``co_runners``.
                planned = co_runners[len(node.running_task_ids):]
                free_cpu_now = node.free_logical_cpus - sum(
                    item.cpu_cores for item in planned)
                free_memory_now = node.free_memory_mib - sum(
                    item.memory_mib for item in planned)
                return (
                    KUBE_SCORE_CEILING
                    - kube_default_node_score(
                        task, node, free_cpu_now, free_memory_now, spread)
                    + tie_break[(task.task_id, node_id)]
                )

            selected = _best_unit_placement(
                snapshot, unit, free_cpu, free_memory, planned_tasks, cost)
            if selected:
                placements.extend(selected)
                _commit(selected, snapshot.workflow.tasks_by_id,
                        free_cpu, free_memory, planned_tasks)
        return placements


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
        "k8s-default": lambda: KubeDefaultPolicy(seed),
        "eft": EFTPolicy,
        "heft": HEFTPolicy,
        "heft-ra": ResourceAwareHEFTPolicy,
        "heft-ra-cap": CapacityAwareHEFTRAPolicy,
        "legacy": LegacyWeightedPolicy,
        "adaptive": AdaptivePolicy,
        "adaptive-no-preempt": AdaptiveNoPreemptionPolicy,
    }
    if normalized not in policies:
        raise ValueError(
            f"unknown simulation policy '{name}'; expected one of {sorted(policies)}"
        )
    return policies[normalized]()