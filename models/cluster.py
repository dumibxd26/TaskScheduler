from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional
from models.enums import NodeType
import time as _time


@dataclass
class RunningTask:
    """Tracks a task currently executing on a node."""
    task_template_id: str
    task_instance_id: str
    start_time: float                     # wall-clock when the pod started
    expected_runtime: Optional[float]     # median runtime from profile (None = unknown)

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
                      expected_runtime: Optional[float] = None):
        """Call when a task is placed on this node."""
        self.active_tasks[task_instance_id] = RunningTask(
            task_template_id=task_template_id,
            task_instance_id=task_instance_id,
            start_time=_time.time(),
            expected_runtime=expected_runtime,
        )
        self.running_tasks = len(self.active_tasks)

    def unregister_task(self, task_instance_id: str):
        """Call when a task finishes on this node."""
        self.active_tasks.pop(task_instance_id, None)
        self.running_tasks = len(self.active_tasks)

@dataclass
class ClusterScenario:
    scenario_id: str
    name: str
    description: str
    nodes: List[Node] = field(default_factory=list)