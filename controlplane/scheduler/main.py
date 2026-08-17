"""
ts-scheduler — the placement brain.

Watches Pending Pods with ``schedulerName=ts-scheduler`` (created by the
ts-controller), drives the full ``SchedulerEngine`` (which calls
``AdaptivePolicy`` when ``TS_POLICY=adaptive``), and binds each pod to the
chosen node via the Kubernetes binding subresource.

The scheduler is responsible for:
  * placement decisions (ECT + UCB + thermal scoring, gang atomicity)
    * learning from pod completions and optional ``__TS_METRICS__`` records
  * tracking data placement (which node holds which output)
  * persisting ProfileStore state to a hostPath volume + ConfigMap

It is NOT responsible for creating Pods or progressing the DAG — that lives
in ts-controller.
"""

from __future__ import annotations

import json
import os
import random
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

        random_seed = os.environ.get("TS_RANDOM_SEED")
        if random_seed is not None:
            random.seed(int(random_seed))
            print(f"[SCHED] Random seed: {random_seed}")

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
            preempt_task=self.preempt_pod,
        )

        self._cluster: Optional[ClusterScenario] = None
        self._cluster_lock = threading.Lock()

        # Track pods we've already pushed into the queue and pods we've bound.
        self._enqueued_pods: set[str] = set()
        self._bound_pods: set[str] = set()
        # Map task_instance_id (== pod name) -> pod spec for binding.
        self._pending_pods: Dict[str, object] = {}
        self._placement_context: Dict[str, Dict[str, Optional[float]]] = {}
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
    def _pod_to_models(
            self, pod) -> tuple[TaskInstance, TaskTemplate, str,
                               PriorityClass, WorkflowClass, bool]:
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
        workflow_class = _WORKFLOW_CLASS_MAP.get(
            annotations.get("ts.scheduler/workflow_class", "BATCH"),
            WorkflowClass.BATCH,
        )
        preemptable = annotations.get(
            "ts.scheduler/preemptable", "true").lower() == "true"

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
        try:
            dependencies = json.loads(
                annotations.get("ts.scheduler/data_dependencies", "[]"))
            for dependency in dependencies:
                producer = str(dependency.get("node", ""))
                size_bytes = max(0, int(dependency.get("bytes", 0)))
                if not producer:
                    continue
                if producer not in task_instance.parent_node_ids:
                    task_instance.parent_node_ids.append(producer)
                task_instance.input_bytes_by_node[producer] = (
                    task_instance.input_bytes_by_node.get(producer, 0)
                    + size_bytes)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

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

        return (task_instance, template, wf_id, priority, workflow_class,
                preemptable)

    def _ensure_workflow(
            self, wf_id: str, priority: PriorityClass,
            workflow_class: WorkflowClass,
            preemptable: bool) -> WorkflowInstance:
        """Find-or-create an in-memory WorkflowInstance for the engine."""
        wf = self.queue.admitted_workflows.get(wf_id)
        if wf is not None:
            return wf

        wf = WorkflowInstance(
            workflow_instance_id=wf_id,
            workflow_template_id=wf_id,
            workflow_class=workflow_class,
            priority=priority,
            preemptable=preemptable,
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
                workflow_class=workflow_class,
                default_priority=priority,
                default_preemptable=preemptable,
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
                (task, template, wf_id, priority, workflow_class,
                 preemptable) = self._pod_to_models(pod)
            except Exception as e:
                print(f"[SCHED] failed to parse pod {name}: {e}")
                return

            wf = self._ensure_workflow(
                wf_id, priority, workflow_class, preemptable)
            # Register the template into the workflow's template map so the
            # engine can find runtime/memory specs.
            self.templates[wf_id].tasks[template.task_template_id] = template
            # Add to the workflow's task list (used by readiness/gang logic).
            existing = wf.task_instances.get(task.task_instance_id)
            if existing is not None:
                existing.task_template_id = task.task_template_id
                existing.state = TaskState.WAITING
                existing.assigned_node_id = None
                task = existing
            else:
                wf.task_instances[task.task_instance_id] = task

            # Directly enqueue as ready — the controller already resolved
            # the DAG and only creates pods for tasks whose parents finished.
            self.queue.enqueue_ready_tasks([task], wf)
            self._enqueued_pods.add(name)
            self._pending_pods[name] = pod
            print(f"[SCHED] Enqueued '{name}' "
                  f"(wf={wf_id}, class={template.task_class.name}, prio={priority.name})")

    def preempt_pod(self, pod_name: str) -> bool:
        with self._state_lock:
            self._bound_pods.discard(pod_name)
            self._enqueued_pods.discard(pod_name)
            self._pending_pods.pop(pod_name, None)
            self._placement_context.pop(pod_name, None)
        try:
            self.core.delete_namespaced_pod(
                name=pod_name,
                namespace=NAMESPACE,
                grace_period_seconds=0,
                body=client.V1DeleteOptions(grace_period_seconds=0),
            )
            return True
        except ApiException as exc:
            print(f"[PREEMPT ERROR] '{pod_name}': {exc.reason}")
            return False

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
        temperature_at_end = None
        with self._cluster_lock:
            if self._cluster:
                for n in self._cluster.nodes:
                    if n.node_id == node_name:
                        node_type = n.node_type
                        temperature_at_end = n.cpu_temperature
                        n.unregister_task(pod.metadata.name)
                        break

        context = self._placement_context.pop(pod.metadata.name, {})
        metrics = self._read_task_metrics(pod.metadata.name)
        output_bytes = {
            str(field): int(size)
            for field, size in metrics.get("output_bytes_by_field", {}).items()
            if isinstance(size, (int, float)) and size >= 0
        }
        task = next(
            (
                workflow.task_instances[pod.metadata.name]
                for workflow in self.queue.admitted_workflows.values()
                if pod.metadata.name in workflow.task_instances
            ),
            TaskInstance(
                task_instance_id=pod.metadata.name,
                workflow_instance_id=wf_id,
                task_template_id=task_template_id,
                state=TaskState.RUNNING,
                assigned_node_id=node_name,
            ),
        )

        print(f"[SCHED] '{pod.metadata.name}' {pod.status.phase} on {node_name} "
              f"(runtime={runtime:.2f}s, startup={startup:.2f}s)")

        if pod.status.phase == "Succeeded" and node_type is not None:
            self.observer.record_task_completion(
                task=task,
                actual_runtime=runtime,
                actual_startup=startup,
                node_id=node_name,
                node_type=node_type,
                node_cpu_at_start=context.get("cpu_usage_ratio", 0.0),
                node_memory_at_start=context.get("memory_usage_ratio", 0.0),
                temperature_at_start=context.get("cpu_temperature"),
                temperature_at_end=temperature_at_end,
                io_bytes_read=int(metrics.get("io_bytes_read", 0)),
                io_bytes_written=int(metrics.get("io_bytes_written", 0)),
                output_bytes_by_field=output_bytes,
                transfer_seconds=float(metrics.get("transfer_seconds", 0.0)),
            )
            self._record_data_placement(wf_id, task_name, node_name,
                                        pod.metadata.name, output_bytes)
            self._dirty = True
        elif pod.status.phase == "Failed":
            self.observer.record_task_failure(
                task=task, node_id=node_name, node_type=node_type,
            )
            self._dirty = True

    def _read_task_metrics(self, pod_name: str) -> dict:
        try:
            log = self.core.read_namespaced_pod_log(
                name=pod_name, namespace=NAMESPACE)
            for line in reversed(log.splitlines()):
                if line.startswith("__TS_METRICS__="):
                    payload = json.loads(line[len("__TS_METRICS__="):])
                    return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            print(f"[SCHED] metrics unavailable for {pod_name}: {exc}")
        return {}

    def _record_data_placement(self, wf_id: str, task_name: str,
                               node_name: str, pod_name: str,
                               output_bytes: Dict[str, int]):
        try:
            fields = output_bytes or {"__bulk__": 0}
            for field_name, size_bytes in fields.items():
                self.data_placement.record_output(
                    wfid=wf_id, task_id=task_name,
                    field_name=field_name,
                    node_id=node_name, size_bytes=size_bytes,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bind — pull dispatch decisions out of the engine and apply to K8s
    # ------------------------------------------------------------------
    def bind_pod(self, pod_name: str, node_name: str):
        context: Dict[str, Optional[float]] = {}
        with self._cluster_lock:
            if self._cluster:
                node = next(
                    (item for item in self._cluster.nodes if item.node_id == node_name),
                    None,
                )
                if node is not None:
                    context = {
                        "cpu_usage_ratio": node.cpu_usage_ratio,
                        "memory_usage_ratio": node.memory_usage_ratio,
                        "cpu_temperature": node.cpu_temperature,
                    }
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
            self._placement_context[pod_name] = context
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
