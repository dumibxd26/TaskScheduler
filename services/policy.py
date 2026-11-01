"""
Pluggable scheduling policies for the live (Kubernetes) scheduler.

Every policy is a function over :class:`SchedulerState` that returns an
:class:`ActionSet` (place, preempt, or hold each queued task). Policies record
their tentative reservations on the in-memory :class:`Node` objects so later
decisions in the same tick see reduced capacity; the engine binds each place
action and rolls the reservation back if the binding fails.

Shared semantics, mirroring the discrete-event simulator:

* Feasibility is logical capacity. No policy overcommits a node; a task that
  fits nowhere is held (Adaptive may plan priority preemption instead).
* Gang members are placed as one atomic unit at the position of their first
  queued member, and only once every member of the gang is queued.
* EFT, HEFT and Adaptive estimate finish times with the same learned-profile
  ECT; they differ only in ordering, penalty terms, and preemption.

Available policies (``TS_POLICY``):

``legacy`` / ``legacy-v1``  exact eight-factor score, original weights
``legacy-tuned``            exact eight-factor score, tuned weights
``eft``                     FIFO order, minimum estimated finish time
``heft``                    controller-DAG upward rank order, minimum EFT
``adaptive``                priority/fairness + rank order, ECT with UCB,
                            failure and thermal terms, priority preemption
``adaptive-no-preempt``     Adaptive without preemption (ablation)
``kube-default``            kube-scheduler default scoring plugins
``calibrate``               least-sampled node for each template (warm-up)
"""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from models.cluster import ClusterScenario, Node
from models.enums import PriorityClass, TaskState
from models.workload import TaskTemplate, WorkflowTemplate
from services.ect import _get_expected_runtime, _get_startup

if TYPE_CHECKING:  # avoid runtime cycles
    from models.profile_store import ProfileStore
    from services.data_placement import DataPlacement
    from services.defrag import DefragPlanner
    from services.preemption import PreemptionPlanner
    from services.queue_manager import QueueManager, TaskEntry
    from services.scheduler import WorkflowSchedulerRunner


# ---------------------------------------------------------------------------
# Action types — what a Policy may decide for a single tick.
# ---------------------------------------------------------------------------

@dataclass
class PlaceAction:
    """Place ``task_instance_id`` on ``node_id``. Engine handles binding."""
    task_instance_id: str
    node_id: str
    expected_runtime: Optional[float] = None
    transfer_seconds: float = 0.0
    score: float = 0.0


@dataclass
class PreemptAction:
    """Evict a running task to free capacity."""
    victim_task_instance_id: str
    mode: str = "kill_restart"


@dataclass
class HoldAction:
    """Skip this task this tick (no feasible node, incomplete gang, ...)."""
    task_instance_id: str
    reason: str = ""


@dataclass
class ActionSet:
    """All decisions for a single tick. Lists are processed in declaration order."""
    place: List[PlaceAction] = field(default_factory=list)
    preempt: List[PreemptAction] = field(default_factory=list)
    hold: List[HoldAction] = field(default_factory=list)


@dataclass(frozen=True)
class SchedulerState:
    cluster: ClusterScenario
    queue: "QueueManager"
    profile_store: "ProfileStore"
    workflow_templates: Dict[str, WorkflowTemplate]
    now: float
    # Pre-sorted task entries (highest effective priority first).
    sorted_tasks: List["TaskEntry"] = field(default_factory=list)
    data_placement: Optional["DataPlacement"] = None


class Policy(ABC):
    """Pluggable scheduling brain. One per scheduler instance."""

    @abstractmethod
    def decide(self, state: SchedulerState) -> ActionSet:
        """Given full state, return the action set for this tick."""

    def name(self) -> str:
        return self.__class__.__name__


POLICY_ENV_VAR = "TS_POLICY"
DEFAULT_POLICY = "legacy"
MIN_SAMPLES_PER_NODE = 3
_W_FAILURE = 50.0          # Adaptive penalty per unit failure rate in [0, 1]
_W_THERMAL_HOT = 20.0      # Adaptive penalty when thermal headroom is low
_THERMAL_HOT_HEADROOM_C = 10.0
_UCB_BETA = 1.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def task_template_for(state: SchedulerState, entry: "TaskEntry") -> TaskTemplate:
    wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
    return wf_tmpl.tasks[entry.task_template_id]


