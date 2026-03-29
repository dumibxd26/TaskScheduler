from typing import List
from models.enums import TaskState, WorkflowState
from models.workload import WorkflowInstance, WorkflowTemplate, TaskInstance

class ReadinessResolver:
    def get_ready_tasks(self, instance: WorkflowInstance, template: WorkflowTemplate) -> List[TaskInstance]:
        ready_tasks = []
        
        # If workflow isn't active, nothing is ready
        if instance.state not in [WorkflowState.ADMITTED, WorkflowState.RUNNING]:
            return ready_tasks

        for task_id, task_instance in instance.task_instances.items():
            if task_instance.state != TaskState.WAITING:
                continue

            # Look through the edges to find all parents of THIS task
            parent_ids = [
                edge.parent_task_id for edge in template.edges 
                if edge.child_task_id == task_id
            ]

            # Check if all parents are FINISHED
            all_parents_finished = True
            for pid in parent_ids:
                parent_instance = instance.task_instances.get(pid)
                if not parent_instance or parent_instance.state != TaskState.FINISHED:
                    all_parents_finished = False
                    break

            if all_parents_finished:
                ready_tasks.append(task_instance)

        return ready_tasks