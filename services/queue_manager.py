import heapq
import time
from dataclasses import dataclass
from typing import List, Optional
from models.enums import PriorityClass, WorkflowState, TaskState
from models.workload import WorkflowInstance, TaskInstance

# How many seconds before REAL_TIME_MEDIUM ages into REAL_TIME_HIGH
AGING_TTL = 60.0


@dataclass
class TaskEntry:
    """Wrapper around a queued task, tracking priority and timing for aging."""
    task: TaskInstance
    workflow: WorkflowInstance
    task_template_id: str
    original_priority: PriorityClass
    effective_priority: PriorityClass
    enqueue_time: float


class QueueManager:
    def __init__(self):
        self.workflow_queue = []                    # min-heap for workflow admission
        self.task_entries: List[TaskEntry] = []     # flat list, re-sorted each tick
        self.admitted_workflows = {}                # active workflows

    # ------------------------------------------------------------------
    # Workflow admission
    # ------------------------------------------------------------------
    def _workflow_sort_key(self, priority: PriorityClass, arrival_time: float):
        """Higher priority value = more urgent → lower sort int for min-heap."""
        return (5 - priority.value, arrival_time)

    def submit_workflow(self, workflow: WorkflowInstance):
        """External API: add a workflow to the admission queue."""
        workflow.arrival_time = time.time()
        workflow.state = WorkflowState.QUEUED

        sort_key = self._workflow_sort_key(workflow.priority, workflow.arrival_time)
        heapq.heappush(self.workflow_queue,
                       (*sort_key, workflow.workflow_instance_id, workflow))
        print(f"[QUEUE] Submitted Workflow: {workflow.workflow_instance_id} "
              f"| Priority: {workflow.priority.name}")

    def admit_next_workflow(self) -> Optional[WorkflowInstance]:
        """Pop the highest-priority workflow from the admission queue."""
        if not self.workflow_queue:
            return None

        _, _, _, workflow = heapq.heappop(self.workflow_queue)
        workflow.state = WorkflowState.ADMITTED
        self.admitted_workflows[workflow.workflow_instance_id] = workflow
        print(f"[ADMISSION] Admitted Workflow: {workflow.workflow_instance_id}")
        return workflow

    # ------------------------------------------------------------------
    # Task queuing
    # ------------------------------------------------------------------
    def enqueue_ready_tasks(self, tasks: List[TaskInstance], workflow: WorkflowInstance):
        """Put DAG-ready tasks into the task queue, inheriting the workflow's priority."""
        now = time.time()
        for task in tasks:
            # Transition WAITING -> READY so the ReadinessResolver won't return
            # this task again next tick and create duplicate queue entries.
            task.state = TaskState.READY
            entry = TaskEntry(
                task=task,
                workflow=workflow,
                task_template_id=task.task_template_id,
                original_priority=workflow.priority,
                effective_priority=workflow.priority,
                enqueue_time=now,
            )
            self.task_entries.append(entry)

    # ------------------------------------------------------------------
    # Priority aging
    # ------------------------------------------------------------------
    def _apply_aging(self):
        """Promote REAL_TIME_MEDIUM tasks that exceeded the aging TTL."""
        now = time.time()
        for entry in self.task_entries:
            if (entry.original_priority == PriorityClass.REAL_TIME_MEDIUM
                    and entry.effective_priority == PriorityClass.REAL_TIME_MEDIUM
                    and now - entry.enqueue_time >= AGING_TTL):
                entry.effective_priority = PriorityClass.REAL_TIME_HIGH
                print(f"[AGING] '{entry.task.task_instance_id}' promoted "
                      f"REAL_TIME_MEDIUM -> REAL_TIME_HIGH")

    # ------------------------------------------------------------------
    # Dispatch helpers (used by the engine dispatch loop)
    # ------------------------------------------------------------------
    def get_sorted_tasks(self) -> List[TaskEntry]:
        """Return tasks ordered by effective priority (highest first), then enqueue time."""
        self._apply_aging()
        return sorted(self.task_entries,
                       key=lambda e: (-e.effective_priority.value, e.enqueue_time))

    def remove_tasks(self, task_ids: set):
        """Remove dispatched tasks from the queue."""
        self.task_entries = [e for e in self.task_entries
                            if e.task.task_instance_id not in task_ids]

    # ------------------------------------------------------------------
    # Legacy single-pop API (kept for k8s_main.py compatibility)
    # ------------------------------------------------------------------
    def get_next_task(self):
        """Pop the single highest-priority task. Legacy API."""
        if not self.task_entries:
            return None, None
        self._apply_aging()
        self.task_entries.sort(key=lambda e: (-e.effective_priority.value, e.enqueue_time))
        entry = self.task_entries.pop(0)
        return entry.task, entry.workflow