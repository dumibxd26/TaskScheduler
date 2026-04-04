import time
from models.enums import TaskClass, NodeType, WorkflowClass, PriorityClass, WorkflowState, TaskState, DependencyType
from models.cluster import Node, ClusterScenario
from models.workload import WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.observer import ExecutionObserver

# --- 1. SETUP ENVIRONMENT ---
cluster = ClusterScenario(
    scenario_id="test-cluster", name="Heterogeneous Test", description="3 Nodes",
    nodes=[
        Node("cpu-node-1", NodeType.CPU_OPT, 4.0, 8192.0, 4.0, 8192.0),
        Node("mem-node-1", NodeType.MEM_OPT, 2.0, 32768.0, 2.0, 32768.0),
        Node("io-node-1", NodeType.IO_OPT, 4.0, 8192.0, 4.0, 8192.0)
    ]
)

# --- 2. SETUP RECIPE (TaskA -> TaskB) ---
template = WorkflowTemplate(
    workflow_template_id="wf-test-1", name="Test DAG",
    workflow_class=WorkflowClass.BATCH, default_priority=PriorityClass.BATCH, default_preemptable=True,
    tasks={
        "taskA": TaskTemplate("taskA", "Download", TaskClass.IO_BOUND, 1.0, 1024.0, "imgA", [NodeType.IO_OPT]),
        "taskB": TaskTemplate("taskB", "Process", TaskClass.CPU_BOUND, 2.0, 4096.0, "imgB", [NodeType.CPU_OPT])
    },
    edges=[DependencyEdge("taskA", "taskB", DependencyType.DATA)] # B depends on A
)

# --- 3. CREATE INSTANCE ---
instance = WorkflowInstance(
    workflow_instance_id="run-001", workflow_template_id=template.workflow_template_id,
    workflow_class=template.workflow_class, priority=template.default_priority, preemptable=template.default_preemptable,
    task_instances={
        "taskA": TaskInstance("taskA-inst", "run-001", "taskA"),
        "taskB": TaskInstance("taskB-inst", "run-001", "taskB")
    }
)
instance.state = WorkflowState.ADMITTED

# --- 4. INITIALIZE SERVICES ---
store = ProfileStore()
algo = PlacementAlgorithm(store)
runner = WorkflowSchedulerRunner(store, algo)
resolver = ReadinessResolver()
observer = ExecutionObserver(store)

# --- 5. RUN THE SIMULATION LOOP ---
print("=== STARTING WORKFLOW SIMULATION ===")
workflow_finished = False
active_running_tasks = []

while not workflow_finished:
    print("\n--- Tick ---")
    
    # Step A: Find ready tasks
    ready_tasks = resolver.get_ready_tasks(instance, template)
    
    # Step B: Schedule and "Start" them
    for task in ready_tasks:
        task_template = template.tasks[task.task_template_id]
        
        # Scheduler places the task
        chosen_node = runner.schedule_task(task, task_template, cluster)
        print(f"[DISPATCHER] Starting {task.task_instance_id} on {chosen_node.node_id}...")
        
        task.state = TaskState.RUNNING
        task.assigned_node_id = chosen_node.node_id
        active_running_tasks.append(task)
        instance.state = WorkflowState.RUNNING

    # Step C: Simulate time passing & tasks finishing
    if active_running_tasks:
        # Simulate the first active task finishing
        finished_task = active_running_tasks.pop(0)
        
        # Mock actual metrics (e.g., Task A took 5 seconds, 1 sec startup)
        observer.record_task_completion(finished_task, actual_runtime=5.0, actual_startup=1.0,
                                        node_id=finished_task.assigned_node_id)
    
    # Step D: Check if workflow is completely done
    if all(t.state == TaskState.FINISHED for t in instance.task_instances.values()):
        instance.state = WorkflowState.FINISHED
        workflow_finished = True
        print("\n=== WORKFLOW FINISHED ===")
        
    time.sleep(1) # Slow down the loop so you can read the console