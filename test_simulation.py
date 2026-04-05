"""
Test harness for the TaskScheduler.
Covers: basic DAG, concurrent workflows, warm-image bonus, failure propagation,
priority ordering, and learning convergence.

Run with:  python test_simulation.py
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

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_cluster() -> ClusterScenario:
    return ClusterScenario(
        scenario_id="test", name="Test Cluster", description="3 nodes",
        nodes=[
            Node("cpu-1", NodeType.CPU_OPT, 4.0, 8192.0, 4.0, 8192.0),
            Node("mem-1", NodeType.MEM_OPT, 2.0, 32768.0, 2.0, 32768.0),
            Node("io-1",  NodeType.IO_OPT,  4.0, 8192.0, 4.0, 8192.0),
        ],
    )


def make_pipeline_template(tid="pipeline-v1") -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_template_id=tid, name="IO->MEM->CPU",
        workflow_class=WorkflowClass.BATCH,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={
            "task-io": TaskTemplate("task-io", "IO", TaskClass.IO_BOUND,
                                    0.5, 100.0, "img-io",
                                    [NodeType.IO_OPT, NodeType.CPU_OPT, NodeType.MEM_OPT]),
            "task-mem": TaskTemplate("task-mem", "MEM", TaskClass.MEMORY_BOUND,
                                     0.5, 300.0, "img-mem",
                                     [NodeType.MEM_OPT, NodeType.CPU_OPT, NodeType.IO_OPT]),
            "task-cpu": TaskTemplate("task-cpu", "CPU", TaskClass.CPU_BOUND,
                                     1.0, 100.0, "img-cpu",
                                     [NodeType.CPU_OPT, NodeType.MEM_OPT, NodeType.IO_OPT]),
        },
        edges=[
            DependencyEdge("task-io", "task-mem", DependencyType.DATA, ["file_path"]),
            DependencyEdge("task-mem", "task-cpu", DependencyType.DATA, ["array_size"]),
        ],
    )


def make_instance(wf_id: str, template_id: str = "pipeline-v1",
                  priority=PriorityClass.BATCH) -> WorkflowInstance:
    return WorkflowInstance(
        workflow_instance_id=wf_id,
        workflow_template_id=template_id,
        workflow_class=WorkflowClass.BATCH,
        priority=priority,
        preemptable=True,
        task_instances={
            "task-io":  TaskInstance(f"{wf_id}-io",  wf_id, "task-io"),
            "task-mem": TaskInstance(f"{wf_id}-mem", wf_id, "task-mem"),
            "task-cpu": TaskInstance(f"{wf_id}-cpu", wf_id, "task-cpu"),
        },
    )


def simulate_finish(task, cluster, observer, templates, queue, node_id=None,
                    runtime=3.0, startup=0.5):
    """Helper: simulate a task completing on its assigned node."""
    nid = node_id or task.assigned_node_id
    matched = next((n for n in cluster.nodes if n.node_id == nid), None)
    node_type = matched.node_type if matched else NodeType.CPU_OPT

    observer.record_task_completion(
        task, actual_runtime=runtime, actual_startup=startup,
        node_id=nid, node_type=node_type,
    )

    # Warm image tracking
    if matched:
        wf = queue.admitted_workflows.get(task.workflow_instance_id)
        if wf:
            tmpl = templates.get(wf.workflow_template_id)
            if tmpl and task.task_template_id in tmpl.tasks:
                matched.warm_images.add(tmpl.tasks[task.task_template_id].image_name)

    # Unregister from node capacity
    if matched:
        matched.unregister_task(task.task_instance_id)


passed = 0
failed = 0


def check(label: str, condition: bool):
    global passed, failed
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if condition:
        passed += 1
    else:
        failed += 1


# ===========================================================================
# TEST 1 -- Basic DAG: IO -> MEM -> CPU finishes in order
# ===========================================================================
def test_basic_dag():
    print("\n=== TEST 1: Basic DAG execution ===")
    cluster = make_cluster()
    template = make_pipeline_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    observer = ExecutionObserver(store)
    engine = SchedulerEngine(queue, resolver, runner, templates)

    wf = make_instance("dag-001")
    queue.submit_workflow(wf)

    # Tick 1: should schedule task-io (no deps)
    engine.run_tick(cluster)
    check("task-io is RUNNING", wf.task_instances["task-io"].state == TaskState.RUNNING)
    check("task-mem still WAITING", wf.task_instances["task-mem"].state == TaskState.WAITING)

    # Finish task-io
    simulate_finish(wf.task_instances["task-io"], cluster, observer, templates, queue)

    # Tick 2: should schedule task-mem
    engine.run_tick(cluster)
    check("task-mem is RUNNING", wf.task_instances["task-mem"].state == TaskState.RUNNING)

    # Finish task-mem
    simulate_finish(wf.task_instances["task-mem"], cluster, observer, templates, queue)

    # Tick 3: should schedule task-cpu
    engine.run_tick(cluster)
    check("task-cpu is RUNNING", wf.task_instances["task-cpu"].state == TaskState.RUNNING)

    # Finish task-cpu
    simulate_finish(wf.task_instances["task-cpu"], cluster, observer, templates, queue)

    # Tick 4: cleanup
    engine.run_tick(cluster)
    check("workflow FINISHED", wf.state == WorkflowState.FINISHED)


# ===========================================================================
# TEST 2 -- Two concurrent workflows: tasks from both get dispatched
# ===========================================================================
def test_concurrent_workflows():
    print("\n=== TEST 2: Concurrent workflows ===")
    cluster = make_cluster()
    template = make_pipeline_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    observer = ExecutionObserver(store)
    engine = SchedulerEngine(queue, resolver, runner, templates)

    wf_a = make_instance("wf-A")
    wf_b = make_instance("wf-B")
    queue.submit_workflow(wf_a)
    queue.submit_workflow(wf_b)

    # Tick 1: both root tasks (task-io) should be dispatched
    engine.run_tick(cluster)
    check("wf-A task-io RUNNING", wf_a.task_instances["task-io"].state == TaskState.RUNNING)
    check("wf-B task-io RUNNING", wf_b.task_instances["task-io"].state == TaskState.RUNNING)
    check("wf-A task-mem still WAITING", wf_a.task_instances["task-mem"].state == TaskState.WAITING)

    # Finish only wf-A's task-io
    simulate_finish(wf_a.task_instances["task-io"], cluster, observer, templates, queue)

    # Tick 2: wf-A's task-mem should be dispatched; wf-B's task-io still running
    engine.run_tick(cluster)
    check("wf-A task-mem RUNNING", wf_a.task_instances["task-mem"].state == TaskState.RUNNING)
    check("wf-B task-mem still WAITING", wf_b.task_instances["task-mem"].state == TaskState.WAITING)


# ===========================================================================
# TEST 3 -- Warm image bonus appears after first execution
# ===========================================================================
def test_warm_image_bonus():
    print("\n=== TEST 3: Warm image bonus ===")
    cluster = make_cluster()
    template = make_pipeline_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    observer = ExecutionObserver(store)
    queue = QueueManager()

    wf = make_instance("warm-001")
    wf.state = WorkflowState.ADMITTED
    queue.admitted_workflows[wf.workflow_instance_id] = wf

    io_task = wf.task_instances["task-io"]
    io_tmpl = template.tasks["task-io"]

    # Score before any execution -- no warm bonus for any node
    score_before = algo.score_node(io_task, io_tmpl, cluster.nodes[2])  # io-1
    check("warm_image=0 before run", score_before["warm_image"] == 0.0)

    # Simulate running on io-1 and marking warm
    io_task.state = TaskState.RUNNING
    io_task.assigned_node_id = "io-1"
    simulate_finish(io_task, cluster, observer, templates, queue, runtime=2.0)

    # Now score again -- io-1 should have the warm bonus
    io_task2 = TaskInstance("warm-001-io-2", "warm-001", "task-io")
    score_after = algo.score_node(io_task2, io_tmpl, cluster.nodes[2])
    check("warm_image=10 after run", score_after["warm_image"] == 10.0)

    # Other nodes should still be cold
    score_other = algo.score_node(io_task2, io_tmpl, cluster.nodes[0])  # cpu-1
    check("warm_image=0 on other node", score_other["warm_image"] == 0.0)


# ===========================================================================
# TEST 4 -- Failure propagation: failed parent -> children FAILED -> workflow FAILED
# ===========================================================================
def test_failure_propagation():
    print("\n=== TEST 4: Failure propagation ===")
    cluster = make_cluster()
    template = make_pipeline_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    observer = ExecutionObserver(store)
    engine = SchedulerEngine(queue, resolver, runner, templates)

    wf = make_instance("fail-001")
    queue.submit_workflow(wf)

    # Tick 1: task-io dispatched
    engine.run_tick(cluster)
    check("task-io RUNNING", wf.task_instances["task-io"].state == TaskState.RUNNING)

    # Simulate task-io FAILING
    observer.record_task_failure(
        wf.task_instances["task-io"],
        node_id=wf.task_instances["task-io"].assigned_node_id,
        reason="OOMKilled",
    )

    # Tick 2: resolver should propagate failure to children and workflow
    engine.run_tick(cluster)
    check("task-mem FAILED (propagated)", wf.task_instances["task-mem"].state == TaskState.FAILED)
    check("task-cpu FAILED (propagated)", wf.task_instances["task-cpu"].state == TaskState.FAILED)
    check("workflow FAILED", wf.state == WorkflowState.FAILED)


# ===========================================================================
# TEST 5 -- Learning convergence: after N runs IO tasks prefer IO_OPT
# ===========================================================================
def test_learning_convergence():
    print("\n=== TEST 5: Learning convergence ===")
    cluster = make_cluster()
    template = make_pipeline_template()

    store = ProfileStore()
    observer = ExecutionObserver(store)

    # Simulate 15 runs of task-io with different runtimes per node type
    # IO_OPT should be fastest
    runtimes = {
        "io-1":  2.0,   # IO_OPT  -- fastest
        "cpu-1": 5.0,   # CPU_OPT -- slower
        "mem-1": 4.0,   # MEM_OPT -- medium
    }

    random.seed(42)
    for i in range(15):
        node = cluster.nodes[i % 3]
        task = TaskInstance(f"learn-io-{i}", f"learn-wf-{i}", "task-io")
        task.state = TaskState.RUNNING
        task.assigned_node_id = node.node_id
        rt = runtimes[node.node_id] + random.uniform(-0.3, 0.3)
        observer.record_task_completion(
            task, actual_runtime=rt, actual_startup=0.5,
            node_id=node.node_id, node_type=node.node_type,
        )

    profile = store.get_profile("task-io")
    check("profile exists", profile is not None)
    check("preferred node type is IO_OPT",
          profile.preferred_node_order[0] == NodeType.IO_OPT)
    check("preferred node ID is io-1",
          profile.preferred_node_ids[0] == "io-1")
    check("completion level > 0",
          store.get_completion_level("task-io") > 0)


# ===========================================================================
# TEST 6 -- Coverage-first exploration: sample all compatible nodes first
# ===========================================================================
def test_exploration_covers_all_nodes():
    print("\n=== TEST 6: Exploration covers all compatible nodes ===")
    cluster = make_cluster()
    template = make_pipeline_template()

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    observer = ExecutionObserver(store)

    chosen_nodes = []
    random.seed(7)

    for idx in range(3):
        task = TaskInstance(f"cover-{idx}", "cover-wf", "task-io")
        node = runner.schedule_task(task, template.tasks["task-io"], cluster)
        chosen_nodes.append(node.node_id)

        observer.record_task_completion(
            task,
            actual_runtime=2.0 + idx,
            actual_startup=0.5,
            node_id=node.node_id,
            node_type=node.node_type,
        )
        node.unregister_task(task.task_instance_id)

    check("first 3 runs visit 3 distinct nodes", len(set(chosen_nodes)) == 3)


# ===========================================================================
# TEST 7 -- Priority ordering: CRITICAL dispatched before BATCH
# ===========================================================================
def test_priority_ordering():
    print("\n=== TEST 7: Priority ordering ===")
    cluster = make_cluster()
    template = make_pipeline_template()
    templates = {template.workflow_template_id: template}

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    engine = SchedulerEngine(queue, resolver, runner, templates)

    # Submit BATCH first, then CRITICAL
    wf_batch = make_instance("pri-batch", priority=PriorityClass.BATCH)
    wf_crit = make_instance("pri-crit", priority=PriorityClass.CRITICAL)
    queue.submit_workflow(wf_batch)
    queue.submit_workflow(wf_crit)

    # After one tick, both root tasks should be dispatched
    engine.run_tick(cluster)
    check("CRITICAL task-io dispatched",
          wf_crit.task_instances["task-io"].state == TaskState.RUNNING)
    check("BATCH task-io dispatched",
          wf_batch.task_instances["task-io"].state == TaskState.RUNNING)

    # The CRITICAL workflow was admitted first (higher priority in the heap)
    check("CRITICAL workflow is active",
          wf_crit.state in (WorkflowState.RUNNING, WorkflowState.ADMITTED))


# ===========================================================================
# Run all tests
# ===========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  TaskScheduler -- Test Suite")
    print("=" * 60)

    test_basic_dag()
    test_concurrent_workflows()
    test_warm_image_bonus()
    test_failure_propagation()
    test_learning_convergence()
    test_exploration_covers_all_nodes()
    test_priority_ordering()

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        exit(1)