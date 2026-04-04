import time
from services.queue_manager import QueueManager
from services.workflow_manager import ReadinessResolver
from services.scheduler import WorkflowSchedulerRunner
from models.enums import TaskState, WorkflowState, PriorityClass

class SchedulerEngine:
    def __init__(self, queue_manager: QueueManager, resolver: ReadinessResolver, runner: WorkflowSchedulerRunner, templates: dict):
        self.queue = queue_manager
        self.resolver = resolver
        self.runner = runner
        self.templates = templates # Map of workflow_template_id -> WorkflowTemplate

    def run_tick(self, cluster_scenario):
        """
        Dispatch loop: runs every tick (e.g. every 1 second).
        1. Admit queued workflows.
        2. Resolve DAGs to find ready tasks.
        3. Walk task queue top-to-bottom by effective priority.
           - If a feasible node exists → DISPATCH (decrement virtual capacity).
           - Otherwise → HOLD (skip; don't block lower-priority tasks).
        """

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

        # 3. Dispatch loop with virtual capacity tracking
        sorted_tasks = self.queue.get_sorted_tasks()
        dispatched = set()

        for entry in sorted_tasks:
            template = self.templates[entry.workflow.workflow_template_id]
            task_template = template.tasks[entry.task_template_id]

            # Check feasibility against current virtual capacity
            feasible = [
                n for n in cluster_scenario.nodes
                if n.node_type in task_template.compatible_node_types
                and n.free_cpu >= task_template.cpu_request
                and n.free_memory >= task_template.memory_request
            ]

            if not feasible:
                if entry.effective_priority == PriorityClass.CRITICAL:
                    # CRITICAL tasks force-dispatch to any compatible node
                    feasible = [n for n in cluster_scenario.nodes
                                if n.node_type in task_template.compatible_node_types]
                if not feasible:
                    print(f"[DISPATCH] HOLD '{entry.task.task_instance_id}' "
                          f"({entry.effective_priority.name}) - no feasible node")
                    continue   # skip this task, try the next one in the queue

            # Score nodes and pick the best
            chosen_node = self.runner.schedule_task(
                entry.task, task_template, cluster_scenario)

            # Decrement virtual capacity to prevent double-booking this tick
            chosen_node.free_cpu -= task_template.cpu_request
            chosen_node.free_memory -= task_template.memory_request

            entry.task.state = TaskState.RUNNING
            entry.task.assigned_node_id = chosen_node.node_id
            dispatched.add(entry.task.task_instance_id)

            print(f"[DISPATCH] '{entry.task.task_instance_id}' "
                  f"({entry.effective_priority.name}) -> {chosen_node.node_id}")

        # Remove dispatched tasks from the queue
        self.queue.remove_tasks(dispatched)