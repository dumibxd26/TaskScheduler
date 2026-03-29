from dataclasses import dataclass, field
from typing import Dict, List
from models.enums import NodeType

@dataclass
class NodeMetrics:
    """Metrics for a task running on a specific node type."""
    avg_runtime: float = 0.0
    avg_startup: float = 0.0
    observations: int = 0  # How many times we've measured this

    @property
    def total_cost(self) -> float:
        """The baseline score used to rank nodes."""
        return self.avg_runtime + self.avg_startup

@dataclass
class TaskProfile:
    """The complete learned profile for a specific task template."""
    task_template_id: str
    # Maps NodeType to its specific metrics
    metrics_by_node_type: Dict[NodeType, NodeMetrics] = field(default_factory=dict)
    # The sorted list of best nodes (index 0 is best)
    preferred_node_order: List[NodeType] = field(default_factory=list)

    def update_preferences(self):
        """Sorts the node types based on the lowest total cost (runtime + startup)."""
        # Sort node types by their total cost, ascending
        sorted_types = sorted(
            self.metrics_by_node_type.keys(),
            key=lambda nt: self.metrics_by_node_type[nt].total_cost
        )
        self.preferred_node_order = sorted_types