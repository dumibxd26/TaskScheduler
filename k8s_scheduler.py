import sys
import time
import json
import threading
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

from models.enums import NodeType, TaskClass, TaskState
from models.cluster import Node, ClusterScenario
from models.workload import TaskInstance, TaskTemplate
from models.profile_store import ProfileStore
from services.scheduler import PlacementAlgorithm, WorkflowSchedulerRunner
from services.observer import ExecutionObserver

SCHEDULER_NAME = "ts-scheduler"
NAMESPACE = "default"

# Maps the label string on the K8s node to our internal enum
_NODE_TYPE_MAP = {
    "CPU_OPT": NodeType.CPU_OPT,
    "MEM_OPT": NodeType.MEM_OPT,
    "IO_OPT": NodeType.IO_OPT,
    "GENERAL": NodeType.GENERAL,
}

# Maps the annotation string on the pod to our internal enum
_TASK_CLASS_MAP = {
    "CPU_BOUND": TaskClass.CPU_BOUND,
    "MEMORY_BOUND": TaskClass.MEMORY_BOUND,
    "IO_BOUND": TaskClass.IO_BOUND,
}

# Default compatible node types per task class (if not provided in annotations)
_DEFAULT_COMPAT = {
    TaskClass.CPU_BOUND: [NodeType.CPU_OPT, NodeType.GENERAL],
    TaskClass.MEMORY_BOUND: [NodeType.MEM_OPT, NodeType.GENERAL],
    TaskClass.IO_BOUND: [NodeType.IO_OPT, NodeType.GENERAL],
}


