import time
from kubernetes import client, config
from models.enums import TaskClass, NodeType, TaskState, PriorityClass, WorkflowClass, DependencyType, WorkflowState
from models.cluster import Node, ClusterScenario
from models.workload import WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.observer import ExecutionObserver

def create_k8s_pod(v1_api, task_name, node_name, cpu_req, mem_req):
    """Physically creates the Pod in your local K8s cluster."""
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=task_name),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name="worker",
                    image="alpine",  # Tiny, instant-download linux image
                    # Simulate work: wait 3 seconds and exit successfully
                    command=["/bin/sh", "-c", "echo 'Starting thesis task...'; sleep 3; echo 'Done!'"],
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"},
                        limits={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"}
                    )
                )
            ],
            node_name=node_name,  # <--- YOUR ALGORITHM'S DECISION GOES HERE
            restart_policy="Never"
        )
    )
    
    print(f"[K8S API] Submitting Pod '{task_name}' to physical node '{node_name}'...")
    v1_api.create_namespaced_pod(namespace="default", body=pod)

def wait_for_pod_completion(v1_api, task_name) -> float:
    """Watches K8s until the Pod finishes, returning the actual runtime."""
    print(f"[K8S API] Waiting for '{task_name}' to finish...")
    start_time = time.time()
    
    while True:
        pod = v1_api.read_namespaced_pod_status(name=task_name, namespace="default")
        phase = pod.status.phase
        
        if phase == "Succeeded":
            runtime = time.time() - start_time
            print(f"[K8S API] Pod '{task_name}' succeeded in {runtime:.2f} seconds!")
            return runtime
        elif phase == "Failed":
            raise RuntimeError(f"Pod {task_name} failed!")
            
        time.sleep(1) # Check again in 1 second

if __name__ == "__main__":
    print("=== CONNECTING TO KUBERNETES ===")
    config.load_kube_config() # Loads your ~/.kube/config (connects to 'kind')
    v1 = client.CoreV1Api()
    
    # 1. Map K8s nodes to your logical models
    # Note: Replace these string names with the exact names from `kubectl get nodes`!
    k8s_node_cpu = "thesis-cluster-worker"  # Adjust if your kind node is named differently
    k8s_node_mem = "thesis-cluster-worker2"
    k8s_node_io  = "thesis-cluster-worker3"
    
    cluster = ClusterScenario(
        scenario_id="local-k8s", name="Real K8s Cluster", description="Running on Mac",
        nodes=[
            Node(k8s_node_cpu, NodeType.CPU_OPT, 4.0, 1024.0, 4.0, 1024.0),
            Node(k8s_node_mem, NodeType.MEM_OPT, 1.0, 4096.0, 1.0, 4096.0),
            Node(k8s_node_io,  NodeType.IO_OPT,  2.0, 2048.0, 2.0, 2048.0)
        ]
    )

    # 2. Setup your exact same DAG
    template = WorkflowTemplate(
        workflow_template_id="real-k8s-pipeline", name="K8s Pipeline",
        workflow_class=WorkflowClass.BATCH, default_priority=PriorityClass.NORMAL, default_preemptable=True,
        tasks={
            "task-io": TaskTemplate("task-io", "IO Job", TaskClass.IO_BOUND, 0.1, 50.0, "alpine", [NodeType.IO_OPT]),
            "task-mem": TaskTemplate("task-mem", "Mem Job", TaskClass.MEMORY_BOUND, 0.1, 50.0, "alpine", [NodeType.MEM_OPT]),
            "task-cpu": TaskTemplate("task-cpu", "CPU Job", TaskClass.CPU_BOUND, 0.1, 50.0, "alpine", [NodeType.CPU_OPT])
        },
        edges=[
            DependencyEdge("task-io", "task-mem", DependencyType.EXECUTION),
            DependencyEdge("task-mem", "task-cpu", DependencyType.EXECUTION)
        ]
    )

    my_workflow = WorkflowInstance(
        workflow_instance_id="k8s-run-001", workflow_template_id="real-k8s-pipeline",
        workflow_class=WorkflowClass.BATCH, priority=PriorityClass.NORMAL, preemptable=False,
        task_instances={
            "task-io": TaskInstance("inst-io", "k8s-run-001", "task-io"),
            "task-mem": TaskInstance("inst-mem", "k8s-run-001", "task-mem"),
            "task-cpu": TaskInstance("inst-cpu", "k8s-run-001", "task-cpu")
        }
    )

    # 3. Initialize Services
    store = ProfileStore()
    algo = PlacementAlgorithm()
    runner = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    observer = ExecutionObserver(store)

    my_workflow.state = WorkflowState.ADMITTED

    # 4. The Live K8s Loop
    while my_workflow.state != WorkflowState.FINISHED:
        ready_tasks = resolver.get_ready_tasks(my_workflow, template)
        
        for task in ready_tasks:
            task_template = template.tasks[task.task_template_id]
            
            # THE BRAIN MAKES THE DECISION
            chosen_node = runner.schedule_task(task, task_template, cluster)
            
            # THE HANDS EXECUTE THE DECISION IN K8S
            # We add a timestamp to the pod name so K8s doesn't complain about duplicates if you run this twice
            k8s_pod_name = f"{task.task_instance_id}-{int(time.time())}"
            create_k8s_pod(v1, k8s_pod_name, chosen_node.node_id, task_template.cpu_request, task_template.memory_request)
            task.state = TaskState.RUNNING
            
            # WAIT FOR K8S TO FINISH
            actual_runtime = wait_for_pod_completion(v1, k8s_pod_name)
            
            # LEARN FROM REALITY
            observer.record_task_completion(task, actual_runtime=actual_runtime, actual_startup=1.0)
            
        # Check if done
        if all(t.state == TaskState.FINISHED for t in my_workflow.task_instances.values()):
            my_workflow.state = WorkflowState.FINISHED
            print("\n=== K8S WORKFLOW FINISHED SUCESSFULLY ===")