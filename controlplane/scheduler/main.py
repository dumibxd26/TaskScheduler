"""
ts-scheduler — the placement brain.

Watches Pending Pods with ``schedulerName=ts-scheduler`` (created by the
ts-controller), runs the selected policy through ``SchedulerEngine`` once per
tick, and binds each placement through the Pod binding subresource.

The scheduler owns placement, binding, victim deletion for preemption,
profile learning, and producer-location bookkeeping. It does not create Pods
or advance the DAG; ts-controller does. Consequently the engine runs with
dependency resolution disabled: a task becomes schedulable only when its Pod
appears, and a preempted task waits for the controller to recreate its Pod.

Concurrency: the Pod watch, the completion watch, the cluster refresh, and the
tick loop all mutate the same in-memory model, so every mutation happens
under one re-entrant lock. Kubernetes reads for the refresh happen outside it.
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

from engine import BIND_DROP, BIND_OK, BIND_RETRY, SchedulerEngine
from models.cluster import ClusterScenario
from models.enums import (NodeType, PriorityClass, TaskClass, TaskState,
                          WorkflowClass, WorkflowState)
from models.profile_store import ProfileStore
from models.workload import (TaskInstance, TaskTemplate, WorkflowInstance,
                             WorkflowTemplate)
from services.dag_metrics import compute_live_upward_ranks
from services.data_placement import DataPlacement
from services.observer import ExecutionObserver
from services.policy import build_policy
from services.quantities import cpu_cores, memory_mib
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
# How long a released Pod is remembered; far longer than any poll-to-merge race.
RELEASED_POD_MEMORY_S = 600.0
PERSIST_INTERVAL_S = float(os.environ.get("TS_PERSIST_INTERVAL_S", "60"))
PROFILE_PATH = os.environ.get("TS_PROFILE_PATH", "/data/profiles_learned.json")
CONFIGMAP_NAME = os.environ.get("TS_PROFILE_CONFIGMAP", "ts-scheduler-profiles")
WORKFLOW_IDLE_GC_S = float(os.environ.get("TS_WORKFLOW_IDLE_GC_S", "600"))
DEFAULT_BANDWIDTH_BYTES_S = 100.0 * 1024 * 1024

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
_PRIORITY_MAP = {item.name: item for item in PriorityClass}
_WORKFLOW_CLASS_MAP = {item.name: item for item in WorkflowClass}


# ─────────────────────────────────────────────────────────────────────────
# Helpers — pod parsing (kept for backward-compatible imports)
# ─────────────────────────────────────────────────────────────────────────
def _parse_cpu(raw) -> float:
    return cpu_cores(raw, 0.5)


def _parse_mem_mi(raw) -> float:
    try:
        return memory_mib(raw, 128.0)
    except ValueError:
        return 128.0


class TsScheduler:
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core = client.CoreV1Api()

        self.seed = int(os.environ.get("TS_RANDOM_SEED", "0"))
        random.seed(self.seed)
        print(f"[SCHED] Random seed: {self.seed}")

        self.store = ProfileStore()
        self.algorithm = PlacementAlgorithm(self.store)
        self.runner = WorkflowSchedulerRunner(self.store, self.algorithm)
        self.observer = ExecutionObserver(self.store)
        self.queue = QueueManager()
        self.resolver = ReadinessResolver()
        self.data_placement = DataPlacement()

        # Scheduler-side workflow templates synthesised from Pod metadata.
        self.templates: Dict[str, WorkflowTemplate] = {}

        self.policy = build_policy(runner=self.runner, seed=self.seed)
        self.policy_label = (os.environ.get("TS_POLICY") or "legacy").lower()
        print(f"[SCHED] Policy: {self.policy.name()} ({self.policy_label})")

        self.engine = SchedulerEngine(
            queue_manager=self.queue,
            resolver=self.resolver,
            runner=self.runner,
            templates=self.templates,
            policy=self.policy,
            data_placement=self.data_placement,
            preempt_task=self.preempt_pod,
            bind_task=self.bind_pod,
            resolve_dependencies=False,
        )

        self._lock = threading.RLock()
        # Older tests and helpers refer to these names.
        self._cluster_lock = self._lock
        self._state_lock = self._lock
        self._cluster: Optional[ClusterScenario] = None

        self._enqueued_pods: set[str] = set()
        self._bound_pods: set[str] = set()
        # Pods whose completion or preemption has been processed, with the
        # time it happened; refresh merges must not charge them again.
        self._released_pods: Dict[str, float] = {}
        self._pending_pods: Dict[str, object] = {}
        self._placement_context: Dict[str, Dict[str, Optional[float]]] = {}
        self._pod_task_names: Dict[str, str] = {}
        self._workflow_last_seen: Dict[str, float] = {}
        self._dirty = False

    # ------------------------------------------------------------------
    # Profile persistence — ConfigMap first, then the local file
    # ------------------------------------------------------------------
    def _load_profiles(self):
        loaded = False
        try:
            cm = self.core.read_namespaced_config_map(
                name=CONFIGMAP_NAME, namespace=NAMESPACE)
            raw = (cm.data or {}).get("profiles", "")
            if raw:
                self.store.load_json(raw)
                loaded = True
                print(f"[SCHED] Loaded {len(self.store.profiles)} profile(s) "
                      f"from ConfigMap {CONFIGMAP_NAME}")
        except ApiException as e:
            if e.status != 404:
                print(f"[SCHED] ConfigMap load failed: {e.reason}")

        if not loaded and os.path.exists(PROFILE_PATH):
            try:
                with open(PROFILE_PATH) as f:
                    self.store.load_json(f.read())
                print(f"[SCHED] Loaded {len(self.store.profiles)} profile(s) "
                      f"from {PROFILE_PATH}")
            except Exception as e:
                print(f"[SCHED] file load failed: {e}")

        if not self.store.profiles:
            print("[SCHED] No saved profiles - starting fresh")

    def _save_profiles(self):
        with self._lock:
            try:
                raw = self.store.to_json()
            except Exception as e:
                print(f"[SCHED] serialise profiles failed: {e}")
                return
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=CONFIGMAP_NAME),
            data={"profiles": raw},
        )
        try:
            self.core.replace_namespaced_config_map(
                name=CONFIGMAP_NAME, namespace=NAMESPACE, body=body)
        except ApiException as e:
            if e.status == 404:
                try:
                    self.core.create_namespaced_config_map(
                        namespace=NAMESPACE, body=body)
                except ApiException as e2:
                    print(f"[SCHED] ConfigMap create failed: {e2.reason}")
            else:
                print(f"[SCHED] ConfigMap save failed: {e.reason}")
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
                self._dirty = False
                self._save_profiles()

    # ------------------------------------------------------------------
    # Cluster state
    # ------------------------------------------------------------------
    def refresh_cluster_state(self):
        from services.k8s_cluster import merge_cluster_state, poll_cluster_inventory
        inventory = poll_cluster_inventory(self.core)
        with self._lock:
            horizon = time.time() - RELEASED_POD_MEMORY_S
            self._released_pods = {
                name: at for name, at in self._released_pods.items() if at >= horizon}
            self._cluster = merge_cluster_state(
                self._cluster, inventory, released=self._released_pods)
            summary = ", ".join(
                f"{node.node_id}:{node.free_cpu:.2f}cpu/{node.running_tasks}t"
                for node in self._cluster.nodes)
        print(f"[CLUSTER] refreshed: {summary}")

    def _cluster_refresh_loop(self):
        while True:
            time.sleep(CLUSTER_REFRESH_S)
            try:
                self.refresh_cluster_state()
            except Exception as e:
                print(f"[SCHED] cluster refresh failed: {e}")

    def _mean_bandwidth_bytes_s(self) -> float:
        if self._cluster and self._cluster.bandwidth_matrix:
            values = list(self._cluster.bandwidth_matrix.values())
            return sum(values) / len(values)
        return DEFAULT_BANDWIDTH_BYTES_S

    # ------------------------------------------------------------------
    # Pod -> internal model
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
            TaskClass.CPU_BOUND)
        priority = _PRIORITY_MAP.get(
            annotations.get("ts.scheduler/priority", "BATCH"), PriorityClass.BATCH)
        workflow_class = _WORKFLOW_CLASS_MAP.get(
            annotations.get("ts.scheduler/workflow_class", "BATCH"),
            WorkflowClass.BATCH)
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
        cpu_req = _parse_cpu(annotations.get(
            "ts.scheduler/logical_cpu_request", reqs.get("cpu", "0.5")))
        mem_req = _parse_mem_mi(annotations.get(
            "ts.scheduler/logical_memory_request", reqs.get("memory", "128Mi")))

        task_instance = TaskInstance(
            task_instance_id=pod.metadata.name,
            workflow_instance_id=wf_id,
            task_template_id=task_template_id,
            task_name=task_name,
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
        except (TypeError, ValueError, AttributeError):
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
            template.expected_output_bytes = int(
                annotations.get("ts.scheduler/expected_output_bytes", "0"))
        except (TypeError, ValueError):
            pass
        if annotations.get("ts.scheduler/gang_group_id"):
            template.gang_group_id = annotations["ts.scheduler/gang_group_id"]
            try:
                task_instance.gang_size = int(
                    annotations.get("ts.scheduler/gang_size", "0")) or None
            except (TypeError, ValueError):
                task_instance.gang_size = None
        if annotations.get("ts.scheduler/checkpointable") == "true":
            template.checkpointable = True

        raw_dag = annotations.get("ts.scheduler/workflow_dag")
        if raw_dag:
            try:
                dag = json.loads(raw_dag)
                ranks = compute_live_upward_ranks(
                    dag, self.store, self._mean_bandwidth_bytes_s())
                task_instance.upward_rank = ranks.get(task_name, 0.0)
                templates_by_task = dag.get("tasks", {})
                task_instance.children = [
                    (str(edge[1]), str(templates_by_task.get(edge[1], "")),
                     float(edge[3]) if len(edge) > 3 and str(edge[2]) == "DATA"
                     else 0.0)
                    for edge in dag.get("edges", [])
                    if str(edge[0]) == task_name
                ]
            except (TypeError, ValueError, KeyError, IndexError) as exc:
                print(f"[SCHED] invalid workflow DAG on {pod.metadata.name}: {exc}")

        return (task_instance, template, wf_id, priority, workflow_class,
                preemptable)

    def _ensure_workflow(
            self, wf_id: str, priority: PriorityClass,
            workflow_class: WorkflowClass,
            preemptable: bool) -> WorkflowInstance:
        """Find-or-create the scheduler-side workflow model."""
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
        # The controller already performed admission at the CRD level.
        wf.state = WorkflowState.ADMITTED
        wf.arrival_time = time.time()
        self.queue.admitted_workflows[wf_id] = wf
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
        with self._lock:
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
            self.templates[wf_id].tasks[template.task_template_id] = template
            existing = wf.task_instances.get(task.task_instance_id)
            if existing is not None:
                # A recreated Pod after preemption or failure: keep counters.
                existing.task_template_id = task.task_template_id
                existing.state = TaskState.WAITING
                existing.assigned_node_id = None
                existing.parent_node_ids = task.parent_node_ids
                existing.input_bytes_by_node = task.input_bytes_by_node
                existing.upward_rank = task.upward_rank
                existing.gang_size = task.gang_size
                existing.children = task.children
                task = existing
            else:
                wf.task_instances[task.task_instance_id] = task

            self.queue.enqueue_ready_tasks([task], wf)
            self._enqueued_pods.add(name)
            self._pending_pods[name] = pod
            self._pod_task_names[name] = task.task_name or task.task_template_id
            self._workflow_last_seen[wf_id] = time.time()
            print(f"[SCHED] Enqueued '{name}' (wf={wf_id}, "
                  f"class={template.task_class.name}, prio={priority.name}, "
                  f"rank={task.upward_rank:.2f})")

    def preempt_pod(self, pod_name: str) -> bool:
        with self._lock:
            self._bound_pods.discard(pod_name)
            self._enqueued_pods.discard(pod_name)
            self._pending_pods.pop(pod_name, None)
            self._placement_context.pop(pod_name, None)
            self._released_pods[pod_name] = time.time()
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
        print(f"[SCHED] Watching Pending pods with schedulerName={SCHEDULER_NAME}")
        w = watch.Watch()
        while True:
            try:
                stream = w.stream(
                    self.core.list_namespaced_pod,
                    namespace=NAMESPACE,
                    timeout_seconds=300,
                )
                for ev in stream:
                    try:
                        if ev["type"] == "DELETED":
                            self._forget_pod(ev["object"].metadata.name)
                            continue
                        pod = ev["object"]
                        if pod.spec.scheduler_name != SCHEDULER_NAME:
                            continue
                        if pod.status.phase != "Pending" or pod.spec.node_name:
                            continue
                        self._on_pending_pod(pod)
                    except Exception as event_error:
                        print(f"[SCHED] pod event failed: {event_error}")
            except Exception as e:
                print(f"[SCHED] pod watch error: {e}; retrying in 5s")
                time.sleep(5)

    def _forget_pod(self, pod_name: str):
        """Drop a deleted Pending Pod from the queue (cleanup or preemption)."""
        with self._lock:
            if pod_name not in self._pending_pods:
                return
            self._pending_pods.pop(pod_name, None)
            self._enqueued_pods.discard(pod_name)
            self.queue.remove_tasks({pod_name})

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
                    try:
                        pod = ev["object"]
                        if pod.spec.scheduler_name != SCHEDULER_NAME:
                            continue
                        if pod.status.phase not in ("Succeeded", "Failed"):
                            continue
                        with self._lock:
                            if pod.metadata.name not in self._bound_pods:
                                continue
                            self._bound_pods.discard(pod.metadata.name)
                        self._record_completion(pod)
                    except Exception as event_error:
                        print(f"[SCHED] completion event failed: {event_error}")
            except Exception as e:
                print(f"[SCHED] completion watch error: {e}; retrying in 5s")
                time.sleep(5)

    @staticmethod
    def _container_times(pod) -> tuple[float, float]:
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
        except (AttributeError, IndexError, TypeError):
            pass
        return runtime, startup

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

        # Kubelet timestamps have one-second resolution; the task images
        # measure their own work with perf_counter and report it in metrics.
        # Input staging is priced separately by the transfer estimate, so the
        # learned runtime excludes it.
        runtime, startup = self._container_times(pod)
        metrics = self._read_task_metrics(pod.metadata.name)
        try:
            measured = float(metrics.get("runtime_seconds", 0.0))
            staging = float(metrics.get("transfer_seconds", 0.0))
            if measured > 0:
                runtime = max(0.0, measured - max(0.0, staging))
        except (TypeError, ValueError):
            pass
        output_bytes = {
            str(field): int(size)
            for field, size in metrics.get("output_bytes_by_field", {}).items()
            if isinstance(size, (int, float)) and size >= 0
        }

        with self._lock:
            node_type = None
            temperature_at_end = None
            if self._cluster:
                for n in self._cluster.nodes:
                    if n.node_id == node_name:
                        node_type = n.node_type
                        temperature_at_end = n.cpu_temperature
                        n.unregister_task(pod.metadata.name)
                        self._released_pods[pod.metadata.name] = time.time()
                        break
            context = self._placement_context.pop(pod.metadata.name, {})
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
            print(f"[DONE] '{pod.metadata.name}' {pod.status.phase} on {node_name} "
                  f"(runtime={runtime:.3f}s, startup={startup:.2f}s) "
                  f"at={time.time():.3f}")
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
                    task=task, node_id=node_name, node_type=node_type)
                self._dirty = True
            self._gc_idle_workflows()

    def _gc_idle_workflows(self):
        """Forget scheduler-side workflows with no live tasks for a while."""
        now = time.time()
        for wf_id, workflow in list(self.queue.admitted_workflows.items()):
            if now - self._workflow_last_seen.get(wf_id, now) < WORKFLOW_IDLE_GC_S:
                continue
            if any(task.state not in (TaskState.FINISHED, TaskState.FAILED)
                   for task in workflow.task_instances.values()):
                continue
            del self.queue.admitted_workflows[wf_id]
            self.templates.pop(wf_id, None)
            self.data_placement.gc_workflow(wf_id)
            self._workflow_last_seen.pop(wf_id, None)

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
        fields = output_bytes or {"__bulk__": 0}
        for field_name, size_bytes in fields.items():
            self.data_placement.record_output(
                wfid=wf_id, task_id=task_name,
                field_name=field_name,
                node_id=node_name, size_bytes=size_bytes,
            )

    # ------------------------------------------------------------------
    # Bind — called by the engine for each placement (lock already held)
    # ------------------------------------------------------------------
    def bind_pod(self, pod_name: str, node_name: str) -> str:
        context: Dict[str, Optional[float]] = {}
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
        except ApiException as e:
            print(f"[BIND ERROR] '{pod_name}' -> '{node_name}': "
                  f"{e.status} {e.reason}")
            if e.status == 404:
                self._pending_pods.pop(pod_name, None)
                self._enqueued_pods.discard(pod_name)
                return BIND_DROP
            return BIND_RETRY
        self._placement_context[pod_name] = context
        self._pending_pods.pop(pod_name, None)
        self._enqueued_pods.discard(pod_name)
        self._bound_pods.add(pod_name)
        print(f"[BIND] '{pod_name}' -> '{node_name}' at={time.time():.3f}")
        return BIND_OK

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------
    def _tick_loop(self):
        while True:
            try:
                with self._lock:
                    if self._cluster is not None:
                        self.engine.run_tick(self._cluster)
            except Exception as e:
                print(f"[SCHED] tick error: {e}")
            time.sleep(TICK_INTERVAL_S)

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
    print("  ts-scheduler - workflow-aware placement")
    print("=" * 60)
    TsScheduler().run()


if __name__ == "__main__":
    main()