class K8sScheduler:
    def __init__(self, runner: WorkflowSchedulerRunner, observer: ExecutionObserver):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.runner = runner
        self.observer = observer
        # Track pods we've already bound (avoids duplicate bindings on re-watch)
        self._bound_pods: set = set()
        # Cache cluster state; refreshed periodically
        self._cluster: ClusterScenario | None = None
        self._cluster_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Cluster state
    # ------------------------------------------------------------------
    def get_cluster_state(self) -> ClusterScenario:
        """Reads real K8s node objects and translates labels into our Node model."""
        k8s_nodes = self.v1.list_node().items
        nodes = []

        for n in k8s_nodes:
            labels = n.metadata.labels or {}
            node_type_str = labels.get("node-type")
            if node_type_str is None:
                # Control-plane or unlabelled node — skip
                continue

            node_type = _NODE_TYPE_MAP.get(node_type_str, NodeType.GENERAL)

            total_cpu = float(labels.get("ts.capacity/cpu", "1"))
            total_mem = float(labels.get("ts.capacity/memory", "1024"))

            # Allocatable from K8s gives real remaining capacity
            alloc = n.status.allocatable or {}
            free_cpu = float(alloc.get("cpu", total_cpu))
            free_mem_ki = alloc.get("memory", f"{int(total_mem * 1024)}Ki")
            # Convert KiB string (e.g. "3906252Ki") → MiB float
            if isinstance(free_mem_ki, str) and free_mem_ki.endswith("Ki"):
                free_mem = float(free_mem_ki[:-2]) / 1024
            else:
                free_mem = total_mem

            # Collect cached images on this node
            warm_images = set()
            for img in (n.status.images or []):
                for name in (img.names or []):
                    warm_images.add(name)

            nodes.append(Node(
                node_id=n.metadata.name,
                node_type=node_type,
                total_cpu=total_cpu,
                total_memory=total_mem,
                free_cpu=free_cpu,
                free_memory=free_mem,
                warm_images=warm_images,
            ))

        scenario = ClusterScenario(
            scenario_id="live-k8s",
            name="Live K8s Cluster",
            description="Auto-discovered from node labels",
            nodes=nodes,
        )
        print(f"[CLUSTER] Discovered {len(nodes)} worker nodes: "
              f"{[f'{n.node_id}({n.node_type.name})' for n in nodes]}")
        return scenario

    def refresh_cluster_state(self):
        with self._cluster_lock:
            self._cluster = self.get_cluster_state()

    # ------------------------------------------------------------------
    # Pod → internal model translation
    # ------------------------------------------------------------------
    @staticmethod
    def _pod_to_models(pod) -> tuple[TaskInstance, TaskTemplate]:
        """Translates pod annotations into TaskInstance + TaskTemplate."""
        annotations = pod.metadata.annotations or {}

        task_template_id = annotations.get("ts.scheduler/task_template_id", pod.metadata.name)
        wf_instance_id = annotations.get("ts.scheduler/workflow_instance_id", "unknown-wf")
        task_class_str = annotations.get("ts.scheduler/task_class", "CPU_BOUND")
        task_class = _TASK_CLASS_MAP.get(task_class_str, TaskClass.CPU_BOUND)

        # Parse compatible node types from annotation (comma-separated) or use defaults
        compat_str = annotations.get("ts.scheduler/compatible_node_types", "")
        if compat_str:
            compatible = [_NODE_TYPE_MAP[s.strip()] for s in compat_str.split(",")
                          if s.strip() in _NODE_TYPE_MAP]
        else:
            compatible = _DEFAULT_COMPAT.get(task_class, [NodeType.GENERAL])

        # Read resource requests from the first container
        container = pod.spec.containers[0]
        requests = (container.resources.requests or {}) if container.resources else {}
        cpu_req = float(requests.get("cpu", "0.5"))
        mem_req_str = requests.get("memory", "128Mi")
        if mem_req_str.endswith("Mi"):
            mem_req = float(mem_req_str[:-2])
        elif mem_req_str.endswith("Gi"):
            mem_req = float(mem_req_str[:-2]) * 1024
        else:
            mem_req = 128.0

        task_instance = TaskInstance(
            task_instance_id=pod.metadata.name,
            workflow_instance_id=wf_instance_id,
            task_template_id=task_template_id,
        )
        task_template = TaskTemplate(
            task_template_id=task_template_id,
            name=annotations.get("ts.scheduler/task_name", task_template_id),
            task_class=task_class,
            cpu_request=cpu_req,
            memory_request=mem_req,
            image_name=container.image,
            compatible_node_types=compatible,
        )
        return task_instance, task_template

    # ------------------------------------------------------------------
    # Pod binding
    # ------------------------------------------------------------------
    def bind_pod(self, pod_name: str, node_name: str):
        """Sends the Binding object to K8s to physically place the Pod."""
        target = client.V1ObjectReference(api_version="v1", kind="Node", name=node_name)
        meta = client.V1ObjectMeta(name=pod_name)
        binding = client.V1Binding(target=target, metadata=meta)

        try:
            self.v1.create_namespaced_pod_binding(
                name=pod_name, namespace=NAMESPACE, body=binding
            )
            print(f"[BIND] Pod '{pod_name}' -> Node '{node_name}'")
        except ApiException as e:
            print(f"[BIND ERROR] Pod '{pod_name}': {e.reason}")

    # ------------------------------------------------------------------
    # Completion watcher (background thread)
    # ------------------------------------------------------------------
    def _watch_completions(self):
        """Background thread: watches for pods that finished, records EWMA metrics."""
        print("[OBSERVER] Starting completion watcher...")
        w = watch.Watch()
        for event in w.stream(self.v1.list_namespaced_pod, namespace=NAMESPACE):
            pod = event["object"]
            if pod.spec.scheduler_name != SCHEDULER_NAME:
                continue
            if pod.status.phase not in ("Succeeded", "Failed"):
                continue
            pod_name = pod.metadata.name
            if pod_name in self._bound_pods:
                self._bound_pods.discard(pod_name)
                self._record_completion(pod)

    def _record_completion(self, pod):
        """Extracts timing from the pod and feeds it to ExecutionObserver."""
        annotations = pod.metadata.annotations or {}
        task_template_id = annotations.get("ts.scheduler/task_template_id", pod.metadata.name)
        node_name = pod.spec.node_name

        # Determine node type from our cached cluster state
        node_type = None
        with self._cluster_lock:
            if self._cluster:
                for n in self._cluster.nodes:
                    if n.node_id == node_name:
                        node_type = n.node_type
                        break

        # Compute wall-clock runtime from container status
        runtime = 0.0
        startup = 0.0
        try:
            cs = pod.status.container_statuses[0]
            if cs.state.terminated:
                t = cs.state.terminated
                if t.started_at and t.finished_at:
                    runtime = (t.finished_at - t.started_at).total_seconds()
                if pod.status.start_time and t.started_at:
                    startup = (t.started_at - pod.status.start_time).total_seconds()
        except Exception:
            pass

        status_str = pod.status.phase
        print(f"[OBSERVER] Pod '{pod.metadata.name}' {status_str} on {node_name} "
              f"(runtime={runtime:.2f}s, startup={startup:.2f}s)")

        if status_str == "Succeeded" and node_type is not None:
            # Build a minimal TaskInstance so the observer can stamp it
            task_inst = TaskInstance(
                task_instance_id=pod.metadata.name,
                workflow_instance_id=annotations.get("ts.scheduler/workflow_instance_id", ""),
                task_template_id=task_template_id,
                state=TaskState.RUNNING,
            )
            self.observer.record_task_completion(
                task_inst,
                actual_runtime=runtime,
                actual_startup=startup,
                node_id=node_name,
                node_type=node_type,
            )

    # ------------------------------------------------------------------
    # Main scheduling loop
    # ------------------------------------------------------------------
    def run(self):
        """Watch for Pending pods assigned to us and schedule them."""
        # Initial cluster discovery
        self.refresh_cluster_state()

        # Start a background thread that records completions for EWMA learning
        observer_thread = threading.Thread(target=self._watch_completions, daemon=True)
        observer_thread.start()

        # Periodically refresh cluster state (every 60s)
        def _refresh_loop():
            while True:
                time.sleep(60)
                try:
                    self.refresh_cluster_state()
                except Exception as exc:
                    print(f"[CLUSTER] Refresh failed: {exc}")

        refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
        refresh_thread.start()

        print(f"[SCHEDULER] Watching for pods with schedulerName={SCHEDULER_NAME}...")
        w = watch.Watch()

        for event in w.stream(self.v1.list_namespaced_pod, namespace=NAMESPACE):
            pod = event["object"]

            # Only care about Pending pods assigned to us that aren't bound yet
            if pod.status.phase != "Pending":
                continue
            if pod.spec.scheduler_name != SCHEDULER_NAME:
                continue
            if pod.spec.node_name is not None:
                continue
            if pod.metadata.name in self._bound_pods:
                continue

            print(f"\n[SCHEDULER] Pending pod: {pod.metadata.name}")

            try:
                task_instance, task_template = self._pod_to_models(pod)

                with self._cluster_lock:
                    cluster = self._cluster

                selected_node = self.runner.schedule_task(
                    task_instance, task_template, cluster
                )

                self.bind_pod(pod.metadata.name, selected_node.node_id)
                self._bound_pods.add(pod.metadata.name)

            except Exception as e:
                print(f"[ERROR] Failed to schedule '{pod.metadata.name}': {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("  TaskScheduler — Custom K8s Scheduler")
    print("=" * 60)

    store = ProfileStore()
    algo = PlacementAlgorithm(store)
    runner = WorkflowSchedulerRunner(store, algo)
    observer = ExecutionObserver(store)

    scheduler = K8sScheduler(runner, observer)
    scheduler.run()