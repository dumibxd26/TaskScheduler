"""
ts-scheduler — the placement brain.

Watches Pending Pods with ``schedulerName=ts-scheduler`` (created by the
ts-controller), drives the full ``SchedulerEngine`` (which calls
``AdaptivePolicy`` when ``TS_POLICY=adaptive``), and binds each pod to the
chosen node via the Kubernetes binding subresource.

The scheduler is responsible for:
  * placement decisions (ECT + UCB + thermal scoring, gang atomicity)
  * learning from pod completions (runtime EWMAs, output sizes, transfer time)
  * tracking data placement (which node holds which output)
  * persisting ProfileStore state to a hostPath volume + ConfigMap

It is NOT responsible for creating Pods or progressing the DAG — that lives
in ts-controller.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

from engine import SchedulerEngine
from models.cluster import ClusterScenario
from models.enums import (NodeType, PriorityClass, TaskClass, TaskState,
                          WorkflowClass, WorkflowState)
from models.profile_store import ProfileStore
from models.workload import (TaskInstance, TaskTemplate, WorkflowInstance,
                             WorkflowTemplate)
from services.data_placement import DataPlacement
from services.observer import ExecutionObserver
from services.policy import build_policy
from services.queue_manager import QueueManager
from services.scheduler import PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────
SCHEDULER_NAME = "ts-scheduler"
NAMESPACE = os.environ.get("TS_NAMESPACE", "default")
TICK_INTERVAL_S = float(os.environ.get("TS_TICK_INTERVAL_S", "1.0"))
CLUSTER_REFRESH_S = float(os.environ.get("TS_CLUSTER_REFRESH_S", "30"))
PERSIST_INTERVAL_S = float(os.environ.get("TS_PERSIST_INTERVAL_S", "60"))
PROFILE_PATH = os.environ.get("TS_PROFILE_PATH", "/data/profiles_learned.json")
CONFIGMAP_NAME = os.environ.get("TS_PROFILE_CONFIGMAP", "ts-scheduler-profiles")

_TASK_CLASS_MAP = {
    "CPU_BOUND": TaskClass.CPU_BOUND,
    "MEMORY_BOUND": TaskClass.MEMORY_BOUND,
    "IO_BOUND": TaskClass.IO_BOUND,
}

_NODE_TYPE_MAP = {
    "CPU_OPT": NodeType.CPU_OPT,
    "MEM_OPT": NodeType.MEM_OPT,
    "IO_OPT": NodeType.IO_OPT,
    "GENERAL": NodeType.GENERAL,
}

_PRIORITY_MAP = {
    "CRITICAL": PriorityClass.CRITICAL,
    "REAL_TIME_HIGH": PriorityClass.REAL_TIME_HIGH,
    "REAL_TIME_MEDIUM": PriorityClass.REAL_TIME_MEDIUM,
    "BATCH": PriorityClass.BATCH,
    "BEST_EFFORT": PriorityClass.BEST_EFFORT,
}

_WORKFLOW_CLASS_MAP = {
    "BATCH": WorkflowClass.BATCH,
    "INTERACTIVE": WorkflowClass.INTERACTIVE,
    "PIPELINE": WorkflowClass.PIPELINE,
    "BURST": WorkflowClass.BURST,
}


# ─────────────────────────────────────────────────────────────────────────
# Helpers — pod parsing
# ─────────────────────────────────────────────────────────────────────────
def _parse_cpu(s) -> float:
    if s is None:
        return 0.5
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s)
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    return float(s)


def _parse_mem_mi(s) -> float:
    if s is None:
        return 128.0
    s = str(s)
    if s.endswith("Mi"):
        return float(s[:-2])
    if s.endswith("Gi"):
        return float(s[:-2]) * 1024.0
    if s.endswith("Ki"):
        return float(s[:-2]) / 1024.0
    try:
        return float(s) / (1024 * 1024)
    except ValueError:
        return 128.0


# ─────────────────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────────────────
class TsScheduler:
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core = client.CoreV1Api()

        # Internal model state.
        self.store = ProfileStore()
        self.algorithm = PlacementAlgorithm(self.store)
        self.runner = WorkflowSchedulerRunner(self.store, self.algorithm)
        self.observer = ExecutionObserver(self.store)
        self.queue = QueueManager()
        self.resolver = ReadinessResolver()
        self.data_placement = DataPlacement()

        # In-cluster workflow templates discovered on the fly. The engine
        # needs WorkflowTemplate objects for upward-rank computation, but
        # at run time we only see individual pods. We synthesise a minimal
        # template from observed pods (just enough for HEFT ranking).
        self.templates: Dict[str, WorkflowTemplate] = {}

        # Build the policy. Honours TS_POLICY=adaptive|legacy.
        self.policy = build_policy(runner=self.runner)
        print(f"[SCHED] Policy: {type(self.policy).__name__}")

        # Pluggable engine wired with the adaptive bits.
        self.engine = SchedulerEngine(
            queue_manager=self.queue,
            resolver=self.resolver,
            runner=self.runner,
            templates=self.templates,
            policy=self.policy,
            data_placement=self.data_placement,
        )

        self._cluster: Optional[ClusterScenario] = None
        self._cluster_lock = threading.Lock()

        # Track pods we've already pushed into the queue and pods we've bound.
        self._enqueued_pods: set[str] = set()
        self._bound_pods: set[str] = set()
        # Map task_instance_id (== pod name) -> pod spec for binding.
        self._pending_pods: Dict[str, object] = {}
        self._state_lock = threading.Lock()

        self._dirty = False  # whether profiles need persisting

    # ------------------------------------------------------------------
    # Profile persistence — ConfigMap + local file (ported from k8s_scheduler.py)
    # ------------------------------------------------------------------
    def _load_profiles(self):
        loaded = False
        # 1. ConfigMap (in-cluster persistence).
        try:
            cm = self.core.read_namespaced_config_map(
                name=CONFIGMAP_NAME, namespace=NAMESPACE,
            )
            raw = (cm.data or {}).get("profiles", "")
            if raw:
                self.store.load_json(raw)
                loaded = True
                print(f"[SCHED] Loaded {len(self.store.profiles)} profile(s) "
                      "from ConfigMap")
        except ApiException as e:
            if e.status != 404:
                print(f"[SCHED] ConfigMap load failed: {e.reason}")

        # 2. Local file fallback.
        if not loaded and os.path.exists(PROFILE_PATH):
            try:
                with open(PROFILE_PATH) as f:
                    self.store.load_json(f.read())
                print(f"[SCHED] Loaded {len(self.store.profiles)} profile(s) "
                      f"from {PROFILE_PATH}")
            except Exception as e:
                print(f"[SCHED] file load failed: {e}")

        if not self.store.profiles:
            print("[SCHED] No saved profiles — starting fresh")

    def _save_profiles(self):
        try:
            raw = self.store.to_json()
        except Exception as e:
            print(f"[SCHED] serialise profiles failed: {e}")
            return

        # 1. ConfigMap.
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=CONFIGMAP_NAME),
            data={"profiles": raw},
        )
        try:
            self.core.replace_namespaced_config_map(
                name=CONFIGMAP_NAME, namespace=NAMESPACE, body=body,
            )
        except ApiException as e:
            if e.status == 404:
                try:
                    self.core.create_namespaced_config_map(
                        namespace=NAMESPACE, body=body,
                    )
                except ApiException as e2:
                    print(f"[SCHED] ConfigMap create failed: {e2.reason}")
            else:
                print(f"[SCHED] ConfigMap save failed: {e.reason}")

        # 2. Local file.
        try:
            os.makedirs(os.path.dirname(PROFILE_PATH) or ".", exist_ok=True)
            with open(PROFILE_PATH, "w") as f:
                f.write(raw)
        except OSError as e:
            print(f"[SCHED] file save failed: {e}")

    def _persist_loop(self):
        while True:
            time.sleep(PERSIST_INTERVAL_S)
            if self._dirty:
                self._save_profiles()
                self._dirty = False

    # ------------------------------------------------------------------
    # Cluster state poll
    # ------------------------------------------------------------------
    def refresh_cluster_state(self):
        from services.k8s_cluster import poll_cluster_state
        with self._cluster_lock:
            self._cluster = poll_cluster_state(preserve_warm=self._cluster)

    def _cluster_refresh_loop(self):
        while True:
            time.sleep(CLUSTER_REFRESH_S)
            try:
                self.refresh_cluster_state()
            except Exception as e:
                print(f"[SCHED] cluster refresh failed: {e}")

    # ------------------------------------------------------------------
    # Pod → internal model
    # ------------------------------------------------------------------
    def _pod_to_models(self, pod) -> tuple[TaskInstance, TaskTemplate, str, PriorityClass]:
        annotations = pod.metadata.annotations or {}
        labels = pod.metadata.labels or {}

        task_template_id = annotations.get(
            "ts.scheduler/task_template_id", pod.metadata.name)
        task_name = annotations.get(
            "ts.scheduler/task_name", labels.get("ts.io/task-name", task_template_id))
        wf_id = annotations.get(
            "ts.scheduler/workflow_instance_id",
            labels.get("ts.io/workflow", "unknown-wf"))
        task_class = _TASK_CLASS_MAP.get(
            annotations.get("ts.scheduler/task_class", "CPU_BOUND"),
            TaskClass.CPU_BOUND,
        )
        priority = _PRIORITY_MAP.get(
            annotations.get("ts.scheduler/priority", "BATCH"),
            PriorityClass.BATCH,
        )

        compat_str = annotations.get("ts.scheduler/compatible_node_types", "")
        if compat_str:
            compat = [_NODE_TYPE_MAP[s.strip()]
                      for s in compat_str.split(",")
                      if s.strip() in _NODE_TYPE_MAP]
        else:
            compat = [NodeType.GENERAL]

        cont = pod.spec.containers[0]
        reqs = (cont.resources.requests or {}) if cont.resources else {}
        cpu_req = _parse_cpu(reqs.get("cpu", "0.5"))
        mem_req = _parse_mem_mi(reqs.get("memory", "128Mi"))

        task_instance = TaskInstance(
            task_instance_id=pod.metadata.name,
            workflow_instance_id=wf_id,
            task_template_id=task_template_id,
        )

        template = TaskTemplate(
            task_template_id=task_template_id,
            name=task_name,
            task_class=task_class,
            cpu_request=cpu_req,
            memory_request=mem_req,
            image_name=cont.image,
            compatible_node_types=compat,
        )

        try:
            exp_out = int(annotations.get("ts.scheduler/expected_output_bytes", "0"))
            template.expected_output_bytes = exp_out
        except Exception:
            pass
        if annotations.get("ts.scheduler/gang_group_id"):
            template.gang_group_id = annotations["ts.scheduler/gang_group_id"]
        if annotations.get("ts.scheduler/checkpointable") == "true":
            template.checkpointable = True

        return task_instance, template, wf_id, priority

    def _ensure_workflow(self, wf_id: str, priority: PriorityClass) -> WorkflowInstance:
        """Find-or-create an in-memory WorkflowInstance for the engine."""
        wf = self.queue.admitted_workflows.get(wf_id)
        if wf is not None:
            return wf

        wf = WorkflowInstance(
            workflow_instance_id=wf_id,
            workflow_template_id=wf_id,
            workflow_class=WorkflowClass.BATCH,
            priority=priority,
            preemptable=True,
            task_instances={},
        )
        # Bypass the admission heap — the controller's CRD already played
        # that role. We register the workflow as already-admitted so the
        # engine's task dispatch logic sees it.
        wf.state = WorkflowState.ADMITTED
        wf.arrival_time = time.time()
        self.queue.admitted_workflows[wf_id] = wf

        # Synthesise a minimal WorkflowTemplate so DAG metrics / readiness
        # checks don't crash. We don't have the CRD's DAG edges here, but
        # the engine will only do per-task dispatch (the controller already
        # gates DAG progression at the CRD level), so an empty edge list
        # is OK for placement purposes.
        if wf_id not in self.templates:
            self.templates[wf_id] = WorkflowTemplate(
                workflow_template_id=wf_id,
                name=wf_id,
                workflow_class=WorkflowClass.BATCH,
                default_priority=priority,
                default_preemptable=True,
                tasks={},
                edges=[],
            )
        return wf

    # ------------------------------------------------------------------
    # Pod watch — feed Pending pods into the engine queue
    # ------------------------------------------------------------------
    def _on_pending_pod(self, pod):
        name = pod.metadata.name
        with self._state_lock:
            if name in self._enqueued_pods or name in self._bound_pods:
                return
            try:
                task, template, wf_id, priority = self._pod_to_models(pod)
            except Exception as e:
                print(f"[SCHED] failed to parse pod {name}: {e}")
                return

            wf = self._ensure_workflow(wf_id, priority)
            # Register the template into the workflow's template map so the
            # engine can find runtime/memory specs.
            self.templates[wf_id].tasks[template.task_template_id] = template
            # Add to the workflow's task list (used by readiness/gang logic).
            if task.task_instance_id not in wf.task_instances:
                wf.task_instances[task.task_instance_id] = task

            # Directly enqueue as ready — the controller already resolved
            # the DAG and only creates pods for tasks whose parents finished.
            self.queue.enqueue_ready_tasks([task], wf)
            self._enqueued_pods.add(name)
            self._pending_pods[name] = pod
            print(f"[SCHED] Enqueued '{name}' "
                  f"(wf={wf_id}, class={template.task_class.name}, prio={priority.name})")

    def _watch_pods(self):
        print(f"[SCHED] Watching Pending pods with schedulerName={SCHEDULER_NAME}...")
        w = watch.Watch()
        while True:
            try:
                stream = w.stream(
                    self.core.list_namespaced_pod,
                    namespace=NAMESPACE,
                    timeout_seconds=300,
                )
                for ev in stream:
                    pod = ev["object"]
                    if pod.spec.scheduler_name != SCHEDULER_NAME:
                        continue
                    if pod.status.phase != "Pending":
                        continue
                    if pod.spec.node_name:
                        continue
                    self._on_pending_pod(pod)
            except Exception as e:
                print(f"[SCHED] pod watch error: {e}; retrying in 5s")
                time.sleep(5)

    # ------------------------------------------------------------------
    # Completion watch — feed observations back to ProfileStore
    # ------------------------------------------------------------------
    def _watch_completions(self):
        print("[SCHED] Watching for pod completions...")
        w = watch.Watch()
        while True:
            try:
                stream = w.stream(
                    self.core.list_namespaced_pod,
                    namespace=NAMESPACE,
                    timeout_seconds=300,
                )
                for ev in stream:
                    pod = ev["object"]
                    if pod.spec.scheduler_name != SCHEDULER_NAME:
                        continue
                    if pod.status.phase not in ("Succeeded", "Failed"):
                        continue
                    with self._state_lock:
                        if pod.metadata.name not in self._bound_pods:
                            continue
                        self._bound_pods.discard(pod.metadata.name)
                    self._record_completion(pod)
            except Exception as e:
                print(f"[SCHED] completion watch error: {e}; retrying in 5s")
                time.sleep(5)

    def _record_completion(self, pod):
        annot = pod.metadata.annotations or {}
        labels = pod.metadata.labels or {}
        node_name = pod.spec.node_name
        task_template_id = annot.get("ts.scheduler/task_template_id",
                                     pod.metadata.name)
        task_name = annot.get("ts.scheduler/task_name",
                              labels.get("ts.io/task-name", task_template_id))
        wf_id = annot.get("ts.scheduler/workflow_instance_id",
                          labels.get("ts.io/workflow", "unknown-wf"))

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

        node_type = None
        with self._cluster_lock:
            if self._cluster:
                for n in self._cluster.nodes:
                    if n.node_id == node_name:
                        node_type = n.node_type
                        break

        print(f"[SCHED] '{pod.metadata.name}' {pod.status.phase} on {node_name} "
              f"(runtime={runtime:.2f}s, startup={startup:.2f}s)")

        if pod.status.phase == "Succeeded" and node_type is not None:
            task = TaskInstance(
                task_instance_id=pod.metadata.name,
                workflow_instance_id=wf_id,
                task_template_id=task_template_id,
                state=TaskState.RUNNING,
                assigned_node_id=node_name,
            )
            self.observer.record_task_completion(
                task=task,
                actual_runtime=runtime,
                actual_startup=startup,
                node_id=node_name,
                node_type=node_type,
            )
            # Record data placement: assume each declared output field lives
            # on the producer's node. We don't know exact bytes here without
            # scraping logs; use the template hint if available.
            self._record_data_placement(wf_id, task_name, node_name,
                                        pod.metadata.name)
            self._dirty = True
        elif pod.status.phase == "Failed":
            task = TaskInstance(
                task_instance_id=pod.metadata.name,
                workflow_instance_id=wf_id,
                task_template_id=task_template_id,
                state=TaskState.RUNNING,
                assigned_node_id=node_name,
            )
            self.observer.record_task_failure(
                task=task, node_id=node_name, node_type=node_type,
            )
            self._dirty = True

    def _record_data_placement(self, wf_id: str, task_name: str,
                               node_name: str, pod_name: str):
        # We don't have per-field sizes here; we just remember the producer
        # node for the task. Children that need bulk fetch can curl from
        # http://<node>:8080/<wf_id>/<pod_name>/.
        try:
            self.data_placement.record_output(
                wfid=wf_id, task_id=task_name,
                field_name="__bulk__",
                node_id=node_name, size_bytes=0,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bind — pull dispatch decisions out of the engine and apply to K8s
    # ------------------------------------------------------------------
    def bind_pod(self, pod_name: str, node_name: str):
        target = client.V1ObjectReference(api_version="v1", kind="Node",
                                           name=node_name)
        meta = client.V1ObjectMeta(name=pod_name)
        body = client.V1Binding(target=target, metadata=meta)
        try:
            # _preload_content=False works around kubernetes-client/python#547
            self.core.create_namespaced_pod_binding(
                name=pod_name, namespace=NAMESPACE, body=body,
                _preload_content=False,
            )
            print(f"[BIND] '{pod_name}' -> '{node_name}'")
        except ApiException as e:
            print(f"[BIND ERROR] '{pod_name}': {e.reason}")

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------
    def _tick_loop(self):
        # Inject a hook so we can observe what the engine dispatched and
        # turn each PlaceAction into a K8s bind.
        original_remove = self.queue.remove_tasks

        def _intercepted_remove(dispatched: set):
            # `dispatched` is the set of task_instance_ids the engine just
            # placed this tick. Bind each one to its assigned node.
            with self._state_lock:
                for tid in dispatched:
                    pod = self._pending_pods.pop(tid, None)
                    if pod is None:
                        continue
                    # Find the chosen node from the TaskInstance state — the
                    # engine sets assigned_node_id before calling remove.
                    node = self._find_assigned_node(tid)
                    if node:
                        self.bind_pod(tid, node)
                        self._bound_pods.add(tid)
                    self._enqueued_pods.discard(tid)
            original_remove(dispatched)

        self.queue.remove_tasks = _intercepted_remove   # type: ignore[assignment]

        while True:
            try:
                with self._cluster_lock:
                    cluster = self._cluster
                if cluster is not None:
                    self.engine.run_tick(cluster)
            except Exception as e:
                print(f"[SCHED] tick error: {e}")
            time.sleep(TICK_INTERVAL_S)

    def _find_assigned_node(self, task_id: str) -> Optional[str]:
        """Look up the engine's chosen node for a just-placed task."""
        for wf in self.queue.admitted_workflows.values():
            t = wf.task_instances.get(task_id)
            if t and t.assigned_node_id:
                return t.assigned_node_id
        return None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self):
        self._load_profiles()
        self.refresh_cluster_state()

        threading.Thread(target=self._watch_pods, daemon=True).start()
        threading.Thread(target=self._watch_completions, daemon=True).start()
        threading.Thread(target=self._cluster_refresh_loop, daemon=True).start()
        threading.Thread(target=self._persist_loop, daemon=True).start()
        self._tick_loop()


def main():
    print("=" * 60)
    print("  ts-scheduler — adaptive placement brain")
    print("=" * 60)
    TsScheduler().run()


if __name__ == "__main__":
    main()
