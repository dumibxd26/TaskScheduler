from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional, Tuple
from models.enums import NodeType, CoolingClass
import time as _time


@dataclass
class RunningTask:
    """Tracks a task currently executing on a node."""
    task_template_id: str
    task_instance_id: str
    start_time: float                     # wall-clock when the pod started
    expected_runtime: Optional[float]     # median runtime from profile (None = unknown)
    cpu_request: float = 0.0              # reserved CPU (released on unregister)
    memory_request: float = 0.0           # reserved memory (released on unregister)

    @property
    def elapsed(self) -> float:
        return _time.time() - self.start_time

    @property
    def estimated_remaining(self) -> Optional[float]:
        """Seconds until this task is expected to finish. None if we can't predict."""
        if self.expected_runtime is None:
            return None
        remaining = self.expected_runtime - self.elapsed
        return max(remaining, 0.0)


@dataclass
class Node:
    node_id: str
    node_type: NodeType
    total_cpu: float
    total_memory: float
    free_cpu: float
    free_memory: float
    running_tasks: int = 0
    warm_images: Set[str] = field(default_factory=set)

    # Live tracking of what's running on this node right now
    active_tasks: Dict[str, RunningTask] = field(default_factory=dict)

    # ---- Thermal subsystem (ProblemSpecification.md §2.6, §4.1) ----
    # Declared static capability of the node's cooling solution.
    cooling_class: CoolingClass = CoolingClass.STANDARD
    # Vendor-declared throttle temperature in degrees C (defaults track
    # commodity x86 silicon — Intel/AMD client chips throttle ~95–105 C).
    thermal_throttle_temp_c: float = 100.0
    # Most recent observed core temperature; None means "not measurable".
    cpu_temperature: Optional[float] = None

    @property
    def thermal_headroom(self) -> Optional[float]:
        """
        Degrees C remaining before the node is expected to throttle.
        None when no temperature reading is available — caller must fall back
        to cooling_class only (per ProblemSpec §2.6).
        """
        if self.cpu_temperature is None:
            return None
        return self.thermal_throttle_temp_c - self.cpu_temperature

    @property
    def cpu_usage_ratio(self) -> float:
        """0.0 = idle, 1.0 = fully loaded."""
        if self.total_cpu <= 0:
            return 1.0
        return max(0.0, 1.0 - (self.free_cpu / self.total_cpu))

    @property
    def memory_usage_ratio(self) -> float:
        if self.total_memory <= 0:
            return 1.0
        return max(0.0, 1.0 - (self.free_memory / self.total_memory))

    @property
    def estimated_free_in(self) -> Optional[float]:
        """
        Seconds until the SOONEST task on this node finishes.
        Returns 0.0 if no tasks are running, None if we can't predict.
        """
        if not self.active_tasks:
            return 0.0

        soonest = None
        for rt in self.active_tasks.values():
            remaining = rt.estimated_remaining
            if remaining is not None:
                if soonest is None or remaining < soonest:
                    soonest = remaining
        return soonest

    @property
    def estimated_all_free_in(self) -> Optional[float]:
        """
        Seconds until ALL tasks on this node finish.
        Returns 0.0 if no tasks running, None if any task is unpredictable.
        """
        if not self.active_tasks:
            return 0.0

        latest = 0.0
        for rt in self.active_tasks.values():
            remaining = rt.estimated_remaining
            if remaining is None:
                return None
            latest = max(latest, remaining)
        return latest

    def register_task(self, task_template_id: str, task_instance_id: str,
                      expected_runtime: Optional[float] = None,
                      cpu_request: float = 0.0, memory_request: float = 0.0):
        """Call when a task is placed on this node. Reserves capacity."""
        self.active_tasks[task_instance_id] = RunningTask(
            task_template_id=task_template_id,
            task_instance_id=task_instance_id,
            start_time=_time.time(),
            expected_runtime=expected_runtime,
            cpu_request=cpu_request,
            memory_request=memory_request,
        )
        self.free_cpu -= cpu_request
        self.free_memory -= memory_request
        self.running_tasks = len(self.active_tasks)

    def unregister_task(self, task_instance_id: str):
        """Call when a task finishes on this node. Releases capacity."""
        rt = self.active_tasks.pop(task_instance_id, None)
        if rt is not None:
            self.free_cpu = min(self.total_cpu, self.free_cpu + rt.cpu_request)
            self.free_memory = min(self.total_memory, self.free_memory + rt.memory_request)
        self.running_tasks = len(self.active_tasks)

@dataclass
class ClusterScenario:
    scenario_id: str
    name: str
    description: str
    nodes: List[Node] = field(default_factory=list)

    # ---- Bandwidth matrix (ProblemSpecification.md §2.5, §4.8) ----
    # Pair-wise bytes/second estimates between every (producer, consumer) node pair.
    # Populated by the BandwidthCollector (Phase 2). Until then, get_bandwidth()
    # falls back to default_bandwidth_bytes_per_s for any missing pair.
    bandwidth_matrix: Dict[Tuple[str, str], float] = field(default_factory=dict)
    default_bandwidth_bytes_per_s: float = 100.0 * 1024 * 1024  # 100 MB/s

    def get_bandwidth(self, producer_node_id: str, consumer_node_id: str) -> float:
        """
        Bytes/second between two nodes. Same node => +inf (no transfer).
        Missing pair => default_bandwidth_bytes_per_s.
        """
        if producer_node_id == consumer_node_id:
            return float("inf")
        return self.bandwidth_matrix.get(
            (producer_node_id, consumer_node_id),
            self.default_bandwidth_bytes_per_s,
        )