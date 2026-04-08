"""
Full simulation: 6 heterogeneous nodes, 10 workflows submitted in bursts.

Demonstrates:
  - Coverage-first exploration across all 6 nodes
  - Depth exploration (3 samples per node)
  - Learning convergence toward best node types
  - Concurrent workflows sharing the cluster
  - Warm-image effects
  - Priority ordering (CRITICAL vs BATCH)
  - DAG dependency enforcement

Run with:  python run_simulation.py
"""
import time
import random
from models.enums import (
    TaskClass, NodeType, WorkflowClass, PriorityClass,
    WorkflowState, TaskState, DependencyType,
)
from models.cluster import Node, ClusterScenario
from models.workload import (
    WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge,
)
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.queue_manager import QueueManager
from services.observer import ExecutionObserver
from engine import SchedulerEngine

random.seed(42)

# ---------------------------------------------------------------------------
# Cluster: 6 nodes, 2 of each type
# ---------------------------------------------------------------------------
def make_big_cluster() -> ClusterScenario:
    return ClusterScenario(
        scenario_id="sim-6node", name="6-Node Heterogeneous Cluster",
        description="2×CPU_OPT, 2×MEM_OPT, 2×IO_OPT",
        nodes=[
            Node("cpu-1", NodeType.CPU_OPT, 4.0, 2048.0, 4.0, 2048.0),
            Node("cpu-2", NodeType.CPU_OPT, 4.0, 2048.0, 4.0, 2048.0),
            Node("mem-1", NodeType.MEM_OPT, 1.0, 8192.0, 1.0, 8192.0),
            Node("mem-2", NodeType.MEM_OPT, 1.0, 8192.0, 1.0, 8192.0),
            Node("io-1",  NodeType.IO_OPT,  2.0, 4096.0, 2.0, 4096.0),
            Node("io-2",  NodeType.IO_OPT,  2.0, 4096.0, 2.0, 4096.0),
        ],
    )

# ---------------------------------------------------------------------------
# Workflow template: IO -> MEM -> CPU (same DAG, 3 tasks)
# ---------------------------------------------------------------------------
def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_template_id="pipeline-v1", name="IO → MEM → CPU Pipeline",
        workflow_class=WorkflowClass.BATCH,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={
            "task-io": TaskTemplate(
                "task-io", "IO Task", TaskClass.IO_BOUND,
                cpu_request=0.5, memory_request=200.0, image_name="img-io",
                compatible_node_types=[NodeType.IO_OPT, NodeType.CPU_OPT, NodeType.MEM_OPT],
            ),
            "task-mem": TaskTemplate(
                "task-mem", "Memory Task", TaskClass.MEMORY_BOUND,
                cpu_request=0.5, memory_request=500.0, image_name="img-mem",
                compatible_node_types=[NodeType.MEM_OPT, NodeType.CPU_OPT, NodeType.IO_OPT],
            ),
            "task-cpu": TaskTemplate(
                "task-cpu", "CPU Task", TaskClass.CPU_BOUND,
                cpu_request=1.0, memory_request=200.0, image_name="img-cpu",
                compatible_node_types=[NodeType.CPU_OPT, NodeType.MEM_OPT, NodeType.IO_OPT],
            ),
        },
        edges=[
            DependencyEdge("task-io", "task-mem", DependencyType.DATA, ["file_path"]),
            DependencyEdge("task-mem", "task-cpu", DependencyType.DATA, ["array_size"]),
        ],
    )

# ---------------------------------------------------------------------------
# Simulated runtimes: tasks are faster on their matching node type
# ---------------------------------------------------------------------------
# Returns (runtime, startup) in seconds.
RUNTIME_TABLE = {
    # task-io: fastest on IO_OPT, slowest on CPU_OPT
    ("task-io", NodeType.IO_OPT):  (1.5, 0.3),
    ("task-io", NodeType.CPU_OPT): (4.0, 0.5),
    ("task-io", NodeType.MEM_OPT): (3.0, 0.4),
    # task-mem: fastest on MEM_OPT
    ("task-mem", NodeType.MEM_OPT): (2.0, 0.3),
    ("task-mem", NodeType.CPU_OPT): (5.0, 0.6),
    ("task-mem", NodeType.IO_OPT):  (4.5, 0.5),
    # task-cpu: fastest on CPU_OPT
    ("task-cpu", NodeType.CPU_OPT): (2.0, 0.2),
    ("task-cpu", NodeType.MEM_OPT): (6.0, 0.5),
    ("task-cpu", NodeType.IO_OPT):  (5.5, 0.4),
}

