"""
DAG metrics — upward rank and critical-path estimates.

Computed once on workflow admission, cached on TaskInstance.upward_rank and
WorkflowInstance.upward_rank_max. Used by AdaptivePolicy for inner-loop task
ordering (higher upward_rank = more critical = schedule first).

upward_rank(v) = w_v + max_{c ∈ children(v)} (c_data + upward_rank(c))

where w_v is the mean predicted runtime across compatible nodes (or template
default if cold-start) and c_data is the predicted transfer cost averaged
across node pairs.

See ProblemSpecification.md §6 and ImplementationArchitecture.md Part V.3.
"""

from __future__ import annotations

from typing import Dict

from models.enums import DependencyType
from models.workload import WorkflowInstance, WorkflowTemplate
from models.profile_store import ProfileStore


# Default per-task weight when no profile data exists.
_DEFAULT_WEIGHT = 10.0
# Default edge cost (transfer) when no data size info is available.
_DEFAULT_EDGE_COST = 0.5


def _task_weight(task_template_id: str, template: WorkflowTemplate,
                 store: ProfileStore) -> float:
    """Mean predicted runtime across all nodes that have observations, or default."""
    profile = store.get_profile(task_template_id)
    if profile and profile.metrics_by_node:
        runtimes = [m.total_cost for m in profile.metrics_by_node.values()
                    if m.count > 0]
        if runtimes:
            return sum(runtimes) / len(runtimes)
    return _DEFAULT_WEIGHT


def _edge_cost(edge, template: WorkflowTemplate) -> float:
    """Predicted transfer time for a DATA edge (simplified average)."""
    if edge.dependency_type != DependencyType.DATA:
        return 0.0
    # Use expected_bytes_by_field if declared; otherwise a flat default.
    if edge.expected_bytes_by_field:
        total_bytes = sum(edge.expected_bytes_by_field.values())
        # Assume ~100 MB/s average bandwidth as a rough central estimate.
        return total_bytes / (100.0 * 1024 * 1024)
    parent_tmpl = template.tasks.get(edge.parent_task_id)
    if parent_tmpl and parent_tmpl.expected_output_bytes > 0:
        return parent_tmpl.expected_output_bytes / (100.0 * 1024 * 1024)
    return _DEFAULT_EDGE_COST


def compute_upward_ranks(wf: WorkflowInstance, tmpl: WorkflowTemplate,
                         store: ProfileStore) -> Dict[str, float]:
    """
    Bottom-up DFS computing upward_rank for every task in the template.

    Returns dict: task_template_id -> upward_rank.

    Side-effect: stamps ``TaskInstance.upward_rank`` on every task in ``wf``
    and sets ``wf.upward_rank_max``.
    """
    # Build adjacency: parent -> list of (child_id, edge)
    children_of: Dict[str, list] = {tid: [] for tid in tmpl.tasks}
    for edge in tmpl.edges:
        if edge.parent_task_id in children_of:
            children_of[edge.parent_task_id].append(
                (edge.child_task_id, edge))

    cache: Dict[str, float] = {}

    def _rank(tid: str) -> float:
        if tid in cache:
            return cache[tid]
        w = _task_weight(tid, tmpl, store)
        child_ranks = []
        for child_id, edge in children_of.get(tid, []):
            c = _edge_cost(edge, tmpl)
            child_ranks.append(c + _rank(child_id))
        rank = w + (max(child_ranks) if child_ranks else 0.0)
        cache[tid] = rank
        return rank

    # Compute for every task.
    for tid in tmpl.tasks:
        _rank(tid)

    # Stamp onto instances.
    max_rank = 0.0
    for tid, rank in cache.items():
        inst = wf.task_instances.get(tid)
        if inst:
            inst.upward_rank = rank
        if rank > max_rank:
            max_rank = rank
    wf.upward_rank_max = max_rank

    return cache
