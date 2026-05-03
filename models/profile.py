from dataclasses import dataclass, field
from typing import Dict, List, Optional
from statistics import median, pstdev
import math
from models.enums import NodeType

# How many recent observations to keep per (task, node) pair for median calculation
OBSERVATION_WINDOW = 20

# EMA smoothing factor for runtime (0 < alpha < 1; higher = react faster).
# Used to detect drift from the median (ProblemSpec §11.5).
EMA_ALPHA = 0.3

# Default minimum observations before a (task, node) bucket is considered
# "trusted" by the legacy 3-phase coverage logic and the new UCB rule.
MIN_OBSERVATIONS_PER_NODE = 3


@dataclass
class Observation:
    """A single recorded execution of a task on a specific node."""
    runtime: float
    startup: float
    node_cpu_at_start: float = 0.0          # CPU usage ratio [0-1] when task was placed
    node_memory_at_start: float = 0.0       # Memory usage ratio [0-1] when task was placed
    timestamp: float = 0.0

    # ---- Phase 0 additions: extra context recorded on every completion ----
    # Bytes read/written by the task (Channel B / local disk). 0 when unknown.
    io_bytes_read: int = 0
    io_bytes_written: int = 0
    # Per-output-field byte counts; populated by Observer when the task emits
    # __TS_OUTPUT__ metadata containing sizes. Empty dict when unknown.
    output_bytes_by_field: Dict[str, int] = field(default_factory=dict)
    # Node CPU temperature (°C) when the task started / finished. None means
    # "thermal collector unavailable on this node".
    temperature_at_start: Optional[float] = None
    temperature_at_end: Optional[float] = None
    # Wall-clock seconds spent fetching remote inputs (initContainer, Channel B).
    # 0.0 when no transfer was needed (Channel C / no DATA parent on other node).
    transfer_seconds: float = 0.0


@dataclass
class NodeMetrics:
    """Rolling-window metrics for a task running on a SPECIFIC node (by node_id)."""
    observations: List[Observation] = field(default_factory=list)

    # ---- Phase 0 additions: EMA tracker for drift detection ----
    # Updated incrementally on every add_observation; persists separately
    # from the rolling-window median so we can compare the two.
    ema_runtime: Optional[float] = None

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
    def stddev_runtime(self) -> float:
        """Population std-dev of runtime over the rolling window.
        Returns 0.0 with fewer than 2 samples (UCB callers must handle that)."""
        if len(self.observations) < 2:
            return 0.0
        return pstdev(o.runtime for o in self.observations)

    @property
    def total_cost(self) -> float:
        return self.median_runtime + self.median_startup

    def ucb_score(self, beta: float = 1.0) -> float:
        """Lower-confidence-bound expected cost: median - beta * stddev / sqrt(n).
        Lower = better candidate (the same direction as total_cost).
        Falls back to total_cost when n < 2 (no variance estimate yet)."""
        n = self.count
        if n < 2:
            return self.total_cost
        return self.total_cost - beta * (self.stddev_runtime / math.sqrt(n))

    def is_drifting(self, drift_threshold: float = 0.25) -> bool:
        """True when the EMA has diverged from the rolling median by more
        than `drift_threshold` (relative). Triggers forced re-exploration
        for this (task, node) pair (ProblemSpec §11.5)."""
        if self.ema_runtime is None or self.count < 3:
            return False
        m = self.median_runtime
        if m <= 0:
            return False
        return abs(self.ema_runtime - m) / m > drift_threshold

    def add_observation(self, obs: Observation):
        self.observations.append(obs)
        # Keep only the most recent observations
        if len(self.observations) > OBSERVATION_WINDOW:
            self.observations = self.observations[-OBSERVATION_WINDOW:]
        # Update the EMA tracker incrementally.
        if self.ema_runtime is None:
            self.ema_runtime = obs.runtime
        else:
            self.ema_runtime = (
                EMA_ALPHA * obs.runtime + (1.0 - EMA_ALPHA) * self.ema_runtime
            )


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

    # Failure tracking per node: node_id -> count of failures.
    # NOTE: this counter decays exponentially via decay_failures() so a node
    # that failed long ago is gradually trusted again (ProblemSpec §11.4).
    failures_by_node: Dict[str, float] = field(default_factory=dict)
    # Wall-clock timestamp of the last decay pass; used by decay_failures.
    failures_last_decay_ts: float = 0.0

    @property
    def exploration_level(self) -> float:
        """
        0.0 - 1.0: how well-explored is this task's placement space?
        Defined as the average per-node observation count divided by the
        target depth (MIN_OBSERVATIONS_PER_NODE), capped at 1.0. The previous
        formula collapsed to a constant whenever any node had been seen at
        all -- this one grows monotonically with sample depth.
        """
        if not self.metrics_by_node:
            return 0.0
        counts = [m.count for m in self.metrics_by_node.values()]
        if not counts:
            return 0.0
        avg = sum(counts) / len(counts)
        return min(1.0, avg / float(MIN_OBSERVATIONS_PER_NODE))

    def record_failure(self, node_id: str):
        """Increment failure count for a node (kept as float for decay)."""
        self.failures_by_node[node_id] = self.failures_by_node.get(node_id, 0.0) + 1.0

    def decay_failures(self, now_ts: float, half_life_hours: float = 14.0):
        """Exponentially decay accumulated failures so old failures fade.
        Idempotent w.r.t. the elapsed time since the last decay pass.
        Half-life default of 14 hours follows ProblemSpec §11.4 (β≈0.95/h)."""
        if not self.failures_by_node:
            self.failures_last_decay_ts = now_ts
            return
        if self.failures_last_decay_ts <= 0.0:
            self.failures_last_decay_ts = now_ts
            return
        elapsed_h = max(0.0, (now_ts - self.failures_last_decay_ts) / 3600.0)
        if elapsed_h <= 0.0:
            return
        factor = 0.5 ** (elapsed_h / max(half_life_hours, 1e-6))
        for nid in list(self.failures_by_node.keys()):
            decayed = self.failures_by_node[nid] * factor
            if decayed < 1e-3:
                del self.failures_by_node[nid]
            else:
                self.failures_by_node[nid] = decayed
        self.failures_last_decay_ts = now_ts

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