def feasible_nodes(cluster: ClusterScenario, tmpl: TaskTemplate) -> List[Node]:
    """Compatible nodes whose remaining logical capacity fits the task."""
    return [
        node for node in cluster.nodes
        if node.node_type in tmpl.compatible_node_types
        and node.free_cpu >= tmpl.cpu_request - 1e-9
        and node.free_memory >= tmpl.memory_request - 1e-9
    ]


def _gang_key(state: SchedulerState, entry: "TaskEntry") -> Optional[Tuple[str, str]]:
    gang_id = task_template_for(state, entry).gang_group_id
    if not gang_id:
        return None
    return entry.workflow.workflow_instance_id, gang_id


def _gang_size(entry: "TaskEntry", default: int) -> int:
    size = getattr(entry.task, "gang_size", None)
    return int(size) if size else default


def scheduling_units(state: SchedulerState, ordered: Sequence["TaskEntry"],
                     ) -> Tuple[List[List["TaskEntry"]], List[HoldAction]]:
    """Group gang members into atomic units at their first queued position.

    A gang is emitted only when every member is queued: ``gang_size`` comes
    from the controller annotation in Kubernetes and from the workflow
    template in the in-process engine.
    """
    members: Dict[Tuple[str, str], List["TaskEntry"]] = {}
    for entry in ordered:
        key = _gang_key(state, entry)
        if key is not None:
            members.setdefault(key, []).append(entry)
    units: List[List["TaskEntry"]] = []
    holds: List[HoldAction] = []
    emitted: set = set()
    for entry in ordered:
        key = _gang_key(state, entry)
        if key is None:
            units.append([entry])
            continue
        if key in emitted:
            continue
        emitted.add(key)
        group = members[key]
        wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
        template_members = sum(
            1 for tmpl in wf_tmpl.tasks.values() if tmpl.gang_group_id == key[1])
        expected = _gang_size(entry, template_members)
        if len(group) >= expected:
            units.append(group)
        else:
            holds.extend(
                HoldAction(member.task.task_instance_id, "gang_incomplete")
                for member in group)
    return units, holds


def _register(state: SchedulerState, entry: "TaskEntry", node: Node,
              tmpl: TaskTemplate) -> PlaceAction:
    expected = state.profile_store.get_expected_runtime(
        entry.task_template_id, node.node_id)
    node.register_task(
        entry.task_template_id, entry.task.task_instance_id,
        expected_runtime=expected,
        cpu_request=tmpl.cpu_request,
        memory_request=tmpl.memory_request,
    )
    return PlaceAction(
        task_instance_id=entry.task.task_instance_id,
        node_id=node.node_id,
        expected_runtime=expected,
    )


def rollback(cluster: ClusterScenario, placed: Sequence[PlaceAction]) -> None:
    nodes = {node.node_id: node for node in cluster.nodes}
    for action in placed:
        node = nodes.get(action.node_id)
        if node is not None:
            node.unregister_task(action.task_instance_id)


def coverage_choice(state: SchedulerState, entry: "TaskEntry",
                    feasible: Sequence[Node]) -> Optional[Node]:
    """Least-sampled feasible node until each has MIN_SAMPLES_PER_NODE runs."""
    profile = state.profile_store.get_profile(entry.task_template_id)
    observations = profile.metrics_by_node if profile else {}
    counts = {
        node.node_id: observations[node.node_id].count
        if node.node_id in observations else 0
        for node in feasible
    }
    minimum = min(counts.values())
    if minimum >= MIN_SAMPLES_PER_NODE:
        return None
    return min(
        (node for node in feasible if counts[node.node_id] == minimum),
        key=lambda node: node.node_id,
    )


def estimated_finish(state: SchedulerState, entry: "TaskEntry", node: Node,
                     tmpl: TaskTemplate) -> float:
    """Learned-profile ECT: now + startup + input transfer + runtime."""
    from services.data_placement import DataPlacement
    from services.ect import task_finish_time

    wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
    data_placement = (
        state.data_placement if state.data_placement is not None else DataPlacement())
    return task_finish_time(
        entry.task, tmpl, node, entry.workflow, wf_tmpl,
        state.profile_store, data_placement, state.cluster, state.now)


