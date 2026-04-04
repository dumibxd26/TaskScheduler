from dataclasses import dataclass, field
from typing import Dict, List, Optional
from statistics import median
from models.enums import NodeType

# How many recent observations to keep per (task, node) pair for median calculation
OBSERVATION_WINDOW = 20


@dataclass
class Observation:
    """A single recorded execution of a task on a specific node."""
    runtime: float
    startup: float
    node_cpu_at_start: float = 0.0          # CPU usage ratio [0-1] when task was placed
    node_memory_at_start: float = 0.0       # Memory usage ratio [0-1] when task was placed
    timestamp: float = 0.0


@dataclass
class NodeMetrics:
    """Rolling-window metrics for a task running on a SPECIFIC node (by node_id)."""
    observations: List[Observation] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.observations)

    @property
    def median_runtime(self) -> float:
        if not self.observations:
            return 0.0
        return median(o.runtime for o in self.observations)

    @property
    def median_startup(self) -> float:
        if not self.observations:
            return 0.0
        return median(o.startup for o in self.observations)

    @property
    def total_cost(self) -> float:
        return self.median_runtime + self.median_startup

    def add_observation(self, obs: Observation):
        self.observations.append(obs)
        # Keep only the most recent observations
        if len(self.observations) > OBSERVATION_WINDOW:
            self.observations = self.observations[-OBSERVATION_WINDOW:]


@dataclass
class NodeTypeMetrics:
    """Aggregate metrics for a task across all nodes of a given NodeType."""
    total_cost: float = 0.0
    total_observations: int = 0


@dataclass
class TaskProfile:
    """The complete learned profile for a specific task template."""
    task_template_id: str

    # Level 2: per individual node (node_id -> NodeMetrics)
    metrics_by_node: Dict[str, NodeMetrics] = field(default_factory=dict)

    # Level 1: aggregate per node type, recomputed from metrics_by_node
    metrics_by_node_type: Dict[NodeType, NodeTypeMetrics] = field(default_factory=dict)

    # Ranked list of best node types (index 0 = best)
    preferred_node_order: List[NodeType] = field(default_factory=list)

    # Ranked list of best individual nodes (index 0 = best)
    preferred_node_ids: List[str] = field(default_factory=list)

    # Mapping from node_id to its NodeType (filled when observations are recorded)
    _node_type_map: Dict[str, NodeType] = field(default_factory=dict)

    # Failure tracking per node: node_id -> count of failures
    failures_by_node: Dict[str, int] = field(default_factory=dict)

    @property
    def exploration_level(self) -> float:
        """
        0.0 - 1.0: how well-explored are this task's placement options?
        Based on how many distinct node types have been observed and depth of data.
        """
        if not self.metrics_by_node_type:
            return 0.0
        confident_types = sum(
            1 for m in self.metrics_by_node_type.values()
            if m.total_observations >= 3
        )
        explored_types = len(self.metrics_by_node_type)
        depth = confident_types / max(explored_types, 1)
        return min(1.0, (explored_types / max(explored_types, 1)) * 0.6 + depth * 0.4)

    def record_failure(self, node_id: str):
        """Increment failure count for a node."""
        self.failures_by_node[node_id] = self.failures_by_node.get(node_id, 0) + 1

    def get_failure_rate(self, node_id: str) -> float:
        """Failure rate = failures / (failures + successful observations)."""
        failures = self.failures_by_node.get(node_id, 0)
        successes = self.metrics_by_node[node_id].count if node_id in self.metrics_by_node else 0
        total = failures + successes
        if total == 0:
            return 0.0
        return failures / total

    def update_preferences(self):
        """Recompute aggregated type metrics and sorted rankings."""
        # Rebuild node type aggregates from per-node data
        type_totals: Dict[NodeType, List[float]] = {}
        type_counts: Dict[NodeType, int] = {}

        for node_id, node_metrics in self.metrics_by_node.items():
            nt = self._node_type_map.get(node_id)
            if nt is None or node_metrics.count == 0:
                continue
            type_totals.setdefault(nt, []).append(node_metrics.total_cost)
            type_counts[nt] = type_counts.get(nt, 0) + node_metrics.count

        self.metrics_by_node_type.clear()
        for nt, costs in type_totals.items():
            self.metrics_by_node_type[nt] = NodeTypeMetrics(
                total_cost=median(costs),
                total_observations=type_counts.get(nt, 0),
            )

        # Level 1: rank node types
        self.preferred_node_order = sorted(
            self.metrics_by_node_type.keys(),
            key=lambda nt: self.metrics_by_node_type[nt].total_cost,
        )

        # Level 2: rank individual nodes
        self.preferred_node_ids = sorted(
            (nid for nid, m in self.metrics_by_node.items() if m.count > 0),
            key=lambda nid: self.metrics_by_node[nid].total_cost,
        )