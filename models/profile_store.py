from typing import Optional, List
from models.enums import NodeType
from models.profile import TaskProfile, NodeMetrics

class ProfileStore:
    def __init__(self):
        # Maps task_template_id -> TaskProfile
        self.profiles: dict[str, TaskProfile] = {}

    def get_profile(self, task_template_id: str) -> Optional[TaskProfile]:
        return self.profiles.get(task_template_id)

    def get_preferred_order(self, task_template_id: str) -> List[NodeType]:
        profile = self.get_profile(task_template_id)
        if profile and profile.preferred_node_order:
            return profile.preferred_node_order
        return []

    def record_observation(self, task_template_id: str, node_type: NodeType, actual_runtime: float, actual_startup: float):
        """Updates the EWMA metrics and recalculates the best node list."""
        if task_template_id not in self.profiles:
            self.profiles[task_template_id] = TaskProfile(task_template_id=task_template_id)
        
        profile = self.profiles[task_template_id]
        
        if node_type not in profile.metrics_by_node_type:
            profile.metrics_by_node_type[node_type] = NodeMetrics(avg_runtime=actual_runtime, avg_startup=actual_startup, observations=1)
        else:
            # Apply EWMA (Exponential Weighted Moving Average) learning
            metrics = profile.metrics_by_node_type[node_type]
            alpha = 0.3 # Learning rate
            metrics.avg_runtime = (metrics.avg_runtime * (1 - alpha)) + (actual_runtime * alpha)
            metrics.avg_startup = (metrics.avg_startup * (1 - alpha)) + (actual_startup * alpha)
            metrics.observations += 1

        # Recalculate the best node order now that we have new data
        profile.update_preferences()