def place_units(state: SchedulerState, units: Sequence[Sequence["TaskEntry"]],
                choose: Callable[["TaskEntry", TaskTemplate, List[Node]],
                                 Optional[Tuple[Node, float]]],
                actions: ActionSet,
                on_unplaced: Optional[Callable[["TaskEntry"], bool]] = None) -> None:
    """Place units in order with all-or-nothing gang semantics.

    ``choose`` returns ``(node, score)`` or None. ``on_unplaced`` may plan a
    preemption for a single-task unit and returns True when it did.
    """
    for unit in units:
        placed: List[PlaceAction] = []
        failed_entry = None
        for entry in unit:
            tmpl = task_template_for(state, entry)
            feasible = feasible_nodes(state.cluster, tmpl)
            choice = choose(entry, tmpl, feasible) if feasible else None
            if choice is None:
                failed_entry = entry
                break
            node, score = choice
            action = _register(state, entry, node, tmpl)
            action.score = score
            placed.append(action)
        if failed_entry is None:
            actions.place.extend(placed)
            continue
        rollback(state.cluster, placed)
        if len(unit) == 1 and on_unplaced is not None and on_unplaced(failed_entry):
            continue
        reason = "gang_not_schedulable" if len(unit) > 1 else "no_feasible_node"
        actions.hold.extend(
            HoldAction(entry.task.task_instance_id, reason) for entry in unit)


# ---------------------------------------------------------------------------
# Legacy eight-factor policy (exact live scorer, v1 or tuned weights)
# ---------------------------------------------------------------------------

class LegacyEightFactorPolicy(Policy):
    """Per-task greedy weighted-sum scoring (services/scheduler.py)."""

    def __init__(self, runner: "WorkflowSchedulerRunner"):
        self.runner = runner

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        units, holds = scheduling_units(state, state.sorted_tasks)
        actions.hold.extend(holds)

        def choose(entry, tmpl, feasible):
            # The runner applies Legacy's own coverage, depth, and 10% random
            # exploration rules, then registers the reservation itself.
            chosen = self.runner.schedule_task(
                entry.task, tmpl, ClusterScenario(
                    scenario_id=state.cluster.scenario_id,
                    name=state.cluster.name,
                    description=state.cluster.description,
                    nodes=feasible,
                    bandwidth_matrix=state.cluster.bandwidth_matrix,
                ),
                parent_node_ids=entry.task.parent_node_ids)
            chosen.unregister_task(entry.task.task_instance_id)
            return chosen, 0.0

        place_units(state, units, choose, actions)
        return actions


# ---------------------------------------------------------------------------
# EFT / HEFT
# ---------------------------------------------------------------------------

class EFTPolicy(Policy):
    """FIFO list scheduling with minimum learned estimated finish time."""

    def order(self, state: SchedulerState) -> List["TaskEntry"]:
        return sorted(state.sorted_tasks, key=lambda entry: entry.enqueue_time)

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        units, holds = scheduling_units(state, self.order(state))
        actions.hold.extend(holds)

        def choose(entry, tmpl, feasible):
            explore = coverage_choice(state, entry, feasible)
            if explore is not None:
                return explore, 0.0
            scored = [
                (estimated_finish(state, entry, node, tmpl), node.node_id, node)
                for node in feasible
            ]
            score, _, node = min(scored, key=lambda item: (item[0], item[1]))
            return node, score

        place_units(state, units, choose, actions)
        return actions


class HEFTPolicy(EFTPolicy):
    """Upward-rank order (controller-published DAG) with EFT placement."""

    def order(self, state: SchedulerState) -> List["TaskEntry"]:
        return sorted(
            state.sorted_tasks,
            key=lambda entry: (-entry.task.upward_rank, entry.enqueue_time))


