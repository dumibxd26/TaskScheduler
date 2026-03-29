import heapq
import time
from typing import List, Optional
from models.enums import WorkflowClass, PriorityClass, WorkflowState
from models.workload import WorkflowInstance, TaskInstance

class QueueManager:
    def __init__(self):
        # Min-heaps for priority queuing
        self.workflow_queue = []
        self.task_queue = []
        self.admitted_workflows = {} # Active workflows currently running

    def _get_sort_keys(self, wf_class: WorkflowClass, priority: PriorityClass, arrival_time: float):
        """
        Translates enums into integers for the heap. Lower number = Higher priority.
        REAL_TIME (0) beats BATCH (1).
        CRITICAL (1) beats NORMAL (3).
        """
        class_weight = 0 if wf_class == WorkflowClass.REAL_TIME else 1
        
        # PriorityClass is an IntEnum, but we want highest value (4=CRITICAL) to be lowest int for min-heap
        priority_weight = 5 - priority.value 
        
        return class_weight, priority_weight, arrival_time

    # --- EXTERNAL API ENDPOINT USES THIS ---
    def submit_workflow(self, workflow: WorkflowInstance):
        """Another service calls this to add a workflow to the cluster."""
        workflow.arrival_time = time.time()
        workflow.state = WorkflowState.QUEUED
        
        sort_keys = self._get_sort_keys(workflow.workflow_class, workflow.priority, workflow.arrival_time)
        
        # Push to heap: (class_weight, priority_weight, arrival_time, workflow_id, workflow_object)
        heapq.heappush(self.workflow_queue, (*sort_keys, workflow.workflow_instance_id, workflow))
        print(f"[QUEUE] Submitted Workflow: {workflow.workflow_instance_id} | Class: {workflow.workflow_class.name}")

    def admit_next_workflow(self) -> Optional[WorkflowInstance]:
        """Pops the highest priority workflow from the queue."""
        if not self.workflow_queue:
            return None
            
        _, _, _, _, workflow = heapq.heappop(self.workflow_queue)
        workflow.state = WorkflowState.ADMITTED
        self.admitted_workflows[workflow.workflow_instance_id] = workflow
        print(f"[ADMISSION] Admitted Workflow: {workflow.workflow_instance_id}")
        return workflow

    def enqueue_ready_tasks(self, tasks: List[TaskInstance], workflow: WorkflowInstance):
        """Puts ready tasks into the task queue, inheriting the workflow's priority."""
        for task in tasks:
            # We use the workflow's priority to ensure REAL_TIME tasks jump the line
            sort_keys = self._get_sort_keys(workflow.workflow_class, workflow.priority, workflow.arrival_time)
            
            # The task_instance_id is used to prevent tie-breaker crashes in heapq
            heapq.heappush(self.task_queue, (*sort_keys, task.task_instance_id, task, workflow))

    def get_next_task(self):
        """Pops the absolute highest priority task waiting to be scheduled."""
        if not self.task_queue:
            return None, None
        
        _, _, _, _, task, workflow = heapq.heappop(self.task_queue)
        return task, workflow