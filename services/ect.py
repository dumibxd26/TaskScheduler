"""
ECT (Expected Completion Time) calculator.

Pure functions — no state. This is the heart of ProblemSpecification.md §8.5:

    ECT(τ, n | A) = t_ready(τ | A) + startup(τ, n) + transfer(τ, n | A) + μ_{τ,n,b}

Used by AdaptivePolicy to score every (task, node) candidate and by the
action-set builder to compute J(A).

See ImplementationArchitecture.md Part V.4.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from models.cluster import Node, ClusterScenario
from models.enums import DependencyType
from models.workload import (
    TaskInstance, TaskTemplate, WorkflowInstance, WorkflowTemplate,
)
from models.profile_store import ProfileStore
from services.data_placement import DataPlacement


# ---------------------------------------------------------------------------
# Default fallbacks when ProfileStore has no data for a (task, node) pair.
# ---------------------------------------------------------------------------
DEFAULT_STARTUP_S = 2.0
DEFAULT_RUNTIME_S = 10.0


def _get_expected_runtime(store: ProfileStore, task_template_id: str,
                          node: Node) -> float:
    """Median runtime from the profile store, or a cold-start default.

    When the node has a current temperature reading, prefer the
    thermal-bucketed median so hot/cold contexts are scored separately.
    """
    if node.cpu_temperature is not None:
        # Lazy import to avoid a circular dependency at module import time.
        from services.thermal import ThermalCollector
        bucket = ThermalCollector.thermal_bucket(
            node.cpu_temperature, node.cooling_class)
        bucketed = store.get_bucketed_runtime(
            task_template_id, node.node_id, bucket)
        if bucketed is not None:
            return bucketed
    rt = store.get_expected_runtime(task_template_id, node.node_id)
    if rt is not None:
        return rt
    return DEFAULT_RUNTIME_S


def _get_startup(store: ProfileStore, task_template_id: str,
                 node: Node) -> float:
    """Median startup from the profile store, or a default."""
    profile = store.get_profile(task_template_id)
    if profile and node.node_id in profile.metrics_by_node:
        m = profile.metrics_by_node[node.node_id]
        if m.count > 0:
            return m.median_startup
    return DEFAULT_STARTUP_S


# ---------------------------------------------------------------------------
# Transfer cost
# ---------------------------------------------------------------------------

def transfer_seconds(task: TaskInstance, tmpl: TaskTemplate,
                     node: Node, wf: WorkflowInstance,
                     wf_tmpl: WorkflowTemplate,
                     dp: DataPlacement,
                     bw_matrix: Dict[Tuple[str, str], float],
                     cluster: ClusterScenario) -> float:
    """
    Sum of (bytes / bandwidth) for every DATA-edge parent whose output is
    NOT resident on ``node``. Zero when all parents ran on the same node
    (Channel C) or when there are no DATA edges.

    Falls back to ``parent_instance.assigned_node_id`` when the DataPlacement
    registry has no record (typical in simulation, where the data plane is
    stubbed). This keeps the ECT estimate honest about co-location.
    """
    total = 0.0
    for edge in wf_tmpl.edges:
        if edge.child_task_id != task.task_template_id:
            continue
        if edge.dependency_type != DependencyType.DATA:
            continue

        parent_instance = wf.task_instances.get(edge.parent_task_id)
        if parent_instance is None:
            continue

        for field_name in edge.data_field_names:
            # Where does this field currently live?
            producer_node = dp.get_producer_node(
                wf.workflow_instance_id, edge.parent_task_id, field_name)

            # Fallback: the parent's actual assigned node (set when the
            # engine dispatched it). Zero-cost if same as candidate node.
            if producer_node is None:
                producer_node = parent_instance.assigned_node_id

            if producer_node is None:
                # Parent hasn't run yet; use template hint for bytes, assume
                # worst-case: it will run on a different node.
                size = edge.expected_bytes_by_field.get(
                    field_name, tmpl.expected_output_bytes)
                bw = cluster.default_bandwidth_bytes_per_s
                if size > 0 and bw > 0:
                    total += size / bw
                continue

            if producer_node == node.node_id:
                continue  # Channel C — same node, zero cost

            size = dp.get_output_size(
                wf.workflow_instance_id, edge.parent_task_id, field_name)
            if size <= 0:
                size = edge.expected_bytes_by_field.get(
                    field_name, tmpl.expected_output_bytes)

            bw = cluster.get_bandwidth(producer_node, node.node_id)
            if size > 0 and bw > 0:
                total += size / bw

    return total


# ---------------------------------------------------------------------------
# Single-task ECT
# ---------------------------------------------------------------------------

def task_finish_time(task: TaskInstance, tmpl: TaskTemplate,
                     node: Node, wf: WorkflowInstance,
                     wf_tmpl: WorkflowTemplate,
                     store: ProfileStore, dp: DataPlacement,
                     cluster: ClusterScenario,
                     now: float,
                     occupancy: Optional[Dict[str, float]] = None) -> float:
    """
    Predicted wall-clock time at which ``task`` finishes if placed on ``node``
    now. ``occupancy`` is a per-node "earliest free at" map the planner has
    accumulated (from earlier placements in the same tick).
    """
    occ = occupancy or {}
    t_ready = max(now, occ.get(node.node_id, now))
    startup = _get_startup(store, task.task_template_id, node)
    xfer = transfer_seconds(task, tmpl, node, wf, wf_tmpl, dp, {}, cluster)
    runtime = _get_expected_runtime(store, task.task_template_id, node)
    return t_ready + startup + xfer + runtime


# ---------------------------------------------------------------------------
# Workflow-level ECT (critical-path finish time under a partial plan)
# ---------------------------------------------------------------------------

def workflow_ect(wf: WorkflowInstance, wf_tmpl: WorkflowTemplate,
                 store: ProfileStore,
                 plan: Dict[str, str],
                 cluster: ClusterScenario,
                 dp: DataPlacement,
                 now: float) -> float:
    """
    Critical-path finish time for a workflow given a (partial) plan mapping
    task_template_id → node_id.  Tasks not in ``plan`` use the best predicted
    node (argmin ECT across compatible nodes).
    """
    # Build finish-time map bottom-up through the DAG.
    finish: Dict[str, float] = {}
    nodes_by_id = {n.node_id: n for n in cluster.nodes}

    def _finish(task_id: str) -> float:
        if task_id in finish:
            return finish[task_id]

        task_inst = wf.task_instances.get(task_id)
        if task_inst is None:
            return now

        # Already finished — use actual finish time.
        if task_inst.finish_time is not None:
            finish[task_id] = task_inst.finish_time
            return task_inst.finish_time

        tmpl = wf_tmpl.tasks[task_id]

        # t_ready = max(parent finish times)
        parents = [e.parent_task_id for e in wf_tmpl.edges
                   if e.child_task_id == task_id]
        t_ready = max((_finish(pid) for pid in parents), default=now)

        # Which node?
        nid = plan.get(task_id)
        if nid and nid in nodes_by_id:
            node = nodes_by_id[nid]
        else:
            # Pick the best predicted node.
            best_cost = float("inf")
            node = cluster.nodes[0] if cluster.nodes else None
            for n in cluster.nodes:
                if n.node_type not in tmpl.compatible_node_types:
                    continue
                c = _get_expected_runtime(store, task_id, n)
                if c < best_cost:
                    best_cost = c
                    node = n

        startup = _get_startup(store, task_id, node)
        xfer = transfer_seconds(
            task_inst, tmpl, node, wf, wf_tmpl, dp, {}, cluster)
        runtime = _get_expected_runtime(store, task_id, node)
        ft = t_ready + startup + xfer + runtime
        finish[task_id] = ft
        return ft

    # The workflow ECT is the max of all leaf-task finish times.
    all_children = {e.child_task_id for e in wf_tmpl.edges}
    all_tasks = set(wf_tmpl.tasks.keys())
    leaves = all_tasks - all_children if all_children else all_tasks
    if not leaves:
        leaves = all_tasks
    return max((_finish(tid) for tid in leaves), default=now)
