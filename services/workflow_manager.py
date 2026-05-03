from typing import List, Dict, Optional
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

    def check_gang_readiness(self, ready_tasks: List[TaskInstance],
                             template: WorkflowTemplate) -> List[TaskInstance]:
        """
        Enforce H8 gang scheduling: tasks sharing a gang_group_id must all
        be DAG-ready before any of them can be enqueued for scheduling.
        Non-gang tasks pass through unchanged.
        
        Returns the filtered list of tasks that may proceed.
        """
        # Partition into gang groups and independent tasks.
        gang_groups: Dict[str, List[TaskInstance]] = {}
        independent: List[TaskInstance] = []
        ready_ids = {t.task_template_id for t in ready_tasks}

        for task in ready_tasks:
            tmpl = template.tasks.get(task.task_template_id)
            if tmpl and tmpl.gang_group_id:
                gang_groups.setdefault(tmpl.gang_group_id, []).append(task)
            else:
                independent.append(task)

        result = list(independent)

        # For each gang group, check if ALL members of the group are ready.
        for group_id, members in gang_groups.items():
            # Find all task_template_ids in this gang group.
            all_gang_ids = [
                tid for tid, t in template.tasks.items()
                if t.gang_group_id == group_id
            ]
            all_ready = all(tid in ready_ids for tid in all_gang_ids)
            if all_ready:
                result.extend(members)
            else:
                # Not all gang members ready — hold them all back.
                for task in members:
                    print(f"[GANG] Holding '{task.task_instance_id}' — "
                          f"gang group '{group_id}' not fully ready")

        return result

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