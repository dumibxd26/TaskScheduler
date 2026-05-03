"""
DefragPlanner — reactive and proactive defragmentation.

**Reactive pass** (every tick): for any HOLD'd task, see if migrating a
  running task from a candidate node to another node would free capacity.

**Proactive pass** (every T_defrag seconds): re-evaluate current placement
  and propose migrations that improve the composite objective J(A) by
  at least η.

See ProblemSpecification.md §10 and ImplementationArchitecture.md Part V.6.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

from models.enums import TaskState

if TYPE_CHECKING:
    from services.policy import SchedulerState, PlaceAction, PreemptAction
    from services.queue_manager import TaskEntry


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
T_DEFRAG_S = 30.0           # seconds between proactive passes
ETA_IMPROVEMENT = 0.05       # minimum ΔJ to accept a proactive migration


@dataclass
class MigrationAction:
    """Move a running task from source_node to dest_node."""
    task_instance_id: str
    source_node: str
    dest_node: str
    reason: str = ""


class DefragPlanner:
    """Defragmentation planner with reactive and proactive passes."""

    def __init__(self, defrag_interval: float = T_DEFRAG_S,
                 eta: float = ETA_IMPROVEMENT):
        self.defrag_interval = defrag_interval
        self.eta = eta
        self._last_proactive_at: float = 0.0

    # ------------------------------------------------------------------
    # Reactive pass
    # ------------------------------------------------------------------
    def reactive_pass(self, held_tasks: List["TaskEntry"],
                      state: "SchedulerState") -> List[MigrationAction]:
        """
        For each HOLD'd task, check if migrating a single running task
        from some node to a less-loaded node could free enough resources.
        Returns a list of proposed migrations.
        """
        migrations: List[MigrationAction] = []
        if not held_tasks:
            return migrations

        for entry in held_tasks:
            tmpl_id = entry.task_template_id
            wf_tmpl = state.workflow_templates[entry.workflow.workflow_template_id]
            task_tmpl = wf_tmpl.tasks[tmpl_id]

            for node in state.cluster.nodes:
                if node.node_type not in task_tmpl.compatible_node_types:
                    continue
                if (node.free_cpu >= task_tmpl.cpu_request
                        and node.free_memory >= task_tmpl.memory_request):
                    continue  # Already feasible — shouldn't be HOLD'd

                # Which running tasks on this node could be moved elsewhere?
                for rt in list(node.active_tasks.values()):
                    # Can we fit this running task on another node?
                    for other in state.cluster.nodes:
                        if other.node_id == node.node_id:
                            continue
                        if (other.free_cpu >= rt.cpu_request
                                and other.free_memory >= rt.memory_request):
                            # Would evicting rt free enough for queued task?
                            after_cpu = node.free_cpu + rt.cpu_request
                            after_mem = node.free_memory + rt.memory_request
                            if (after_cpu >= task_tmpl.cpu_request
                                    and after_mem >= task_tmpl.memory_request):
                                migrations.append(MigrationAction(
                                    task_instance_id=rt.task_instance_id,
                                    source_node=node.node_id,
                                    dest_node=other.node_id,
                                    reason=f"reactive: free {node.node_id} for "
                                           f"{entry.task.task_instance_id}",
                                ))
                                break
                    if migrations:
                        break  # one migration per held task max
                if migrations:
                    break

        return migrations

    # ------------------------------------------------------------------
    # Proactive pass
    # ------------------------------------------------------------------
    def proactive_pass(self, state: "SchedulerState") -> List[MigrationAction]:
        """
        Periodic pass that scans for beneficial migrations even when
        nothing is HOLD'd. Runs at most once every ``defrag_interval`` seconds.
        """
        now = state.now
        if (now - self._last_proactive_at) < self.defrag_interval:
            return []
        self._last_proactive_at = now

        migrations: List[MigrationAction] = []

        # Load-balance pass: find the most loaded node and least loaded node,
        # and propose moving a small task from the former to the latter.
        if len(state.cluster.nodes) < 2:
            return migrations

        nodes_sorted = sorted(
            state.cluster.nodes,
            key=lambda n: n.cpu_usage_ratio,
            reverse=True,
        )
        hottest = nodes_sorted[0]
        coolest = nodes_sorted[-1]

        if hottest.cpu_usage_ratio - coolest.cpu_usage_ratio < self.eta:
            return migrations  # Not enough imbalance to warrant defrag.

        # Pick the smallest running task on the hot node that fits on the cool.
        candidates = sorted(
            hottest.active_tasks.values(),
            key=lambda rt: rt.cpu_request,
        )
        for rt in candidates:
            if (coolest.free_cpu >= rt.cpu_request
                    and coolest.free_memory >= rt.memory_request):
                migrations.append(MigrationAction(
                    task_instance_id=rt.task_instance_id,
                    source_node=hottest.node_id,
                    dest_node=coolest.node_id,
                    reason="proactive: load-balance CPU",
                ))
                break

        return migrations
