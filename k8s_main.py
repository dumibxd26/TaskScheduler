import time
import json
from kubernetes import client, config
from models.enums import TaskClass, NodeType, TaskState, PriorityClass, WorkflowClass, DependencyType, WorkflowState
from models.cluster import Node, ClusterScenario
from models.workload import WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.observer import ExecutionObserver

NAMESPACE = "default"


SHARED_VOLUME_HOST_PATH = "/ts-data"        # path inside Kind nodes (extraMounts)
SHARED_VOLUME_MOUNT_PATH = "/data/shared"   # path inside every pod


def create_k8s_pod(v1_api, pod_name, node_name, image_name, command, args,
                   cpu_req, mem_req, workflow_id: str = "unknown",
                   env_vars: dict = None):
    """Creates a Pod pinned to a specific node, with the shared volume mounted."""
    env = [client.V1EnvVar(name=k, value=str(v)) for k, v in (env_vars or {}).items()]
    # Inject shared-volume env vars so tasks know where to read/write
    env.append(client.V1EnvVar(name="TS_SHARED_DIR", value=SHARED_VOLUME_MOUNT_PATH))
    env.append(client.V1EnvVar(name="TS_WORKFLOW_ID", value=workflow_id))

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name="worker",
                    image=image_name,
                    image_pull_policy="Never",   # use the locally loaded image
                    command=command or None,
                    args=args or None,
                    env=env or None,
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"},
                        limits={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"},
                    ),
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="shared-data",
                            mount_path=SHARED_VOLUME_MOUNT_PATH,
                        )
                    ],
                )
            ],
            node_name=node_name,      # scheduler decision hard-pinned here
            restart_policy="Never",
            volumes=[
                client.V1Volume(
                    name="shared-data",
                    host_path=client.V1HostPathVolumeSource(
                        path=SHARED_VOLUME_HOST_PATH,
                        type="DirectoryOrCreate",
                    ),
                )
            ],
        ),
    )
    print(f"[K8S] Submitting pod '{pod_name}' -> node '{node_name}'")
    v1_api.create_namespaced_pod(namespace=NAMESPACE, body=pod)



def wait_for_pod_completion(v1_api, pod_name) -> tuple:
    """
    Polls K8s until the pod Succeeded/Failed.
    Returns (startup_seconds, runtime_seconds) where:
      - startup = Pending → Running  (image pull + container init)
      - runtime = Running → Succeeded (actual task execution)

    NOTE (kind / local clusters): `kind load docker-image` copies the image into
    every node's containerd cache simultaneously, so Docker-level pull time is ~0
    on all nodes.  The startup split here still captures container-runtime
    initialisation overhead.  Our scheduler's warm_images set models a higher-level
    notion of warmth: "has this task's container ever been launched on this node?"
    Nodes that have already run a task get a W_WARM_IMAGE=10 scoring bonus on
    future placements, reflecting reduced JIT, page-cache, and runtime-init costs.
    """
    print(f"[K8S] Waiting for '{pod_name}' ...")
    t_submit = time.time()
    t_running = None
    while True:
        phase = v1_api.read_namespaced_pod_status(
            name=pod_name, namespace=NAMESPACE
        ).status.phase
        if phase == "Running" and t_running is None:
            t_running = time.time()
        elif phase == "Succeeded":
            now = time.time()
            startup = (t_running - t_submit) if t_running is not None else 0.0
            runtime = (now - t_running) if t_running is not None else (now - t_submit)
            print(f"[K8S] '{pod_name}' done | startup={startup:.2f}s  runtime={runtime:.2f}s")
            return startup, runtime
        if phase == "Failed":
            raise RuntimeError(f"Pod '{pod_name}' failed!")
        time.sleep(1)


def extract_task_output(v1_api, pod_name) -> dict:
    """
    Reads pod logs and parses the __TS_OUTPUT__=<json> line printed by each task.
    Returns the parsed dict, or {} if not found.
    """
    try:
        logs = v1_api.read_namespaced_pod_log(name=pod_name, namespace=NAMESPACE)
        for line in logs.splitlines():
            if line.startswith("__TS_OUTPUT__="):
                return json.loads(line[len("__TS_OUTPUT__="):])
    except Exception as e:
        print(f"[WARN] Could not read logs for '{pod_name}': {e}")
    return {}


