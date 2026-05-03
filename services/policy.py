"""
Pluggable scheduling policy interface.

Phase 0 of the migration plan from ImplementationArchitecture.md introduces this
seam without changing scheduler behaviour. Every policy is a pure function over
``SchedulerState`` that returns an ``ActionSet`` describing what the engine
should do this tick (place, preempt, or hold each candidate task).

The legacy 8-factor scoring code lives in ``services.scheduler`` and is wrapped
verbatim by ``LegacyEightFactorPolicy`` so the existing simulation tests keep
producing the same decisions. Future policies (FCFS, HEFT, Adaptive) plug into
the same interface — see ImplementationArchitecture.md Part V.1 / IX.2.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from models.cluster import ClusterScenario
from models.enums import TaskState, PriorityClass
from models.workload import WorkflowTemplate

if TYPE_CHECKING:  # avoid runtime cycles
    from services.queue_manager import QueueManager, TaskEntry
    from services.scheduler import WorkflowSchedulerRunner
    from models.profile_store import ProfileStore
    from services.data_placement import DataPlacement
    from services.preemption import PreemptionPlanner
    from services.defrag import DefragPlanner


# ---------------------------------------------------------------------------
# Action types — what a Policy may decide for a single tick.
# ---------------------------------------------------------------------------

@dataclass
class PlaceAction:
    """Place ``task_instance_id`` on ``node_id``. Engine handles binding."""
    task_instance_id: str
    node_id: str
    expected_runtime: Optional[float] = None     # for register_task on the chosen node
    transfer_seconds: float = 0.0                # initContainer cost (Channel B); 0 today
    score: float = 0.0                           # opaque; legacy stuffs total here


@dataclass
class PreemptAction:
    """Evict a running task to free capacity. Phase 4 wires the planner."""
    victim_task_instance_id: str
    mode: str = "kill_restart"                   # or "checkpoint"


@dataclass
class HoldAction:
    """Skip this task this tick — no feasible node / waiting for dependency."""
    task_instance_id: str
    reason: str = ""


@dataclass
class ActionSet:
    """All decisions for a single tick. Lists are processed in declaration order."""
    place: List[PlaceAction] = field(default_factory=list)
    preempt: List[PreemptAction] = field(default_factory=list)
    hold: List[HoldAction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SchedulerState — read-only aggregate handed to ``Policy.decide``.
# Frozen so the policy cannot accidentally mutate the shared state; the engine
# is the only thing allowed to apply the action set returned by the policy.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchedulerState:
    cluster: ClusterScenario
    queue: "QueueManager"
    profile_store: "ProfileStore"
    workflow_templates: Dict[str, WorkflowTemplate]
    now: float
    # Pre-sorted task entries (highest effective priority first). The engine
    # builds this once per tick from QueueManager.get_sorted_tasks().
    sorted_tasks: List["TaskEntry"] = field(default_factory=list)
    # Phase 2+: references to subsystems used by AdaptivePolicy.
    data_placement: Optional["DataPlacement"] = None


# ---------------------------------------------------------------------------
# Policy ABC.
# ---------------------------------------------------------------------------

class Policy(ABC):
    """Pluggable scheduling brain. One per scheduler instance."""

    @abstractmethod
    def decide(self, state: SchedulerState) -> ActionSet:
        """Pure function: given full state, return the action set for this tick."""

    def name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# LegacyEightFactorPolicy — wraps the existing per-task greedy dispatcher.
#
# Reproduces the exact decision sequence of the pre-Phase-0 engine so the
# default behaviour is preserved. Future policies (HEFT, Adaptive) replace
# this class but live behind the same interface.
# ---------------------------------------------------------------------------

class LegacyEightFactorPolicy(Policy):
    """Per-task greedy weighted-sum scoring (services/scheduler.py)."""

    def __init__(self, runner: "WorkflowSchedulerRunner"):
        self.runner = runner

    def decide(self, state: SchedulerState) -> ActionSet:
        actions = ActionSet()
        sorted_tasks = state.sorted_tasks

        for entry in sorted_tasks:
            wf_template = state.workflow_templates[entry.workflow.workflow_template_id]
            task_template = wf_template.tasks[entry.task_template_id]

            # Feasibility against current (already mutated) node capacity.
            feasible = [
                n for n in state.cluster.nodes
                if n.node_type in task_template.compatible_node_types
                and n.free_cpu >= task_template.cpu_request
                and n.free_memory >= task_template.memory_request
            ]

            if not feasible:
                # CRITICAL tasks force-dispatch to any compatible node — preserve
                # legacy carve-out exactly.
                if entry.effective_priority == PriorityClass.CRITICAL:
                    feasible = [n for n in state.cluster.nodes
                                if n.node_type in task_template.compatible_node_types]
                if not feasible:
                    actions.hold.append(HoldAction(
                        task_instance_id=entry.task.task_instance_id,
                        reason="no_feasible_node",
                    ))
                    continue

            # Delegate the actual node choice to the existing runner. It also
            # registers the task on the chosen node (capacity decrement).
            chosen = self.runner.schedule_task(
                entry.task, task_template, state.cluster)

            actions.place.append(PlaceAction(
                task_instance_id=entry.task.task_instance_id,
                node_id=chosen.node_id,
                expected_runtime=state.profile_store.get_expected_runtime(
                    entry.task.task_template_id, chosen.node_id),
            ))

        return actions


# ---------------------------------------------------------------------------
# Registry / factory — selects the active policy from an env var or a flag.
# Default is "legacy" so unmodified runs reproduce today's behaviour.
# ---------------------------------------------------------------------------

POLICY_ENV_VAR = "TS_POLICY"
DEFAULT_POLICY = "legacy"

# ---------------------------------------------------------------------------
# AdaptivePolicy — two-tier ECT-based scheduler (Phase 3).
#
# Outer loop: sort workflows by (priority_class desc, vruntime asc, arrival asc).
# Inner loop: within each workflow, sort ready tasks by upward_rank desc.
# Scoring: ECT(task, node) using learned predictions + UCB exploration bonus.
# Handles: preemption (Phase 4), gang scheduling (Phase 4), thermal (Phase 5).
# ---------------------------------------------------------------------------

# Weights for the composite objective J(A). Used as scoring penalties on
# top of the raw ECT estimate (lower score = better candidate).
_W_FAILURE = 50.0          # penalty per unit failure_rate (∈ [0,1])
_W_THERMAL_HOT = 20.0      # extra cost when thermal_headroom < THERMAL_HOT_HEADROOM_C
_THERMAL_HOT_HEADROOM_C = 10.0
_UCB_BETA = 1.0            # exploration coefficient for UCB-based node scoring
_EXPLORATION_RATE = 0.10   # 10% random exploration on cold-start (task, node) pairs


class AdaptivePolicy(Policy):
    """Two-tier ECT-based adaptive scheduler (the thesis algorithm)."""

    def __init__(self, *,
                 preemption_planner: Optional["PreemptionPlanner"] = None,
                 defrag_planner: Optional["DefragPlanner"] = None):
        self._preemption = preemption_planner
        self._defrag = defrag_planner

    def name(self) -> str:
        return "AdaptivePolicy"

    def decide(self, state: SchedulerState) -> ActionSet:
        import random
        from services.ect import task_finish_time, DEFAULT_RUNTIME_S
        from services.data_placement import DataPlacement

        actions = ActionSet()
        # Engine always passes a DataPlacement; fall back to a fresh empty
        # one for direct-call (test) usage so transfer_seconds() doesn't crash.
        dp = state.data_placement if state.data_placement is not None else DataPlacement()
        store = state.profile_store

        # ------------------------------------------------------------------
        # Two-tier ordering:
        # 1. Group entries by workflow, sort workflows by (priority desc,
        #    vruntime asc, arrival asc).
        # 2. Within each workflow, sort tasks by upward_rank desc.
        # ------------------------------------------------------------------
        wf_groups: Dict[str, List["TaskEntry"]] = {}
        for entry in state.sorted_tasks:
            wfid = entry.workflow.workflow_instance_id
            wf_groups.setdefault(wfid, []).append(entry)

        # Workflow ordering key.
        wf_order = sorted(
            wf_groups.keys(),
            key=lambda wfid: (
                -wf_groups[wfid][0].effective_priority.value,
                wf_groups[wfid][0].workflow.vruntime,
                wf_groups[wfid][0].workflow.arrival_time or 0.0,
            ),
        )

        ordered_entries: List["TaskEntry"] = []
        for wfid in wf_order:
            entries = wf_groups[wfid]
            entries.sort(key=lambda e: -e.task.upward_rank)
            ordered_entries.extend(entries)

        # ------------------------------------------------------------------
        # Gang grouping: collect gang groups so we can enforce atomicity.
        # ------------------------------------------------------------------
        gang_groups: Dict[Tuple[str, str], List["TaskEntry"]] = {}
        non_gang: List["TaskEntry"] = []
        for entry in ordered_entries:
            wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
            tmpl = wf_tmpl.tasks[entry.task_template_id]
            if tmpl.gang_group_id:
                key = (entry.workflow.workflow_instance_id, tmpl.gang_group_id)
                gang_groups.setdefault(key, []).append(entry)
            else:
                non_gang.append(entry)

        # Track cumulative node usage during this tick for occupancy.
        occupancy: Dict[str, float] = {}

        def _try_place(entry: "TaskEntry") -> bool:
            """Score all feasible nodes and place the task on the best one.
            Returns True if placed, False if held."""
            wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
            tmpl = wf_tmpl.tasks[entry.task_template_id]

            # Feasibility filter.
            feasible = [
                n for n in state.cluster.nodes
                if n.node_type in tmpl.compatible_node_types
                and n.free_cpu >= tmpl.cpu_request
                and n.free_memory >= tmpl.memory_request
            ]

            # CRITICAL force-dispatch fallback.
            if not feasible and entry.effective_priority == PriorityClass.CRITICAL:
                feasible = [n for n in state.cluster.nodes
                            if n.node_type in tmpl.compatible_node_types]

            if not feasible:
                return False

            # Profile exploration: if (task, node) is cold, explore randomly
            # with probability _EXPLORATION_RATE.
            explore = False
            profile = store.get_profile(entry.task_template_id)
            if profile:
                for n in feasible:
                    m = profile.metrics_by_node.get(n.node_id)
                    if m and m.is_drifting():
                        explore = True
                        break
                if profile.exploration_level < 1.0:
                    explore = True

            if explore and random.random() < _EXPLORATION_RATE:
                chosen = random.choice(feasible)
            else:
                # Score each node by ECT (lower = better) + UCB.
                best_node = feasible[0]
                best_score = float("inf")
                for n in feasible:
                    # ECT-based score.
                    ect = task_finish_time(
                        entry.task, tmpl, n,
                        entry.workflow, wf_tmpl,
                        store,
                        dp,
                        state.cluster,
                        state.now,
                        occupancy,
                    )

                    # UCB exploration bonus: prefer the more optimistic of
                    # the two estimates so we explore high-variance nodes.
                    ucb = store.get_ucb_score(
                        entry.task_template_id, n.node_id, beta=_UCB_BETA)
                    if ucb is not None:
                        ect = min(ect, ucb)

                    # Failure penalty (J(A) risk term).
                    fail_rate = store.get_failure_rate(
                        entry.task_template_id, n.node_id)
                    ect += fail_rate * _W_FAILURE

                    # Thermal headroom penalty (Phase 5).
                    if (n.cpu_temperature is not None
                            and n.thermal_headroom is not None
                            and n.thermal_headroom < _THERMAL_HOT_HEADROOM_C):
                        ect += _W_THERMAL_HOT

                    if ect < best_score:
                        best_score = ect
                        best_node = n

                chosen = best_node

            # Register capacity reservation.
            expected_rt = store.get_expected_runtime(
                entry.task_template_id, chosen.node_id)
            chosen.register_task(
                entry.task_template_id, entry.task.task_instance_id,
                expected_runtime=expected_rt,
                cpu_request=tmpl.cpu_request,
                memory_request=tmpl.memory_request,
            )

            # Update occupancy tracker for future ECT calculations this tick.
            occ_end = (occupancy.get(chosen.node_id, state.now)
                       + (expected_rt or DEFAULT_RUNTIME_S))
            occupancy[chosen.node_id] = occ_end

            actions.place.append(PlaceAction(
                task_instance_id=entry.task.task_instance_id,
                node_id=chosen.node_id,
                expected_runtime=expected_rt,
                score=best_score if not explore else 0.0,
            ))
            return True

        # ------------------------------------------------------------------
        # Place gang groups atomically (all-or-nothing).
        # ------------------------------------------------------------------
        for gang_key, gang_entries in gang_groups.items():
            # Try to place all members; if any fails, hold all.
            tentative_places: List[PlaceAction] = []
            all_ok = True
            saved_actions_len = len(actions.place)

            for entry in gang_entries:
                if not _try_place(entry):
                    all_ok = False
                    break

            if not all_ok:
                # Roll back any placements from this gang group.
                rollback = actions.place[saved_actions_len:]
                actions.place = actions.place[:saved_actions_len]
                for pa in rollback:
                    # Unregister the capacity reservation.
                    for n in state.cluster.nodes:
                        if n.node_id == pa.node_id:
                            n.unregister_task(pa.task_instance_id)
                            break
                for entry in gang_entries:
                    actions.hold.append(HoldAction(
                        task_instance_id=entry.task.task_instance_id,
                        reason="gang_not_schedulable",
                    ))

        # ------------------------------------------------------------------
        # Place non-gang tasks.
        # ------------------------------------------------------------------
        for entry in non_gang:
            if not _try_place(entry):
                # Try preemption if available.
                preempted = False
                if self._preemption:
                    victims = self._preemption.find_victims(entry, state)
                    if victims:
                        actions.preempt.extend(victims)
                        # Don't place yet — engine will re-queue next tick.
                        preempted = True

                if not preempted:
                    actions.hold.append(HoldAction(
                        task_instance_id=entry.task.task_instance_id,
                        reason="no_feasible_node",
                    ))

        # ------------------------------------------------------------------
        # Defrag pass (reactive for HOLD'd tasks).
        # ------------------------------------------------------------------
        if self._defrag and actions.hold:
            held_entries = [
                e for e in ordered_entries
                if any(h.task_instance_id == e.task.task_instance_id
                       for h in actions.hold)
            ]
            migrations = self._defrag.reactive_pass(held_entries, state)
            for mig in migrations:
                print(f"[DEFRAG] reactive: migrate '{mig.task_instance_id}' "
                      f"{mig.source_node} -> {mig.dest_node}")

        # Proactive defrag.
        if self._defrag:
            proactive = self._defrag.proactive_pass(state)
            for mig in proactive:
                print(f"[DEFRAG] proactive: migrate '{mig.task_instance_id}' "
                      f"{mig.source_node} -> {mig.dest_node}")

        return actions


def build_policy(name: Optional[str] = None, *,
                 runner: Optional["WorkflowSchedulerRunner"] = None,
                 preemption_planner: Optional["PreemptionPlanner"] = None,
                 defrag_planner: Optional["DefragPlanner"] = None) -> Policy:
    """Construct the policy named by ``name`` (or by ``$TS_POLICY``).

    Phase 0 ships ``legacy``; Phase 3+ adds ``adaptive``.
    """
    selected = (name or os.environ.get(POLICY_ENV_VAR) or DEFAULT_POLICY).lower()
    if selected in ("legacy", "legacy_eight_factor", "eight_factor"):
        if runner is None:
            raise ValueError("LegacyEightFactorPolicy requires runner=")
        return LegacyEightFactorPolicy(runner=runner)
    if selected in ("adaptive", "adaptive_ect", "ect"):
        # Default-construct the planners when none were supplied so the
        # full algorithm (preemption + defrag) runs out of the box.
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
    raise ValueError(
        f"Unknown policy '{selected}'. Available: 'legacy', 'adaptive'.")