class ResourceAwareHEFTPolicy(HEFTPolicy):
    """Live HEFT-RA: HEFT order, contention-aware ECT, and DAG lookahead.

    Mirrors the simulator's HEFT-RA with the signals a live scheduler has:

    * every remote input pull from a producer shares that producer's egress
      with pulls planned earlier in this tick and pulls started recently,
      so fan-out children are not all priced as if they had the link alone;
    * a producer is scored by the latest finish of its DAG children, each at
      its best learned node, with siblings pulling concurrently.

    Live nodes report no failures or temperature in the validation matrix,
    so the simulator's thermal and failure-risk terms reduce to the learned
    runtime and failure rate already present in the profiles.
    """

    RECENT_FLOW_WINDOW_S = 20.0

    def __init__(self):
        # (producer node, consumer node, start time, expected seconds)
        self._recent_flows: List[Tuple[str, str, float, float]] = []

    def _bandwidth(self, state: SchedulerState, source: str, target: str) -> float:
        return max(state.cluster.get_bandwidth(source, target), 1.0)

    def _egress_load(self, state: SchedulerState, producer: str,
                     planned: List[Tuple[str, str]]) -> int:
        recent = sum(
            1 for source, _, started, duration in self._recent_flows
            if source == producer and state.now <= started + duration)
        return recent + sum(1 for source, _ in planned if source == producer)

    def _transfer_s(self, state: SchedulerState, entry: "TaskEntry", node: Node,
                    planned: List[Tuple[str, str]]) -> float:
        slowest = 0.0
        for producer, size in entry.task.input_bytes_by_node.items():
            if producer == node.node_id or size <= 0:
                continue
            sharing = self._egress_load(state, producer, planned) + 1
            slowest = max(slowest, size * sharing / self._bandwidth(
                state, producer, node.node_id))
        return slowest

    def _lookahead(self, state: SchedulerState, entry: "TaskEntry",
                   node: Node, own_finish: float) -> float:
        children = entry.task.children
        if not children:
            return own_finish
        siblings = sum(1 for _, _, size in children if size > 0)
        latest = own_finish
        for _, template_id, size in children:
            best = None
            for candidate in state.cluster.nodes:
                cost = (
                    _get_startup(state.profile_store, template_id, candidate)
                    + _get_expected_runtime(state.profile_store, template_id, candidate)
                )
                if candidate.node_id != node.node_id and size > 0:
                    cost += size * max(1, siblings) / self._bandwidth(
                        state, node.node_id, candidate.node_id)
                best = cost if best is None else min(best, cost)
            if best is not None:
                latest = max(latest, own_finish + best)
        return latest

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        self._recent_flows = [
            flow for flow in self._recent_flows
            if state.now <= flow[2] + flow[3] + self.RECENT_FLOW_WINDOW_S]
        units, holds = scheduling_units(state, self.order(state))
        actions.hold.extend(holds)
        planned: List[Tuple[str, str]] = []

        def choose(entry, tmpl, feasible):
            explore = coverage_choice(state, entry, feasible)
            if explore is not None:
                node, score = explore, 0.0
            else:
                scored = []
                for node in feasible:
                    own = (
                        state.now
                        + _get_startup(state.profile_store, entry.task_template_id, node)
                        + self._transfer_s(state, entry, node, planned)
                        + _get_expected_runtime(
                            state.profile_store, entry.task_template_id, node)
                    )
                    scored.append((self._lookahead(state, entry, node, own),
                                   node.node_id, node))
                score, _, node = min(scored, key=lambda item: (item[0], item[1]))
            for producer, size in entry.task.input_bytes_by_node.items():
                if producer != node.node_id and size > 0:
                    planned.append((producer, node.node_id))
                    self._recent_flows.append((
                        producer, node.node_id, state.now,
                        size / self._bandwidth(state, producer, node.node_id)))
            return node, score

        place_units(state, units, choose, actions)
        return actions


