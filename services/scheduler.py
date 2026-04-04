import random
import time
from typing import List, Optional, Dict
from models.enums import NodeType
from models.workload import TaskInstance, TaskTemplate, DependencyEdge
from models.cluster import Node, ClusterScenario
from models.profile_store import ProfileStore

__all__ = ["ProfileStore", "PlacementAlgorithm", "WorkflowSchedulerRunner"]

# -------------------------------------------------------------------
# Scoring weights (tune these to change scheduling behaviour)
# -------------------------------------------------------------------
W_TYPE_AFFINITY     = 30   # Learned node-type preference (core thesis claim)
W_RESOURCE_FIT      = 20   # Available CPU/memory headroom + core scaling
W_NODE_AVAILABILITY = 20   # How soon current tasks on this node will finish
W_WARM_IMAGE        = 10   # Image already cached on node
W_LOAD_BALANCE      = 10   # Fewer running tasks = less contention
W_FAILURE_PENALTY   = -10  # Penalty for nodes that failed this task before
W_DATA_LOCALITY     =  5   # Prefer nodes where parent task ran (data is local)
W_MEMORY_PRESSURE   = -15  # Sharp penalty when node memory is near exhaustion

# Memory pressure threshold: below this ratio of free memory, penalise hard
MEMORY_PRESSURE_THRESHOLD = 0.15  # 15% free = danger zone

# Exploration: 10% of the time pick randomly even when we have learned data
EXPLORATION_RATE = 0.10


class PlacementAlgorithm:
    def __init__(self, profile_store: ProfileStore):
        self.profile_store = profile_store

    def score_node(self, task: TaskInstance, template: TaskTemplate,
                   node: Node, parent_node_ids: List[str] = None) -> Dict[str, float]:
        """
        Compute a detailed placement score for (task, node).
        Returns a dict of individual factor scores + a 'total' key.
        """
        scores: Dict[str, float] = {}
        profile = self.profile_store.get_profile(task.task_template_id)

        # --- 1. Type affinity (0 to W_TYPE_AFFINITY) ---
        if profile and profile.preferred_node_order:
            order = profile.preferred_node_order
            if node.node_type in order:
                rank = order.index(node.node_type)
                scores["type_affinity"] = W_TYPE_AFFINITY * (1.0 - rank / max(len(order), 1))
            else:
                scores["type_affinity"] = 0.0
        else:
            scores["type_affinity"] = W_TYPE_AFFINITY * 0.5 if node.node_type in template.compatible_node_types else 0.0

        # --- 2. Resource fit (0 to W_RESOURCE_FIT) ---
        cpu_fit = 0.0
        if node.free_cpu >= template.cpu_request:
            cpu_fit = min(node.free_cpu / max(template.cpu_request, 0.01), 5.0) / 5.0

        mem_fit = 0.0
        if node.free_memory >= template.memory_request:
            mem_fit = min(node.free_memory / max(template.memory_request, 1.0), 5.0) / 5.0

        core_bonus = 0.0
        if template.max_cores is None:
            core_bonus = min(node.free_cpu / max(node.total_cpu, 1), 1.0)
        elif template.max_cores > template.min_cores:
            usable = min(node.free_cpu, template.max_cores)
            core_bonus = usable / template.max_cores

        scores["resource_fit"] = W_RESOURCE_FIT * (cpu_fit * 0.35 + mem_fit * 0.35 + core_bonus * 0.30)

        # --- 3. Warm image (0 or W_WARM_IMAGE) ---
        scores["warm_image"] = W_WARM_IMAGE if template.image_name in node.warm_images else 0.0

        # --- 4. Load balancing (0 to W_LOAD_BALANCE) ---
        max_tasks = max(node.total_cpu * 2, 4)
        load_ratio = node.running_tasks / max_tasks
        scores["load_balance"] = W_LOAD_BALANCE * (1.0 - min(load_ratio, 1.0))

        # --- 5. Node availability forecast (0 to W_NODE_AVAILABILITY) ---
        free_in = node.estimated_free_in
        if free_in is not None:
            if free_in == 0.0:
                scores["availability"] = W_NODE_AVAILABILITY
            else:
                scores["availability"] = W_NODE_AVAILABILITY * max(0.0, 1.0 - free_in / 30.0)
        else:
            scores["availability"] = W_NODE_AVAILABILITY * 0.3

        # --- 6. Failure penalty (0 to W_FAILURE_PENALTY, negative) ---
        fail_rate = self.profile_store.get_failure_rate(task.task_template_id, node.node_id)
        scores["failure_penalty"] = W_FAILURE_PENALTY * fail_rate

        # --- 7. Data locality (0 or W_DATA_LOCALITY) ---
        if parent_node_ids and node.node_id in parent_node_ids:
            scores["data_locality"] = W_DATA_LOCALITY
        else:
            scores["data_locality"] = 0.0

        # --- 8. Memory pressure (W_MEMORY_PRESSURE to 0, negative) ---
        #   Sharp penalty when free memory drops below MEMORY_PRESSURE_THRESHOLD.
        #   At 0% free → full penalty. At threshold → 0 penalty.
        free_mem_ratio = node.free_memory / max(node.total_memory, 1)
        if free_mem_ratio < MEMORY_PRESSURE_THRESHOLD:
            pressure = 1.0 - (free_mem_ratio / MEMORY_PRESSURE_THRESHOLD)
            scores["memory_pressure"] = W_MEMORY_PRESSURE * pressure
        else:
            scores["memory_pressure"] = 0.0

        scores["total"] = sum(scores.values())
        return scores

    def compute_placement(self, task: TaskInstance, template: TaskTemplate,
                          available_nodes: List[Node],
                          parent_node_ids: List[str] = None) -> Node:
        """Filter feasible nodes, score them, pick the best."""
        feasible = [
            n for n in available_nodes
            if n.node_type in template.compatible_node_types
            and n.free_cpu >= template.cpu_request
            and n.free_memory >= template.memory_request
        ]

        if not feasible:
            feasible = [n for n in available_nodes
                        if n.node_type in template.compatible_node_types]

        if not feasible:
            raise ValueError(f"No feasible nodes for task '{task.task_instance_id}'")

        scored = [(self.score_node(task, template, n, parent_node_ids), n) for n in feasible]
        scored.sort(key=lambda x: x[0]["total"], reverse=True)

        return scored[0][1]


