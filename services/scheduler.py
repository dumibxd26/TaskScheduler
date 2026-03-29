import random
from typing import List, Optional
from models.enums import NodeType
from models.workload import TaskInstance, TaskTemplate
from models.cluster import Node, ClusterScenario

# --- 1. The Profile Store (The Cache) ---

class ProfileStore:
    def __init__(self):
        # Maps (workflow_template_id, task_template_id) -> NodeType (for now)
        self._placement_cache = {}
        # Maps task_template_id -> (avg_runtime, avg_startup)
        self._metrics_cache = {}

    def get_cached_placement(self, wf_template_id: str, task_template_id: str) -> Optional[NodeType]:
        return self._placement_cache.get((wf_template_id, task_template_id))

    def save_placement(self, wf_template_id: str, task_template_id: str, node_type: NodeType):
        self._placement_cache[(wf_template_id, task_template_id)] = node_type

    def get_metrics(self, task_template_id: str):
        # Mocking some metrics for the initial logic
        return self._metrics_cache.get(task_template_id, {"avg_runtime": 10.0, "startup": 2.0})

# --- 2. The Actual Scheduler (The "Slow Path" / Algorithm) ---

class PlacementAlgorithm:
    def compute_placement(self, task: TaskInstance, template: TaskTemplate, available_nodes: List[Node]) -> Node:
        """
        The actual scheduling algorithm.
        For now: Filter by compatibility, then pick a random node.
        """
        # 1. Filter feasible nodes
        feasible_nodes = [
            n for n in available_nodes 
            if n.node_type in template.compatible_node_types
        ]
        
        if not feasible_nodes:
            raise ValueError(f"No feasible nodes found for task {task.task_instance_id}")

        # 2. Random placement for now (Placeholder for future HEFT/Scoring logic)
        selected_node = random.choice(feasible_nodes)
        return selected_node

# --- 3. The Runner (The Coordinator / "Fast Path") ---

class WorkflowSchedulerRunner:
    def __init__(self, profile_store: ProfileStore, algorithm: PlacementAlgorithm):
        self.profile_store = profile_store
        self.algorithm = algorithm

    def is_warm_sensitive(self, avg_runtime: float, startup_time: float) -> bool:
        """The initial logic for prewarming mimic."""
        return avg_runtime < startup_time

    def schedule_task(self, task: TaskInstance, template: TaskTemplate, cluster: ClusterScenario) -> Node:
        # 1. Check for Warmness requirement
        metrics = self.profile_store.get_metrics(task.task_template_id)
        needs_warm_instance = self.is_warm_sensitive(metrics["avg_runtime"], metrics["startup"])
        
        if needs_warm_instance:
            print(f"[WARM LOGIC] Task {task.task_instance_id} needs a warm instance.")
            # Future: add logic to specifically filter for nodes with warm_images

        # 2. Try the Fast Path (Cache Hit)
        cached_node_type = self.profile_store.get_cached_placement(
            task.workflow_instance_id, # In a real scenario, this uses template_id
            task.task_template_id
        )

        if cached_node_type:
            print(f"[CACHE HIT] Reusing placement profile: {cached_node_type}")
            # Find a random node of the cached type
            feasible_nodes = [n for n in cluster.nodes if n.node_type == cached_node_type]
            return random.choice(feasible_nodes)

        # 3. Slow Path (Cache Miss)
        print(f"[CACHE MISS] Running actual scheduler for {task.task_instance_id}")
        selected_node = self.algorithm.compute_placement(task, template, cluster.nodes)

        # 4. Save decision for next time
        self.profile_store.save_placement(
            task.workflow_instance_id, # Needs to be template_id later for cross-instance sharing
            task.task_template_id, 
            selected_node.node_type
        )

        return selected_node