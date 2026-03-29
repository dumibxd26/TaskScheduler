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
    
    # The Execution Definition
    # e.g., command=["python3"], args=["/app/process.py"]
    command: List[str] = field(default_factory=list) 
    args: List[str] = field(default_factory=list)

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