def get_simulated_runtime(task_template_id: str, node_type: NodeType) -> tuple:
    """Returns (runtime, startup) with a small random jitter."""
    base_rt, base_su = RUNTIME_TABLE.get(
        (task_template_id, node_type), (4.0, 0.5)
    )
    jitter = random.uniform(-0.2, 0.2)
    return max(0.5, base_rt + jitter), max(0.1, base_su + jitter * 0.3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_wf(wf_id: str, priority=PriorityClass.BATCH) -> WorkflowInstance:
    return WorkflowInstance(
        workflow_instance_id=wf_id,
        workflow_template_id="pipeline-v1",
        workflow_class=WorkflowClass.BATCH,
        priority=priority,
        preemptable=True,
        task_instances={
            "task-io":  TaskInstance(f"{wf_id}-io",  wf_id, "task-io"),
            "task-mem": TaskInstance(f"{wf_id}-mem", wf_id, "task-mem"),
            "task-cpu": TaskInstance(f"{wf_id}-cpu", wf_id, "task-cpu"),
        },
    )


def simulate_finish(task, cluster, observer, templates, queue):
    """Simulate a task finishing with realistic per-node-type runtimes."""
    node = next((n for n in cluster.nodes if n.node_id == task.assigned_node_id), None)
    if not node:
        return

    rt, su = get_simulated_runtime(task.task_template_id, node.node_type)
    observer.record_task_completion(
        task, actual_runtime=rt, actual_startup=su,
        node_id=node.node_id, node_type=node.node_type,
        node_cpu_at_start=node.cpu_usage_ratio,
        node_memory_at_start=node.memory_usage_ratio,
    )

    # Mark warm image
    wf = queue.admitted_workflows.get(task.workflow_instance_id)
    if wf:
        tmpl = templates.get(wf.workflow_template_id)
        if tmpl and task.task_template_id in tmpl.tasks:
            img = tmpl.tasks[task.task_template_id].image_name
            was_warm = img in node.warm_images
            node.warm_images.add(img)
            if not was_warm:
                print(f"  [WARM] '{img}' now warm on '{node.node_id}'")

    # Free capacity (handled automatically by unregister_task now)
    node.unregister_task(task.task_instance_id)


def print_cluster_status(cluster):
    """Print a one-line status of each node."""
    print("  ┌─────────────────────────────────────────────────────────────┐")
    for n in cluster.nodes:
        warm = ",".join(sorted(n.warm_images)) if n.warm_images else "-"
        print(f"  │ {n.node_id:6s} ({n.node_type.name:7s}) "
              f"cpu={n.free_cpu:.1f}/{n.total_cpu:.1f}  "
              f"mem={n.free_memory:.0f}/{n.total_memory:.0f}  "
              f"tasks={n.running_tasks}  warm=[{warm}]")
    print("  └─────────────────────────────────────────────────────────────┘")


def print_profile_summary(store, task_ids):
    """Print the current learned preferences."""
    print("\n  ╔══ LEARNED PROFILES ══════════════════════════════════════════╗")
    for tid in task_ids:
        p = store.get_profile(tid)
        if not p:
            print(f"  ║ {tid}: no data yet")
            continue
        order = ", ".join(nt.name for nt in p.preferred_node_order) if p.preferred_node_order else "?"
        nodes = ", ".join(p.preferred_node_ids[:3]) if p.preferred_node_ids else "?"
        obs = sum(m.count for m in p.metrics_by_node.values())
        expl = p.exploration_level
        print(f"  ║ {tid:10s}  best_types=[{order}]  best_nodes=[{nodes}]  "
              f"obs={obs}  exploration={expl:.0%}")
    print("  ╚═════════════════════════════════════════════════════════════╝")


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  TASK SCHEDULER SIMULATION — 6 Nodes, 10 Workflows")
    print("=" * 70)

    cluster = make_big_cluster()
    template = make_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    observer = ExecutionObserver(store)
    engine = SchedulerEngine(queue, resolver, runner, templates)

    # --- Submit 10 workflows in 3 bursts ---
    workflows = []

    # Burst 1: 4 BATCH workflows
    print("\n>>> BURST 1: Submitting 4 BATCH workflows")
    for i in range(1, 5):
        wf = make_wf(f"wf-{i:02d}", PriorityClass.BATCH)
        queue.submit_workflow(wf)
        workflows.append(wf)

    # Burst 2: 3 REAL_TIME_MEDIUM workflows
    print("\n>>> BURST 2: Submitting 3 REAL_TIME_MEDIUM workflows")
    for i in range(5, 8):
        wf = make_wf(f"wf-{i:02d}", PriorityClass.REAL_TIME_MEDIUM)
        queue.submit_workflow(wf)
        workflows.append(wf)

    # Burst 3: 3 more — 1 CRITICAL + 2 BATCH
    print("\n>>> BURST 3: Submitting 1 CRITICAL + 2 BATCH workflows")
    wf_crit = make_wf("wf-08", PriorityClass.CRITICAL)
    queue.submit_workflow(wf_crit)
    workflows.append(wf_crit)
    for i in range(9, 11):
        wf = make_wf(f"wf-{i:02d}", PriorityClass.BATCH)
        queue.submit_workflow(wf)
        workflows.append(wf)

    # --- Engine loop ---
    tick = 0
    max_ticks = 200  # safety cap

    while tick < max_ticks:
        tick += 1

        # Check if all workflows are done
        all_done = all(
            w.state in (WorkflowState.FINISHED, WorkflowState.FAILED)
            for w in workflows
        )
        if all_done:
            break

        # Gather currently running tasks before the tick
        running_before = []
        for wf in workflows:
            for t in wf.task_instances.values():
                if t.state == TaskState.RUNNING:
                    running_before.append((wf, t))

        # Simulate finishing 1 random running task per tick
        # (this paces the simulation; in real K8s this is driven by pod completion)
        if running_before:
            wf_fin, task_fin = random.choice(running_before)
            simulate_finish(task_fin, cluster, observer, templates, queue)

        print(f"\n{'─' * 70}")
        print(f"  TICK {tick}")
        print(f"{'─' * 70}")
        print_cluster_status(cluster)

        # Run the engine tick (admit, resolve DAGs, dispatch)
        engine.run_tick(cluster)

        # Count active
        n_queued = len(queue.workflow_queue)
        n_active = len(queue.admitted_workflows)
        n_finished = sum(1 for w in workflows if w.state == WorkflowState.FINISHED)
        n_failed = sum(1 for w in workflows if w.state == WorkflowState.FAILED)
        n_running_tasks = sum(
            1 for w in workflows
            for t in w.task_instances.values()
            if t.state == TaskState.RUNNING
        )
        print(f"\n  [STATUS] workflows: queued={n_queued} active={n_active} "
              f"finished={n_finished} failed={n_failed}  |  running_tasks={n_running_tasks}")

    # --- Final summary ---
    print("\n" + "=" * 70)
    print("  SIMULATION COMPLETE")
    print("=" * 70)

    print_profile_summary(store, ["task-io", "task-mem", "task-cpu"])

    # Print per-workflow summary
    print("\n  WORKFLOW RESULTS:")
    for wf in workflows:
        task_nodes = {
            tid: t.assigned_node_id or "?"
            for tid, t in wf.task_instances.items()
        }
        print(f"    {wf.workflow_instance_id:8s}  {wf.priority.name:18s}  "
              f"state={wf.state.name:10s}  placements={task_nodes}")

    # Print warm images per node
    print("\n  WARM IMAGES PER NODE:")
    for n in cluster.nodes:
        warm = ", ".join(sorted(n.warm_images)) if n.warm_images else "(none)"
        print(f"    {n.node_id:6s} ({n.node_type.name:7s}): {warm}")

    # Node utilization (how many tasks ran on each node)
    print("\n  PLACEMENT HISTORY:")
    for tid in ["task-io", "task-mem", "task-cpu"]:
        p = store.get_profile(tid)
        if not p:
            continue
        for nid, nm in sorted(p.metrics_by_node.items()):
            print(f"    {tid:10s} on {nid:6s}: {nm.count} runs, "
                  f"median_rt={nm.median_runtime:.2f}s, median_su={nm.median_startup:.2f}s, "
                  f"total_cost={nm.total_cost:.2f}s")

    print(f"\n  Total ticks: {tick}")
    print("=" * 70)


if __name__ == "__main__":
    main()