if __name__ == "__main__":
    print("=== CONNECTING TO KUBERNETES ===")
    config.load_kube_config()
    v1 = client.CoreV1Api()

    # Node names must match kubeadmConfigPatches in kind-cluster.yaml
    cluster = ClusterScenario(
        scenario_id="local-k8s", name="Real K8s Cluster",
        description="6-node kind cluster (2×CPU_OPT, 2×MEM_OPT, 2×IO_OPT)",
        nodes=[
            Node("ts-node-cpu-1", NodeType.CPU_OPT, 2.0, 1024.0, 2.0, 1024.0),
            Node("ts-node-cpu-2", NodeType.CPU_OPT, 2.0, 1024.0, 2.0, 1024.0),
            Node("ts-node-mem-1", NodeType.MEM_OPT, 1.0, 2048.0, 1.0, 2048.0),
            Node("ts-node-mem-2", NodeType.MEM_OPT, 1.0, 2048.0, 1.0, 2048.0),
            Node("ts-node-io-1",  NodeType.IO_OPT,  1.0, 1024.0, 1.0, 1024.0),
            Node("ts-node-io-2",  NodeType.IO_OPT,  1.0, 1024.0, 1.0, 1024.0),
        ],
    )

    # DAG: IO -> MEM -> CPU, with metadata flowing between steps via stdout logs
    template = WorkflowTemplate(
        workflow_template_id="real-k8s-pipeline",
        name="K8s Real Worker Pipeline",
        workflow_class=WorkflowClass.BATCH,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={
            "task-io": TaskTemplate(
                "task-io", "IO Job", TaskClass.IO_BOUND,
                cpu_request=0.5, memory_request=512.0,
                image_name="ts-task-io:v1",
                compatible_node_types=[NodeType.IO_OPT, NodeType.CPU_OPT, NodeType.MEM_OPT],
                min_cores=1, max_cores=1,
            ),
            "task-mem": TaskTemplate(
                "task-mem", "Mem Job", TaskClass.MEMORY_BOUND,
                cpu_request=0.5, memory_request=768.0,
                image_name="ts-task-mem:v1",
                compatible_node_types=[NodeType.MEM_OPT, NodeType.CPU_OPT, NodeType.IO_OPT],
                min_cores=1, max_cores=1,
            ),
            "task-cpu": TaskTemplate(
                "task-cpu", "CPU Job", TaskClass.CPU_BOUND,
                cpu_request=1.0, memory_request=256.0,
                image_name="ts-task-cpu:v1",
                compatible_node_types=[NodeType.CPU_OPT, NodeType.MEM_OPT, NodeType.IO_OPT],
                min_cores=1, max_cores=None,  # scales with cores
            ),
        },
        edges=[
            DependencyEdge("task-io",  "task-mem", DependencyType.DATA, ["generated_file_path"]),
            DependencyEdge("task-mem", "task-cpu", DependencyType.DATA, ["processed_array_size"]),
        ],
    )

    wf_id = "k8s-run-001"
    my_workflow = WorkflowInstance(
        workflow_instance_id=wf_id,
        workflow_template_id="real-k8s-pipeline",
        workflow_class=WorkflowClass.BATCH,
        priority=PriorityClass.BATCH,
        preemptable=False,
        task_instances={
            "task-io":  TaskInstance("inst-io",  wf_id, "task-io"),
            "task-mem": TaskInstance("inst-mem", wf_id, "task-mem"),
            "task-cpu": TaskInstance("inst-cpu", wf_id, "task-cpu"),
        },
    )

    store    = ProfileStore()
    algo     = PlacementAlgorithm(store)
    runner   = WorkflowSchedulerRunner(store, algo)
    resolver = ReadinessResolver()
    observer = ExecutionObserver(store)

    my_workflow.state = WorkflowState.ADMITTED

    print(f"\n=== STARTING WORKFLOW {wf_id} ===\n")

    while my_workflow.state != WorkflowState.FINISHED:
        ready_tasks = resolver.get_ready_tasks(my_workflow, template)

        for task in ready_tasks:
            task_tmpl = template.tasks[task.task_template_id]

            # 1. Track parent node IDs for data-locality scoring
            parent_edges = [e for e in template.edges if e.child_task_id == task.task_template_id]
            parent_node_ids = []
            for edge in parent_edges:
                parent_task = my_workflow.task_instances.get(edge.parent_task_id)
                if parent_task and parent_task.assigned_node_id:
                    parent_node_ids.append(parent_task.assigned_node_id)

            # 2. Scheduler picks the best node (passes parent node IDs for data locality)
            chosen_node = runner.schedule_task(task, task_tmpl, cluster, parent_node_ids)

            # Record warm/cold state BEFORE the task runs so the log is informative
            # and so future scheduling decisions can see the pre-run state.
            was_warm = task_tmpl.image_name in chosen_node.warm_images
            print(f"[WARM]  '{task_tmpl.image_name}' on '{chosen_node.node_id}': "
                  f"{'WARM (bonus +10)' if was_warm else 'COLD (first run)'}")

            # 3. Submit real pod to K8s — shared volume is mounted automatically;
            #    tasks read parent outputs and write their own outputs directly.
            pod_name = f"{task.task_instance_id}-{int(time.time())}"
            task.start_time = time.time()
            task.assigned_node_id = chosen_node.node_id
            create_k8s_pod(
                v1, pod_name, chosen_node.node_id,
                task_tmpl.image_name, task_tmpl.command, task_tmpl.args,
                task_tmpl.cpu_request, task_tmpl.memory_request,
                workflow_id=wf_id,
            )
            task.state = TaskState.RUNNING

            # 4. Block until pod completes — returns (startup_seconds, runtime_seconds)
            actual_startup, actual_runtime = wait_for_pod_completion(v1, pod_name)

            # 5. Unregister task from node tracker (it's done)
            chosen_node.unregister_task(task.task_instance_id)

            # 6. Update the learning profile
            observer.record_task_completion(
                task, actual_runtime=actual_runtime, actual_startup=actual_startup,
                node_id=chosen_node.node_id, node_type=chosen_node.node_type,
                node_cpu_at_start=chosen_node.cpu_usage_ratio,
                node_memory_at_start=chosen_node.memory_usage_ratio,
            )

        if all(t.state == TaskState.FINISHED for t in my_workflow.task_instances.values()):
            my_workflow.state = WorkflowState.FINISHED
            print(f"\n=== WORKFLOW {wf_id} COMPLETE ===")
            break

        time.sleep(1)  # brief pause before next DAG resolution tick