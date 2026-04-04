import time
from services.queue_manager import QueueManager
from services.workflow_manager import ReadinessResolver
from services.scheduler import WorkflowSchedulerRunner
from models.enums import TaskState, WorkflowState

class SchedulerEngine:
    def __init__(self, queue_manager: QueueManager, resolver: ReadinessResolver, runner: WorkflowSchedulerRunner, templates: dict):
        self.queue = queue_manager
        self.resolver = resolver
        self.runner = runner
        self.templates = templates # Map of workflow_template_id -> WorkflowTemplate

    def run_tick(self, cluster_scenario):
        """This runs continuously (e.g., every 1 second)."""
        
        # 1. Admit new workflows from the backlog
        while len(self.queue.workflow_queue) > 0:
            self.queue.admit_next_workflow()

        # 2. Resolve DAGs for all active workflows
        for wf_id, workflow in list(self.queue.admitted_workflows.items()):
            template = self.templates[workflow.workflow_template_id]
            
            ready_tasks = self.resolver.get_ready_tasks(workflow, template)
            
            if ready_tasks:
                self.queue.enqueue_ready_tasks(ready_tasks, workflow)
                
            # Clean up finished workflows
            if workflow.state == WorkflowState.FINISHED:
                del self.queue.admitted_workflows[wf_id]

        # 3. Schedule the highest priority tasks
        task, workflow = self.queue.get_next_task()
        while task is not None:
            template = self.templates[workflow.workflow_template_id]
            task_template = template.tasks[task.task_template_id]
            
            # Run your actual algorithm (Cache Hit or Math)
            chosen_node = self.runner.schedule_task(task, task_template, cluster_scenario)
            
            print(f"[SCHEDULER] Placed {task.task_instance_id} (Priority: {workflow.workflow_class.name}) on {chosen_node.node_id}")
            
            # For local simulation, we just mark it running.
            task.state = TaskState.RUNNING 
            task.assigned_node_id = chosen_node.node_id
            
            # Grab the next task in the queue
            task, workflow = self.queue.get_next_task()