class CapacityAwareHEFTRAPolicy(ResourceAwareHEFTPolicy):
    """Live HEFT-RA whose lookahead respects the capacity children will find.

    The children of a producer are assigned the way the (non-delay) list
    scheduler will place them: each goes to the best node that still has a
    free slot in the CPU capacity free when the producer is scored (this
    tick's reservations included; the producer's own request is not yet
    registered, which models its slot being released when it finishes). A
    child waits for the earliest sibling only when every node is full, and
    only when no node has capacity at all does the estimate fall back to the
    optimistic bound.
    """

    def _lookahead(self, state: SchedulerState, entry: "TaskEntry",
                   node: Node, own_finish: float) -> float:
        children = entry.task.children
        if not children:
            return own_finish
        workflow_template = state.workflow_templates.get(
            entry.workflow.workflow_template_id)
        producer_template = (workflow_template.tasks.get(entry.task_template_id)
                             if workflow_template is not None else None)
        siblings = sum(1 for _, _, size in children if size > 0)
        assigned: Dict[str, List[float]] = {}
        latest = own_finish
        for _, template_id, size in sorted(children):
            # A child's Pod does not exist yet, so its template is usually
            # unknown to this workflow; any workflow that used the same
            # template knows its request, and otherwise the producer's own
            # size is the best available guess.
            template = _known_template(state, workflow_template, template_id)
            request = (template.cpu_request if template is not None
                       else producer_template.cpu_request
                       if producer_template is not None else 0.0)
            free: List[Tuple[float, str]] = []
            waiting: List[Tuple[float, str]] = []
            optimistic = None
            for candidate in state.cluster.nodes:
                if (template is not None and candidate.node_type
                        not in template.compatible_node_types):
                    continue
                arrival = own_finish
                if candidate.node_id != node.node_id and size > 0:
                    arrival += size * max(1, siblings) / self._bandwidth(
                        state, node.node_id, candidate.node_id)
                duration = (
                    _get_startup(state.profile_store, template_id, candidate)
                    + _get_expected_runtime(
                        state.profile_store, template_id, candidate))
                if optimistic is None or arrival + duration < optimistic:
                    optimistic = arrival + duration
                slots = (int((candidate.free_cpu + 1e-9) // request)
                         if request > 0 else len(children))
                if slots <= 0:
                    continue
                taken = assigned.get(candidate.node_id, [])
                if len(taken) < slots:
                    free.append((arrival + duration, candidate.node_id))
                else:
                    released = sorted(taken)[len(taken) - slots]
                    waiting.append(
                        (max(arrival, released) + duration, candidate.node_id))
            options = free or waiting
            if options:
                finish, chosen = min(options)
                assigned.setdefault(chosen, []).append(finish)
            else:
                finish = optimistic
            if finish is not None:
                latest = max(latest, finish)
        return latest


def _known_template(state: SchedulerState,
                    workflow_template: Optional[WorkflowTemplate],
                    template_id: str) -> Optional[TaskTemplate]:
    """A task template by id: this workflow's first, then any other's."""
    if workflow_template is not None and template_id in workflow_template.tasks:
        return workflow_template.tasks[template_id]
    for other in state.workflow_templates.values():
        if template_id in other.tasks:
            return other.tasks[template_id]
    return None


# ---------------------------------------------------------------------------
# Adaptive (the project's composite policy)
# ---------------------------------------------------------------------------

class AdaptivePolicy(Policy):
    """Priority/fairness-ordered ECT scheduler with UCB, risk and preemption."""

    def __init__(self, *,
                 preemption_planner: Optional["PreemptionPlanner"] = None,
                 defrag_planner: Optional["DefragPlanner"] = None):
        self._preemption = preemption_planner
        self._defrag = defrag_planner

    def name(self) -> str:
        return "AdaptivePolicy" if self._preemption else "AdaptiveNoPreemptPolicy"

    def record_preemption(self, node_id: str, task_id: str, now: float):
        if self._preemption is not None:
            self._preemption.record_preemption(node_id, task_id, now)

    def order(self, state: SchedulerState) -> List["TaskEntry"]:
        groups: Dict[str, List["TaskEntry"]] = {}
        for entry in state.sorted_tasks:
            groups.setdefault(entry.workflow.workflow_instance_id, []).append(entry)
        workflow_order = sorted(
            groups,
            key=lambda wfid: (
                -max(entry.effective_priority.value for entry in groups[wfid]),
                groups[wfid][0].workflow.vruntime,
                groups[wfid][0].workflow.arrival_time or 0.0,
                wfid,
            ),
        )
        ordered: List["TaskEntry"] = []
        for wfid in workflow_order:
            ordered.extend(sorted(
                groups[wfid],
                key=lambda entry: (-entry.task.upward_rank, entry.enqueue_time)))
        return ordered

    def score(self, state: SchedulerState, entry: "TaskEntry", node: Node,
              tmpl: TaskTemplate) -> float:
        store = state.profile_store
        ect = estimated_finish(state, entry, node, tmpl)
        ucb = store.get_ucb_score(entry.task_template_id, node.node_id, beta=_UCB_BETA)
        expected = store.get_expected_runtime(entry.task_template_id, node.node_id)
        if ucb is not None and expected is not None:
            ect += min(0.0, ucb - expected)
        ect += store.get_failure_rate(entry.task_template_id, node.node_id) * _W_FAILURE
        if (node.cpu_temperature is not None
                and node.thermal_headroom is not None
                and node.thermal_headroom < _THERMAL_HOT_HEADROOM_C):
            ect += _W_THERMAL_HOT
        return ect

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        if self._preemption is not None:
            self._preemption.begin_tick()
        ordered = self.order(state)
        units, holds = scheduling_units(state, ordered)
        actions.hold.extend(holds)

        def choose(entry, tmpl, feasible):
            explore = coverage_choice(state, entry, feasible)
            if explore is not None:
                return explore, 0.0
            scored = [
                (self.score(state, entry, node, tmpl), node.node_id, node)
                for node in feasible
            ]
            score, _, node = min(scored, key=lambda item: (item[0], item[1]))
            return node, score

        def on_unplaced(entry) -> bool:
            if self._preemption is None:
                return False
            victims = self._preemption.find_victims(entry, state)
            if not victims:
                return False
            actions.preempt.extend(victims)
            # The urgent task stays queued and is placed on the next tick,
            # after the engine has deleted the victims and freed capacity.
            actions.hold.append(HoldAction(
                entry.task.task_instance_id, "awaiting_preemption"))
            return True

        place_units(state, units, choose, actions, on_unplaced)

        if self._defrag:
            held_ids = {hold.task_instance_id for hold in actions.hold}
            held_entries = [
                entry for entry in ordered if entry.task.task_instance_id in held_ids]
            # Defragmentation is advisory: migrations are logged, not applied.
            for migration in self._defrag.reactive_pass(held_entries, state):
                print(f"[DEFRAG] advisory: migrate '{migration.task_instance_id}' "
                      f"{migration.source_node} -> {migration.dest_node}")
        return actions


# ---------------------------------------------------------------------------
# kube-scheduler default scoring baseline
# ---------------------------------------------------------------------------

IMAGE_LOCALITY_MIN_MIB = 23.0
IMAGE_LOCALITY_MAX_MIB = 1000.0


def kube_default_score(node: Node, tmpl: TaskTemplate,
                       image_spread: float, image_size_mib: float) -> float:
    """NodeResourcesFit(LeastAllocated) + BalancedAllocation + ImageLocality."""
    requested_cpu = node.total_cpu - node.free_cpu + tmpl.cpu_request
    requested_memory = node.total_memory - node.free_memory + tmpl.memory_request
    cpu_fraction = min(1.0, max(0.0, requested_cpu / max(node.total_cpu, 1e-9)))
    memory_fraction = min(
        1.0, max(0.0, requested_memory / max(node.total_memory, 1e-9)))
    least_allocated = 100.0 * ((1 - cpu_fraction) + (1 - memory_fraction)) / 2
    balanced = 100.0 * (1.0 - abs(cpu_fraction - memory_fraction) / 2)
    image_locality = 0.0
    if tmpl.image_name in node.warm_images:
        scaled = image_size_mib * image_spread
        image_locality = 100.0 * min(1.0, max(0.0, (
            scaled - IMAGE_LOCALITY_MIN_MIB)
            / (IMAGE_LOCALITY_MAX_MIB - IMAGE_LOCALITY_MIN_MIB)))
    return least_allocated + balanced + image_locality


class KubeDefaultPolicy(Policy):
    """Default kube-scheduler profile over the scheduler's capacity model.

    PrioritySort queue order and the three default scoring plugins that apply
    to bare Pods. It ignores node speed, DAG position, data location,
    temperature, and history; ties are broken by a seeded random draw.
    """

    def __init__(self, seed: int = 0, image_size_mib: float = 150.0):
        self._random = random.Random(seed)
        self.image_size_mib = image_size_mib

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        ordered = sorted(
            state.sorted_tasks,
            key=lambda entry: (-entry.effective_priority.value, entry.enqueue_time))
        units, holds = scheduling_units(state, ordered)
        actions.hold.extend(holds)
        total_nodes = max(1, len(state.cluster.nodes))

        def choose(entry, tmpl, feasible):
            spread = sum(
                tmpl.image_name in node.warm_images
                for node in state.cluster.nodes) / total_nodes
            scored = [
                (kube_default_score(node, tmpl, spread, self.image_size_mib),
                 self._random.random(), node)
                for node in feasible
            ]
            best, _, node = max(scored, key=lambda item: (item[0], item[1]))
            return node, best

        place_units(state, units, choose, actions)
        return actions


class CalibrationPolicy(Policy):
    """Warm-up policy: gather MIN_SAMPLES_PER_NODE runs per (template, node).

    Samples already observed plus runs in flight are counted, and a task is
    held rather than sent to an already-covered node while some compatible
    node still needs samples, so every pair ends with the target count.
    """

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        ordered = sorted(state.sorted_tasks, key=lambda entry: entry.enqueue_time)
        units, holds = scheduling_units(state, ordered)
        actions.hold.extend(holds)

        def count(template_id: str, node: Node) -> int:
            profile = state.profile_store.get_profile(template_id)
            observed = (profile.metrics_by_node[node.node_id].count
                        if profile and node.node_id in profile.metrics_by_node else 0)
            in_flight = sum(
                1 for running in node.active_tasks.values()
                if running.task_template_id == template_id)
            return observed + in_flight

        def choose(entry, tmpl, feasible):
            compatible = [
                node for node in state.cluster.nodes
                if node.node_type in tmpl.compatible_node_types]
            needy = {
                node.node_id for node in compatible
                if count(entry.task_template_id, node) < MIN_SAMPLES_PER_NODE}
            candidates = (
                [node for node in feasible if node.node_id in needy]
                if needy else list(feasible))
            if not candidates:
                return None
            node = min(candidates, key=lambda item: (
                count(entry.task_template_id, item), item.node_id))
            return node, 0.0

        place_units(state, units, choose, actions)
        return actions


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

LIVE_POLICIES = (
    "legacy", "legacy-v1", "legacy-tuned", "eft", "heft", "heft-ra",
    "heft-ra-cap", "adaptive", "adaptive-no-preempt", "kube-default",
    "calibrate",
)


def build_policy(name: Optional[str] = None, *,
                 runner: Optional["WorkflowSchedulerRunner"] = None,
                 preemption_planner: Optional["PreemptionPlanner"] = None,
                 defrag_planner: Optional["DefragPlanner"] = None,
                 seed: int = 0) -> Policy:
    """Construct the policy named by ``name`` (or by ``$TS_POLICY``)."""
    selected = (name or os.environ.get(POLICY_ENV_VAR) or DEFAULT_POLICY).lower()
    selected = selected.replace("_", "-")
    if selected in ("legacy", "legacy-v1", "legacy-tuned",
                    "legacy-eight-factor", "eight-factor"):
        if runner is None:
            raise ValueError("LegacyEightFactorPolicy requires runner=")
        from services.scheduler import legacy_weights_for_policy
        runner.algorithm.weights = legacy_weights_for_policy(selected)
        return LegacyEightFactorPolicy(runner=runner)
    if selected in ("adaptive", "adaptive-ect", "ect"):
        if preemption_planner is None:
            from services.preemption import PreemptionPlanner
            preemption_planner = PreemptionPlanner()
        if defrag_planner is None:
            from services.defrag import DefragPlanner
            defrag_planner = DefragPlanner()
        return AdaptivePolicy(
            preemption_planner=preemption_planner,
            defrag_planner=defrag_planner,
        )
    if selected == "adaptive-no-preempt":
        return AdaptivePolicy(preemption_planner=None, defrag_planner=None)
    if selected == "eft":
        return EFTPolicy()
    if selected == "heft":
        return HEFTPolicy()
    if selected == "heft-ra":
        return ResourceAwareHEFTPolicy()
    if selected == "heft-ra-cap":
        return CapacityAwareHEFTRAPolicy()
    if selected in ("kube-default", "k8s-default"):
        return KubeDefaultPolicy(seed=seed)
    if selected == "calibrate":
        return CalibrationPolicy()
    raise ValueError(
        f"Unknown policy '{selected}'. Available: {', '.join(LIVE_POLICIES)}.")
