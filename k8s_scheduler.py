from __future__ import annotations

import os
import sys
import time
import json
import threading
from pathlib import Path
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


PROFILE_CONFIGMAP = "ts-scheduler-profiles"
SAVE_INTERVAL = 30  # seconds between ConfigMap saves
# Local file path for profile persistence (survives cluster deletion).
# Set TS_PROFILE_PATH env var to override.
PROFILE_FILE = Path(os.environ.get(
    "TS_PROFILE_PATH",
    os.path.join(os.path.dirname(__file__), "profiles_learned.json"),
))


class K8sScheduler:
    def __init__(self, runner: WorkflowSchedulerRunner, observer: ExecutionObserver,
                 store: ProfileStore):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.runner = runner
        self.observer = observer
        self.store = store
        # Track pods we've already bound (avoids duplicate bindings on re-watch)
        self._bound_pods: set = set()
        # Cache cluster state; refreshed periodically
        self._cluster: ClusterScenario | None = None
        self._cluster_lock = threading.Lock()
        self._dirty = False  # True when profiles have changed since last save

    # ------------------------------------------------------------------
    # Cluster state — polls K8s for real free resources
    # ------------------------------------------------------------------
    def refresh_cluster_state(self):
        from services.k8s_cluster import poll_cluster_state
        with self._cluster_lock:
            self._cluster = poll_cluster_state(preserve_warm=self._cluster)

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
        cpu_req_str = requests.get("cpu", "0.5")
        # K8s may return millicore format like "200m" or plain "0.5"
        if isinstance(cpu_req_str, str) and cpu_req_str.endswith("m"):
            cpu_req = float(cpu_req_str[:-1]) / 1000.0
        else:
            cpu_req = float(cpu_req_str)

        mem_req_str = requests.get("memory", "128Mi")
        if mem_req_str.endswith("Mi"):
            mem_req = float(mem_req_str[:-2])
        elif mem_req_str.endswith("Gi"):
            mem_req = float(mem_req_str[:-2]) * 1024
        elif mem_req_str.endswith("Ki"):
            mem_req = float(mem_req_str[:-2]) / 1024
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
            # NOTE: _preload_content=False avoids a known kubernetes-client bug
            # (kubernetes-client/python#547): the binding subresource returns
            # an empty body on success, but the client tries to deserialize it
            # back into V1Binding and raises "Invalid value for `target`,
            # must not be `None`" — even though the bind already succeeded.
            self.v1.create_namespaced_pod_binding(
                name=pod_name, namespace=NAMESPACE, body=binding,
                _preload_content=False,
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
            self._dirty = True

    # ------------------------------------------------------------------
    # Profile persistence — ConfigMap
    # ------------------------------------------------------------------
    def _load_profiles(self):
        """Load saved profiles from ConfigMap first, then fall back to local file."""
        loaded = False
        # Try ConfigMap (in-cluster persistence).
        try:
            cm = self.v1.read_namespaced_config_map(
                name=PROFILE_CONFIGMAP, namespace=NAMESPACE
            )
            raw = (cm.data or {}).get("profiles", "")
            if raw:
                self.store.load_json(raw)
                loaded = True
                count = sum(
                    sum(nm.count for nm in p.metrics_by_node.values())
                    for p in self.store.profiles.values()
                )
                print(f"[PERSIST] Loaded {len(self.store.profiles)} task profile(s) "
                      f"({count} total observations) from ConfigMap")
        except ApiException as e:
            if e.status == 404:
                pass  # will try local file
            else:
                print(f"[PERSIST] ConfigMap load failed: {e.reason}")

        # Fall back to local file (survives cluster deletion).
        if not loaded and PROFILE_FILE.exists():
            try:
                raw = PROFILE_FILE.read_text()
                self.store.load_json(raw)
                count = sum(
                    sum(nm.count for nm in p.metrics_by_node.values())
                    for p in self.store.profiles.values()
                )
                print(f"[PERSIST] Loaded {len(self.store.profiles)} task profile(s) "
                      f"({count} total observations) from {PROFILE_FILE}")
            except Exception as e:
                print(f"[PERSIST] Local file load failed: {e}")

        if not self.store.profiles:
            print("[PERSIST] No saved profiles found — starting fresh")

    def _save_profiles(self):
        """Save current profiles to ConfigMap AND local file."""
        json_data = self.store.to_json()

        # 1. ConfigMap (available to in-cluster restarts).
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=PROFILE_CONFIGMAP),
            data={"profiles": json_data},
        )
        try:
            self.v1.replace_namespaced_config_map(
                name=PROFILE_CONFIGMAP, namespace=NAMESPACE, body=body
            )
        except ApiException as e:
            if e.status == 404:
                self.v1.create_namespaced_config_map(
                    namespace=NAMESPACE, body=body
                )
            else:
                print(f"[PERSIST] ConfigMap save failed: {e.reason}")

        # 2. Local file (survives cluster deletion / kind recreate).
        try:
            PROFILE_FILE.write_text(json_data)
        except OSError as e:
            print(f"[PERSIST] Local file save failed: {e}")

        count = sum(
            sum(nm.count for nm in p.metrics_by_node.values())
            for p in self.store.profiles.values()
        )
        print(f"[PERSIST] Saved {len(self.store.profiles)} profile(s) "
              f"({count} observations) to ConfigMap + {PROFILE_FILE.name}")

    def _persist_loop(self):
        """Background thread: saves profiles every SAVE_INTERVAL seconds."""
        while True:
            time.sleep(SAVE_INTERVAL)
            if self._dirty:
                self._save_profiles()
                self._dirty = False

    # ------------------------------------------------------------------
    # Main scheduling loop
    # ------------------------------------------------------------------
    def run(self):
        """Watch for Pending pods assigned to us and schedule them."""
        # Load previously saved profiles
        self._load_profiles()

        # Initial cluster discovery
        self.refresh_cluster_state()

        # Start a background thread that records completions for EWMA learning
        observer_thread = threading.Thread(target=self._watch_completions, daemon=True)
        observer_thread.start()

        # Start a background thread that periodically saves profiles
        persist_thread = threading.Thread(target=self._persist_loop, daemon=True)
        persist_thread.start()

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

    scheduler = K8sScheduler(runner, observer, store)
    scheduler.run()