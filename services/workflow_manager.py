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

            # If any parent FAILED, this task can never run → propagate failure
            any_parent_failed = any(
                instance.task_instances.get(pid) is not None
                and instance.task_instances[pid].state == TaskState.FAILED
                for pid in parent_ids
            )
            if any_parent_failed:
                task_instance.state = TaskState.FAILED
                print(f"[DAG] '{task_instance.task_instance_id}' FAILED "
                      f"(parent dependency failed)")
                continue

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

    @staticmethod
    def check_workflow_terminal(instance: WorkflowInstance) -> bool:
        """
        Check if all tasks in a workflow have reached a terminal state
        (FINISHED or FAILED).  If so, set the workflow state accordingly.
        Returns True if the workflow just transitioned to a terminal state.
        """
        states = [t.state for t in instance.task_instances.values()]

        if all(s in (TaskState.FINISHED, TaskState.FAILED) for s in states):
            if any(s == TaskState.FAILED for s in states):
                instance.state = WorkflowState.FAILED
                print(f"[WORKFLOW] '{instance.workflow_instance_id}' FAILED "
                      f"({sum(1 for s in states if s == TaskState.FAILED)} task(s) failed)")
            else:
                instance.state = WorkflowState.FINISHED
                print(f"[WORKFLOW] '{instance.workflow_instance_id}' FINISHED")
            return True
        return False