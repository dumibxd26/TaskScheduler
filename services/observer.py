import time
from models.enums import TaskState
from models.workload import TaskInstance
from services.scheduler import ProfileStore

class ExecutionObserver:
    def __init__(self, profile_store: ProfileStore):
        self.profile_store = profile_store

    def record_task_completion(self, task: TaskInstance, actual_runtime: float, actual_startup: float):
        """
        Called when a pod actually finishes. Updates the task state and the learning profile.
        """
        # 1. Update task state
        task.state = TaskState.FINISHED
        task.finish_time = time.time()
        
        print(f"[OBSERVER] Task {task.task_instance_id} finished in {actual_runtime}s (Startup: {actual_startup}s).")

        # 2. Update the Profile Store (Using a simple EWMA formula)
        # EWMA: New_Estimate = (Old_Estimate * 0.7) + (Actual_Observation * 0.3)
        current_metrics = self.profile_store.get_metrics(task.task_template_id)
        
        old_runtime = current_metrics.get("avg_runtime", actual_runtime)
        old_startup = current_metrics.get("startup", actual_startup)

        new_runtime = (old_runtime * 0.7) + (actual_runtime * 0.3)
        new_startup = (old_startup * 0.7) + (actual_startup * 0.3)

        # Save the new learned metrics back to the cache
        self.profile_store._metrics_cache[task.task_template_id] = {
            "avg_runtime": round(new_runtime, 2),
            "startup": round(new_startup, 2)
        }
        print(f"[LEARNING] Updated profile for '{task.task_template_id}': Runtime ~{round(new_runtime, 2)}s")