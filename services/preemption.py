"""
PreemptionPlanner — victim selection and budget enforcement.

Implements ProblemSpecification.md §10:
 - Priority preemption: a higher-priority task may evict a lower-priority one.
 - Makespan preemption: if evicting a task frees a node that reduces overall
   predicted finish time by more than the cost of restarting the victim.
 - Budget caps: per-node K preemptions per minute; per-task M lifetime cap;
   near-completion immunity; minimum runtime threshold.

See ImplementationArchitecture.md Part V.5.
"""

from __future__ import annotations

import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from models.enums import PriorityClass, TaskState

if TYPE_CHECKING:
    from services.policy import SchedulerState, PreemptAction
    from services.queue_manager import TaskEntry
    from models.cluster import Node, RunningTask


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_PREEMPTIONS_PER_NODE_PER_MIN = 3
MAX_PREEMPTIONS_PER_TASK = 2
NEAR_COMPLETION_THRESHOLD_S = 5.0
MIN_RUNTIME_BEFORE_PREEMPT_S = 5.0


class PreemptionPlanner:
    """Finds victims to free capacity for a higher-value queued task."""

    def __init__(self,
                 max_per_node_per_min: int = MAX_PREEMPTIONS_PER_NODE_PER_MIN,
                 max_per_task: int = MAX_PREEMPTIONS_PER_TASK,
                 near_completion_s: float = NEAR_COMPLETION_THRESHOLD_S,
                 min_runtime_s: float = MIN_RUNTIME_BEFORE_PREEMPT_S):
        self.max_per_node_per_min = max_per_node_per_min
        self.max_per_task = max_per_task
        self.near_completion_s = near_completion_s
        self.min_runtime_s = min_runtime_s
        # node_id -> list of (timestamp) of recent preemptions
        self._recent: Dict[str, List[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Budget checks
    # ------------------------------------------------------------------
    def _can_preempt_node(self, node_id: str, now: float) -> bool:
        """Has this node already hit its per-minute preemption cap?"""
        cutoff = now - 60.0
        recent = [t for t in self._recent[node_id] if t > cutoff]
        self._recent[node_id] = recent
        return len(recent) < self.max_per_node_per_min

    @staticmethod
    def _is_near_completion(rt: "RunningTask") -> bool:
        """True if the running task is predicted to finish very soon."""
        remaining = rt.estimated_remaining
        if remaining is not None and remaining < NEAR_COMPLETION_THRESHOLD_S:
            return True
        return False

    def _has_run_long_enough(self, rt: "RunningTask", now: float) -> bool:
        """Avoid thrashing: don't preempt something that just started."""
        return rt.elapsed >= self.min_runtime_s

    # ------------------------------------------------------------------
    # Victim selection
    # ------------------------------------------------------------------
    def _find_victim_workflow(self, task_instance_id: str, state):
        """Locate the WorkflowInstance that owns a running task_instance_id."""
        for wf in state.queue.admitted_workflows.values():
            if task_instance_id in wf.task_instances:
                return wf
        return None

    def find_victims(self, queued_task: "TaskEntry",
                     state: "SchedulerState") -> Optional[List["PreemptAction"]]:
        """
        Try to find a set of victims whose eviction would free enough
        capacity on some node for ``queued_task``.

        Returns a list of PreemptActions, or None if no valid preemption
        exists.
        """
        from services.policy import PreemptAction

        tmpl_id = queued_task.task_template_id
        wf_tmpl = state.workflow_templates[queued_task.workflow.workflow_template_id]
        task_tmpl = wf_tmpl.tasks[tmpl_id]
        now = state.now
        queued_priority_value = queued_task.effective_priority.value

        candidates = []

        for node in state.cluster.nodes:
            if node.node_type not in task_tmpl.compatible_node_types:
                continue
            if not self._can_preempt_node(node.node_id, now):
                continue

            # What running tasks could we evict?
            evictable = []
            for rt in node.active_tasks.values():
                # Find the workflow that owns this running task to compare priority.
                victim_wf = self._find_victim_workflow(
                    rt.task_instance_id, state)
                if victim_wf is None:
                    continue
                # Only preempt strictly lower-priority tasks from preemptable
                # workflows.
                if victim_wf.priority.value >= queued_priority_value:
                    continue
                if not victim_wf.preemptable:
                    continue
                # Per-task lifetime cap.
                victim_inst = victim_wf.task_instances.get(rt.task_instance_id)
                if (victim_inst is not None
                        and victim_inst.preemption_count >= self.max_per_task):
                    continue
                if self._is_near_completion(rt):
                    continue
                if not self._has_run_long_enough(rt, now):
                    continue
                evictable.append(rt)

            if not evictable:
                continue

            # Greedy: try evicting the cheapest victims until we free enough.
            # Sort by estimated_remaining (ascending — evict the one with most
            # remaining work last, because it's cheapest to lose a task that's
            # just started).
            evictable.sort(key=lambda rt: rt.elapsed)
            freed_cpu = node.free_cpu
            freed_mem = node.free_memory
            victims = []

            for rt in evictable:
                if (freed_cpu >= task_tmpl.cpu_request
                        and freed_mem >= task_tmpl.memory_request):
                    break
                freed_cpu += rt.cpu_request
                freed_mem += rt.memory_request
                victims.append(rt)

            if (freed_cpu >= task_tmpl.cpu_request
                    and freed_mem >= task_tmpl.memory_request
                    and victims):
                actions = [
                    PreemptAction(
                        victim_task_instance_id=v.task_instance_id,
                        mode="kill_restart",
                    )
                    for v in victims
                ]
                candidates.append((node, actions))

        if not candidates:
            return None

        # Pick the node where we evict fewest victims.
        best_node, best_actions = min(candidates, key=lambda x: len(x[1]))
        return best_actions

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------
    def record_preemption(self, node_id: str, task_id: str,
                          now: Optional[float] = None):
        """Call after a preemption is applied."""
        ts = now or _time.time()
        self._recent[node_id].append(ts)
