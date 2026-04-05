import time
from models.enums import TaskClass, NodeType, WorkflowClass, PriorityClass, DependencyType, TaskState, WorkflowState
from models.cluster import Node, ClusterScenario
from models.workload import WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.queue_manager import QueueManager
from services.observer import ExecutionObserver
from engine import SchedulerEngine

def setup_simulation():
    # 1. Create your Mac Mini "Nodes"
    cluster = ClusterScenario(
        scenario_id="local-mac", name="Mac Mini Cluster", description="3 simulated nodes",
        nodes=[
            Node("node-cpu", NodeType.CPU_OPT, 4.0, 1024.0, 4.0, 1024.0),
            Node("node-mem", NodeType.MEM_OPT, 1.0, 4096.0, 1.0, 4096.0),
            Node("node-io",  NodeType.IO_OPT,  2.0, 2048.0, 2.0, 2048.0)
        ]
    )

    # 2. Define the Recipe (IO -> Mem -> CPU)
    template = WorkflowTemplate(
        workflow_template_id="mac-mini-pipeline-v1", name="Constrained Pipeline",
        workflow_class=WorkflowClass.BATCH, default_priority=PriorityClass.BATCH, default_preemptable=True,
        tasks={
            "task-io": TaskTemplate("task-io", "IO Job", TaskClass.IO_BOUND, 0.5, 100.0, "img-io", [NodeType.IO_OPT, NodeType.GENERAL]),
            "task-mem": TaskTemplate("task-mem", "Mem Job", TaskClass.MEMORY_BOUND, 0.5, 300.0, "img-mem", [NodeType.MEM_OPT, NodeType.GENERAL]),
            "task-cpu": TaskTemplate("task-cpu", "CPU Job", TaskClass.CPU_BOUND, 1.0, 100.0, "img-cpu", [NodeType.CPU_OPT, NodeType.GENERAL])
        },
        edges=[
            DependencyEdge("task-io", "task-mem", DependencyType.DATA, ["file_path"]),
            DependencyEdge("task-mem", "task-cpu", DependencyType.DATA, ["array_size"])
        ]
    )

    return cluster, {"mac-mini-pipeline-v1": template}

# --- THE STARTER BUTTON ---
if __name__ == "__main__":
    print("=== STARTING TASK SCHEDULER SIMULATION ===")
    
    # 1. Initialize all your services
    cluster, templates = setup_simulation()
    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    queue = QueueManager()
    observer = ExecutionObserver(store)
    
    engine = SchedulerEngine(queue, resolver, runner, templates)

    # 2. Simulate an external service submitting a workflow!
    my_workflow = WorkflowInstance(
        workflow_instance_id="run-001",
        workflow_template_id="mac-mini-pipeline-v1",
        workflow_class=WorkflowClass.REAL_TIME, # Giving it high priority!
        priority=PriorityClass.CRITICAL,
        preemptable=False,
        task_instances={
            "task-io": TaskInstance("inst-io", "run-001", "task-io"),
            "task-mem": TaskInstance("inst-mem", "run-001", "task-mem"),
            "task-cpu": TaskInstance("inst-cpu", "run-001", "task-cpu")
        }
    )
    
    queue.submit_workflow(my_workflow)
    
    # 3. Run the Engine Loop
    active_tasks = []
    
    while True:
        print("\n--- Engine Tick ---")
        
        # Let the engine process the queue and schedule tasks
        engine.run_tick(cluster)
        
        # Grab any newly scheduled tasks from the engine
        # (In a real system, K8s does this. Here, we fake it for the simulation)
        for wf in queue.admitted_workflows.values():
            for t in wf.task_instances.values():
                if t.state == TaskState.RUNNING and t not in active_tasks:
                    active_tasks.append(t)
        
        # Check if we are totally done
        if not queue.admitted_workflows and not active_tasks:
            print("\n=== ALL WORKFLOWS FINISHED. SHUTTING DOWN. ===")
            break

        # Simulate time passing and ONE task finishing per tick
        if active_tasks:
            finished_task = active_tasks.pop(0)
            time.sleep(1) # Dramatic pause
            
            # The Observer records it and marks it FINISHED
            matched = None
            if finished_task.assigned_node_id:
                matched = next((n for n in cluster.nodes if n.node_id == finished_task.assigned_node_id), None)
            node_type = matched.node_type if matched else cluster.nodes[0].node_type
            observer.record_task_completion(finished_task, actual_runtime=3.0, actual_startup=0.5,
                                            node_id=finished_task.assigned_node_id,
                                            node_type=node_type)

            # Mark the image warm on the node that just ran it.
            # In a real cluster this reflects container-runtime cache; in simulation
            # it ensures the W_WARM_IMAGE=10 scoring bonus applies on repeat runs.
            if matched:
                parent_wf = queue.admitted_workflows.get(finished_task.workflow_instance_id)
                wf_tmpl = templates.get(
                    parent_wf.workflow_template_id if parent_wf else None
                ) if parent_wf else None
                if wf_tmpl and finished_task.task_template_id in wf_tmpl.tasks:
                    img = wf_tmpl.tasks[finished_task.task_template_id].image_name
                    was_warm = img in matched.warm_images
                    matched.warm_images.add(img)
                    if not was_warm:
                        print(f"[WARM]  '{img}' is now warm on '{matched.node_id}'")
            
            # Update the workflow state if all tasks are done
            parent_wf = queue.admitted_workflows.get(finished_task.workflow_instance_id)
            if parent_wf and all(t.state == TaskState.FINISHED for t in parent_wf.task_instances.values()):
                parent_wf.state = WorkflowState.FINISHED

        time.sleep(1) # Wait 1 second before the next tick