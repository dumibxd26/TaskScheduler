import time
from typing import Optional, List
from models.enums import NodeType
from models.profile import TaskProfile, NodeMetrics, Observation, NodeTypeMetrics


class ProfileStore:
    def __init__(self):
        self.profiles: dict[str, TaskProfile] = {}

    def get_profile(self, task_template_id: str) -> Optional[TaskProfile]:
        return self.profiles.get(task_template_id)

    def get_preferred_order(self, task_template_id: str) -> List[NodeType]:
        profile = self.get_profile(task_template_id)
        if profile and profile.preferred_node_order:
            return profile.preferred_node_order
        return []

    def get_preferred_nodes(self, task_template_id: str) -> List[str]:
        """Returns ranked list of individual node IDs (best first)."""
        profile = self.get_profile(task_template_id)
        if profile and profile.preferred_node_ids:
            return profile.preferred_node_ids
        return []

    def get_completion_level(self, task_template_id: str) -> float:
        """Returns how well-explored this task's scheduling options are (0.0–1.0)."""
        profile = self.get_profile(task_template_id)
        if profile:
            return profile.exploration_level
        return 0.0

    def get_node_median_runtime(self, task_template_id: str, node_id: str) -> Optional[float]:
        """Returns the median runtime for a task on a specific node, or None if unknown."""
        profile = self.get_profile(task_template_id)
        if profile and node_id in profile.metrics_by_node:
            m = profile.metrics_by_node[node_id]
            if m.count > 0:
                return m.median_runtime
        return None

    def get_expected_runtime(self, task_template_id: str, node_id: str) -> Optional[float]:
        """Returns median_runtime + median_startup for a (task, node), or None."""
        profile = self.get_profile(task_template_id)
        if profile and node_id in profile.metrics_by_node:
            m = profile.metrics_by_node[node_id]
            if m.count > 0:
                return m.total_cost
        return None

    def get_failure_rate(self, task_template_id: str, node_id: str) -> float:
        profile = self.get_profile(task_template_id)
        if profile:
            return profile.get_failure_rate(node_id)
        return 0.0

    def record_failure(self, task_template_id: str, node_id: str):
        if task_template_id not in self.profiles:
            self.profiles[task_template_id] = TaskProfile(task_template_id=task_template_id)
        self.profiles[task_template_id].record_failure(node_id)

    def record_observation(self, task_template_id: str, node_id: str,
                           node_type: NodeType, actual_runtime: float,
                           actual_startup: float, node_cpu_at_start: float = 0.0,
                           node_memory_at_start: float = 0.0):
        """Records a runtime observation for a (task, node) pair and recomputes rankings."""
        if task_template_id not in self.profiles:
            self.profiles[task_template_id] = TaskProfile(task_template_id=task_template_id)

        profile = self.profiles[task_template_id]

        # Register the node_id → NodeType mapping
        profile._node_type_map[node_id] = node_type

        # Ensure per-node metrics exist
        if node_id not in profile.metrics_by_node:
            profile.metrics_by_node[node_id] = NodeMetrics()

        # Add the observation (rolling window, median computed on demand)
        profile.metrics_by_node[node_id].add_observation(Observation(
            runtime=actual_runtime,
            startup=actual_startup,
            node_cpu_at_start=node_cpu_at_start,
            node_memory_at_start=node_memory_at_start,
            timestamp=time.time(),
        ))

        # Recompute type-level aggregates and rankings
        profile.update_preferences()