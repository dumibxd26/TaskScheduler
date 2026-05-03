from dataclasses import dataclass, field
from typing import List, Dict, Optional
from models.enums import (
    DependencyType, TaskClass, NodeType, 
    WorkflowClass, PriorityClass, TaskState, WorkflowState
)

@dataclass
class DependencyEdge:
    parent_task_id: str
    child_task_id: str
    dependency_type: DependencyType  # DATA or EXECUTION
    
    # The exact field names written by the parent and required by the child
    data_field_names: List[str] = field(default_factory=list)

    # Optional per-field expected byte sizes. Used by the ECT calculator to
    # estimate transfer cost when the producer's actual output size is not yet
    # known (e.g. predicting a placement before the parent has even run).
    # Keys must be a subset of data_field_names; missing keys fall back to
    # TaskTemplate.expected_output_bytes on the producer.
    expected_bytes_by_field: Dict[str, int] = field(default_factory=dict)

@dataclass
class TaskTemplate:
    """The static blueprint of a task."""
    task_template_id: str
    name: str
    task_class: TaskClass
    cpu_request: float
    memory_request: float
    image_name: str
    compatible_node_types: List[NodeType]

    # Core parallelism requirements
    # min_cores: minimum cores needed to run at all
    # max_cores: cores beyond which there's no benefit (None = "the more the better")
    min_cores: int = 1
    max_cores: Optional[int] = 1  # Default: single-threaded
    
    # The Execution Definition
    # e.g., command=["python3"], args=["/app/process.py"]
    command: List[str] = field(default_factory=list) 
    args: List[str] = field(default_factory=list)

    # ---- Preemption support (ProblemSpecification.md §10) ----
    # If True, the task supports a checkpoint/restore protocol; otherwise
    # preemption is kill+restart. Default False = behave like today.
    checkpointable: bool = False
    # Suggested wall-clock interval between checkpoints (seconds).
    checkpoint_interval_s: float = 30.0

    # ---- Gang scheduling (ProblemSpecification.md §5.9) ----
    # Tasks sharing the same gang_group_id within one workflow must be
    # placed atomically (all-or-nothing) per H8. None = independent task.
    gang_group_id: Optional[str] = None

    # ---- Output size hint (ProblemSpecification.md §4.3) ----
    # Coarse expected output size in bytes. Feeds the transfer term of ECT
    # before the task has produced real output. 0 = unknown/no output.
    expected_output_bytes: int = 0

@dataclass
class WorkflowTemplate:
    """The static blueprint of a DAG/Workflow."""
    workflow_template_id: str
    name: str
    workflow_class: WorkflowClass
    default_priority: PriorityClass
    default_preemptable: bool
    tasks: Dict[str, TaskTemplate]
    edges: List[DependencyEdge] = field(default_factory=list)

@dataclass
class TaskInstance:
    """A live, running instance of a TaskTemplate."""
    task_instance_id: str
    workflow_instance_id: str
    task_template_id: str
    state: TaskState = TaskState.WAITING
    assigned_node_id: Optional[str] = None
    start_time: Optional[float] = None
    finish_time: Optional[float] = None

    # Wall-clock time of the most recent successful checkpoint (None = never).
    # Set by the engine when a checkpoint completes. Used by the preemption
    # planner to pick checkpoint vs kill+restart.
    last_checkpoint_at: Optional[float] = None

    # How many times this task has been preempted in its lifetime. The
    # preemption planner uses this to enforce the per-task cap (M = 2).
    preemption_count: int = 0

    # HEFT-style upward rank (longer remaining critical path => higher).
    # Computed once on workflow admission by services/dag_metrics.py.
    upward_rank: float = 0.0

@dataclass
class WorkflowInstance:
    """A live, active instance of a WorkflowTemplate."""
    workflow_instance_id: str
    workflow_template_id: str
    workflow_class: WorkflowClass
    priority: PriorityClass
    preemptable: bool
    task_instances: Dict[str, TaskInstance]
    state: WorkflowState = WorkflowState.QUEUED
    arrival_time: Optional[float] = None
    finish_time: Optional[float] = None

    # Cumulative "virtual runtime" used by the outer-loop fairness order
    # (ProblemSpecification.md §6, Q8). Engine increments per tick by
    # (elapsed_s × normalised_cpu_share). 0.0 means "never advanced".
    vruntime: float = 0.0

    # Cached maximum upward_rank across this workflow's tasks. Filled by
    # services/dag_metrics.compute_upward_ranks at admission.
    upward_rank_max: float = 0.0