class WorkflowSchedulerRunner:
    def __init__(self, profile_store: ProfileStore, algorithm: PlacementAlgorithm):
        self.profile_store = profile_store
        self.algorithm = algorithm

    def schedule_task(self, task: TaskInstance, template: TaskTemplate,
                      cluster: ClusterScenario,
                      parent_node_ids: List[str] = None) -> Node:
        """
        1. If exploration level is low, explore (random compatible node).
        2. Otherwise, score all compatible nodes and pick the best.
        3. Register the task on the chosen node for availability tracking.
        """
        exploration = self.profile_store.get_completion_level(task.task_template_id)

        should_explore = (
            exploration == 0.0
            or random.random() < EXPLORATION_RATE
        )

        if should_explore and exploration < 1.0:
            compatible = [n for n in cluster.nodes
                          if n.node_type in template.compatible_node_types]
            if compatible:
                chosen = random.choice(compatible)
                expected_rt = self.profile_store.get_expected_runtime(
                    task.task_template_id, chosen.node_id)
                chosen.register_task(task.task_template_id, task.task_instance_id, expected_rt)
                print(f"[EXPLORE] '{task.task_instance_id}' -> {chosen.node_type.name} "
                      f"({chosen.node_id})  [exploration={exploration:.0%}]")
                return chosen

        # Exploitation: full scoring
        chosen = self.algorithm.compute_placement(
            task, template, cluster.nodes, parent_node_ids)
        detail = self.algorithm.score_node(task, template, chosen, parent_node_ids)

        expected_rt = self.profile_store.get_expected_runtime(
            task.task_template_id, chosen.node_id)
        chosen.register_task(task.task_template_id, task.task_instance_id, expected_rt)

        # Log the score breakdown
        parts = [f"{k}={v:.1f}" for k, v in detail.items() if k != "total" and v != 0.0]
        print(f"[SCORE]   '{task.task_instance_id}' -> {chosen.node_type.name} "
              f"({chosen.node_id})  total={detail['total']:.1f}  [{', '.join(parts)}]")
        return chosen