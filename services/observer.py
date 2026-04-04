import time
from models.enums import TaskState, NodeType
from models.workload import TaskInstance
from models.profile_store import ProfileStore


class ExecutionObserver:
    def __init__(self, profile_store: ProfileStore):
        self.profile_store = profile_store

    def record_task_completion(self, task: TaskInstance, actual_runtime: float,
                               actual_startup: float, node_id: str = None,
                               node_type: NodeType = None,
                               node_cpu_at_start: float = 0.0,
                               node_memory_at_start: float = 0.0):
        """
        Called when a pod finishes.
        - Marks the task FINISHED and stamps finish_time.
        - Records the observation in the ProfileStore (per-node, median-based).
        """
        task.state = TaskState.FINISHED
        task.finish_time = time.time()

        print(f"[OBSERVER] Task '{task.task_instance_id}' finished in "
              f"{actual_runtime:.2f}s (startup: {actual_startup:.2f}s) "
              f"on {node_id or '?'} ({node_type.name if node_type else '?'})")

        if node_id is not None and node_type is not None:
            self.profile_store.record_observation(
                task_template_id=task.task_template_id,
                node_id=node_id,
                node_type=node_type,
                actual_runtime=actual_runtime,
                actual_startup=actual_startup,
                node_cpu_at_start=node_cpu_at_start,
                node_memory_at_start=node_memory_at_start,
            )
            profile = self.profile_store.get_profile(task.task_template_id)
            if profile and profile.preferred_node_order:
                best_type = profile.preferred_node_order[0]
                best_node = profile.preferred_node_ids[0] if profile.preferred_node_ids else "?"
                completion = profile.exploration_level
                print(f"[LEARNING] '{task.task_template_id}' best: {best_type.name}/{best_node} "
                      f"[completion={completion:.0%}]")
