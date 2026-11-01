"""
ts-controller — the workflow lifecycle controller.

Runs inside the cluster as a Deployment. Watches Workflow CRDs and creates a
Pod for each task whose parents have finished. When a task Pod succeeds it
scrapes the Channel A output (``__TS_OUTPUT__=...``) from the Pod log and
injects declared fields as environment variables into child Pods. It retries
interrupted (deleted) tasks, propagates parent failures to descendants, and
writes Workflow status.

Each Pod carries the scheduler contract as ``ts.scheduler/*`` annotations,
including the complete workflow DAG so the scheduler can compute HEFT upward
ranks, and the gang size so gangs are only placed when complete.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────
SCHEDULER_NAME = os.environ.get("TS_POD_SCHEDULER_NAME", "ts-scheduler")
CRD_GROUP = "ts.io"
CRD_VERSION = "v1"
WORKFLOW_PLURAL = "workflows"
TASKTEMPLATE_PLURAL = "tasktemplates"
LABEL_WORKFLOW = "ts.io/workflow"
LABEL_TASK_NAME = "ts.io/task-name"
LABEL_MANAGED_BY = "ts.io/managed-by"
ANNOT_PRIORITY = "ts.scheduler/priority"
EMULATION_CONFIGMAP = os.environ.get("TS_EMULATION_CONFIGMAP", "ts-emulation")

NAMESPACE = os.environ.get("TS_NAMESPACE", "default")
RECONCILE_INTERVAL_S = float(os.environ.get("TS_RECONCILE_INTERVAL_S", "2"))
_DNS_INVALID = re.compile(r"[^a-z0-9-]+")


# ─────────────────────────────────────────────────────────────────────────
# In-memory state for each Workflow we are managing.
# ─────────────────────────────────────────────────────────────────────────
class _TaskState:
    __slots__ = ("name", "template_name", "depends_on", "state",
                 "pod_name", "node_name", "node_ip", "ready_at",
                 "pod_created_at", "started_at", "finished_at", "outputs",
                 "attempts")

    def __init__(self, name: str, template_name: str,
                 depends_on: List[Dict[str, Any]]):
        self.name = name
        self.template_name = template_name
        # depends_on: list of {parent, dependencyType, dataFields}
        self.depends_on = depends_on
        self.state = "WAITING"
        self.pod_name: Optional[str] = None
        self.node_name: Optional[str] = None
        self.node_ip: Optional[str] = None
        self.ready_at: Optional[str] = None
        self.pod_created_at: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.outputs: Dict[str, Any] = {}
        self.attempts = 0


class _WorkflowState:
    def __init__(self, wf_obj: Dict[str, Any]):
        self.uid: str = wf_obj["metadata"]["uid"]
        self.name: str = wf_obj["metadata"]["name"]
        self.namespace: str = wf_obj["metadata"]["namespace"]
        self.generation: int = wf_obj["metadata"].get("generation", 0)
        self.priority: str = wf_obj["spec"].get("priority", "BATCH")
        self.preemptable: bool = wf_obj["spec"].get("preemptable", True)
        self.workflow_class: str = wf_obj["spec"].get("workflowClass", "BATCH")
        self.tasks: Dict[str, _TaskState] = {}
        for t in wf_obj["spec"]["tasks"]:
            self.tasks[t["name"]] = _TaskState(
                name=t["name"],
                template_name=t["template"],
                depends_on=t.get("dependsOn", []),
            )
        self.state = "ADMITTED"
        # Sub-second admission time on the cluster clock; creationTimestamp is
        # truncated to whole seconds by the API server.
        self.admitted_at: str = _now_iso()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.message: str = ""
        self.final_status_pushed = False


# ─────────────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────────────
class WorkflowController:
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()
        self.workflows: Dict[str, _WorkflowState] = {}     # uid -> state
        self.template_cache: Dict[str, Dict[str, Any]] = {}  # name -> spec
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # TaskTemplate lookup
    # ------------------------------------------------------------------
    def _get_template(self, name: str) -> Optional[Dict[str, Any]]:
        cache = getattr(self, "template_cache", None)
        if cache is None:
            return None
        cached = cache.get(name)
        if cached:
            return cached
        api = getattr(self, "custom", None)
        if api is None:
            return None
        try:
            obj = api.get_namespaced_custom_object(
                group=CRD_GROUP, version=CRD_VERSION,
                namespace=NAMESPACE, plural=TASKTEMPLATE_PLURAL, name=name,
            )
            cache[name] = obj["spec"]
            return obj["spec"]
        except ApiException as e:
            if e.status == 404:
                print(f"[CTRL] TaskTemplate '{name}' not found")
            return None

    # ------------------------------------------------------------------
    # Pod construction
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_pod_name(workflow_name: str, task_name: str, wf_uid: str) -> str:
        """Deterministic, collision-free DNS-1123 Pod name (<= 63 chars).

        The readable prefix may be truncated, so a digest of the workflow UID
        and the full task name guarantees uniqueness per workflow task while
        keeping the name stable across retries (needed for adoption).
        """
        digest = hashlib.sha1(
            f"{wf_uid}/{task_name}".encode("utf-8")).hexdigest()[:10]
        readable = _DNS_INVALID.sub(
            "-", f"{workflow_name}-{task_name}".lower()).strip("-")
        readable = readable[:63 - len(digest) - 1].rstrip("-")
        return f"{readable}-{digest}" if readable else digest

    def _workflow_dag(self, wf: _WorkflowState) -> Dict[str, Any]:
        """Compact DAG (task -> template, typed edges with byte hints)."""
        edges = []
        for task in wf.tasks.values():
            for dependency in task.depends_on:
                parent = wf.tasks.get(dependency.get("parent"))
                if parent is None:
                    continue
                kind = dependency.get("dependencyType", "DATA")
                size = 0
                if kind == "DATA":
                    template = self._get_template(parent.template_name) or {}
                    try:
                        size = int(template.get("expectedOutputBytes", 0))
                    except (TypeError, ValueError):
                        size = 0
                edges.append([parent.name, task.name, kind, size])
        return {
            "tasks": {name: task.template_name for name, task in wf.tasks.items()},
            "edges": edges,
        }

    def _gang_size(self, wf: _WorkflowState, gang_id: str) -> int:
        count = 0
        for task in wf.tasks.values():
            template = self._get_template(task.template_name) or {}
            if template.get("gangGroupId") == gang_id:
                count += 1
        return count

    def _build_pod(self, wf: _WorkflowState, task: _TaskState,
                   tmpl_spec: Dict[str, Any], env_vars: Dict[str, str]) -> client.V1Pod:
        pod_name = self._safe_pod_name(wf.name, task.name, wf.uid)

        logical_cpu = tmpl_spec["cpuRequest"]
        logical_mem = tmpl_spec["memoryRequest"]
        physical = tmpl_spec.get("physicalResources") or {}
        physical_cpu = physical.get("cpuRequest", logical_cpu)
        physical_mem = physical.get("memoryRequest", logical_mem)
        cpu_limit = physical.get("cpuLimit", physical_cpu)
        mem_limit = physical.get("memoryLimit", physical_mem)
        emulation = tmpl_spec.get("emulation") or {}
        work_scale = float(emulation.get("workScale", 1.0))
        time_scale = float(emulation.get("timeScale", 1.0))
        if work_scale <= 0 or time_scale <= 0:
            raise ValueError("emulation scales must be positive")
        compat = ",".join(tmpl_spec.get("compatibleNodeTypes", ["GENERAL"]))

        annotations = {
            "ts.scheduler/task_template_id": tmpl_spec.get("_template_name", task.template_name),
            "ts.scheduler/task_name": task.name,
            "ts.scheduler/task_class": tmpl_spec["taskClass"],
            "ts.scheduler/compatible_node_types": compat,
            "ts.scheduler/workflow_instance_id": wf.name,
            "ts.scheduler/expected_output_bytes":
                str(tmpl_spec.get("expectedOutputBytes", 0)),
            "ts.scheduler/priority": wf.priority,
            "ts.scheduler/workflow_class": wf.workflow_class,
            "ts.scheduler/preemptable": str(wf.preemptable).lower(),
            "ts.scheduler/logical_cpu_request": str(logical_cpu),
            "ts.scheduler/logical_memory_request": str(logical_mem),
            "ts.scheduler/work_scale": str(work_scale),
            "ts.scheduler/time_scale": str(time_scale),
            "ts.scheduler/workflow_dag": json.dumps(
                self._workflow_dag(wf), separators=(",", ":")),
        }
        if tmpl_spec.get("gangGroupId"):
            annotations["ts.scheduler/gang_group_id"] = tmpl_spec["gangGroupId"]
            annotations["ts.scheduler/gang_size"] = str(
                self._gang_size(wf, tmpl_spec["gangGroupId"]) or 1)
        if tmpl_spec.get("checkpointable"):
            annotations["ts.scheduler/checkpointable"] = "true"

        data_dependencies = []
        for dependency in task.depends_on:
            if dependency.get("dependencyType", "DATA") != "DATA":
                continue
            parent = wf.tasks.get(dependency["parent"])
            if parent is None or not parent.node_name:
                continue
            expected_bytes = 0
            parent_template = getattr(self, "template_cache", {}).get(
                parent.template_name, {})
            try:
                expected_bytes = int(
                    parent.outputs.get(
                        "bytes_written",
                        parent_template.get("expectedOutputBytes", 0),
                    ))
            except (TypeError, ValueError):
                pass
            data_dependencies.append({
                "parent": parent.name,
                "node": parent.node_name,
                "bytes": max(0, expected_bytes),
            })
        if data_dependencies:
            annotations["ts.scheduler/data_dependencies"] = json.dumps(
                data_dependencies, separators=(",", ":"))

        labels = {
            LABEL_WORKFLOW: wf.name,
            LABEL_TASK_NAME: task.name,
            LABEL_MANAGED_BY: "ts-controller",
        }

        env = [client.V1EnvVar(name=str(k), value=str(v))
               for k, v in (tmpl_spec.get("env") or {}).items()]
        env.extend(client.V1EnvVar(name=k, value=v) for k, v in env_vars.items())
        env.extend([
            client.V1EnvVar(name="TS_WORKFLOW_ID", value=wf.name),
            client.V1EnvVar(name="TS_TASK_INSTANCE_ID", value=pod_name),
            client.V1EnvVar(name="TS_TASK_NAME", value=task.name),
            client.V1EnvVar(name="TS_TASK_CLASS", value=tmpl_spec["taskClass"]),
            client.V1EnvVar(name="TS_WORK_SCALE", value=str(work_scale)),
            client.V1EnvVar(name="TS_TIME_SCALE", value=str(time_scale)),
            client.V1EnvVar(name="TS_INPUTS_DIR", value="/data/inputs"),
            client.V1EnvVar(
                name="TS_OUTPUTS_DIR",
                value=f"/data/outputs/{wf.name}/{pod_name}",
            ),
            # Emulated node speed: the image learns which node it landed on
            # and reads that node's speed factors from an optional ConfigMap.
            client.V1EnvVar(
                name="TS_NODE_NAME",
                value_from=client.V1EnvVarSource(
                    field_ref=client.V1ObjectFieldSelector(
                        field_path="spec.nodeName"))),
            client.V1EnvVar(
                name="TS_NODE_SPEEDS",
                value_from=client.V1EnvVarSource(
                    config_map_key_ref=client.V1ConfigMapKeySelector(
                        name=EMULATION_CONFIGMAP, key="node-speeds",
                        optional=True))),
        ])

        mounts = [
            client.V1VolumeMount(name="inputs", mount_path="/data/inputs"),
            client.V1VolumeMount(name="outputs", mount_path="/data/outputs"),
        ]
        volumes = [
            client.V1Volume(name="inputs",
                             empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(name="outputs",
                             host_path=client.V1HostPathVolumeSource(
                                 path="/var/lib/ts-data",
                                 type="DirectoryOrCreate")),
        ]

        container = client.V1Container(
            name="worker",
            image=tmpl_spec["image"],
            image_pull_policy=tmpl_spec.get("imagePullPolicy", "IfNotPresent"),
            command=tmpl_spec.get("command") or None,
            args=tmpl_spec.get("args") or None,
            env=env,
            resources=client.V1ResourceRequirements(
                requests={
                    "cpu": str(physical_cpu), "memory": str(physical_mem)},
                limits={"cpu": str(cpu_limit), "memory": str(mem_limit)},
            ),
            volume_mounts=mounts,
        )

        owner_ref = client.V1OwnerReference(
            api_version=f"{CRD_GROUP}/{CRD_VERSION}",
            kind="Workflow",
            name=wf.name,
            uid=wf.uid,
            controller=True,
            block_owner_deletion=True,
        )

        return client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=wf.namespace,
                labels=labels,
                annotations=annotations,
                owner_references=[owner_ref],
            ),
            spec=client.V1PodSpec(
                scheduler_name=SCHEDULER_NAME,
                restart_policy="Never",
                containers=[container],
                volumes=volumes,
            ),
        )

    # ------------------------------------------------------------------
    # DAG -> ready tasks
    # ------------------------------------------------------------------
    @staticmethod
    def _is_task_ready(task: _TaskState, wf: _WorkflowState) -> bool:
        if task.state != "WAITING":
            return False
        for dep in task.depends_on:
            parent = wf.tasks.get(dep["parent"])
            if not parent or parent.state != "FINISHED":
                return False
        return True

    @staticmethod
    def _gather_env(task: _TaskState, wf: _WorkflowState) -> Dict[str, str]:
        """Build the env-var bundle for a child task.

        * Channel A: ``<field>=<value>`` for each declared output field.
        * Channel B: ``TS_PARENT_<name>_NODE``, ``_OUTPUTS_DIR`` and
          ``_FILESERVER_URL`` so the task can read a same-node parent's files
          directly or fetch a remote parent's files from its fileserver.
        """
        env: Dict[str, str] = {}
        fileserver_port = os.environ.get("TS_FILESERVER_PORT", "8080")
        for dep in task.depends_on:
            parent = wf.tasks.get(dep["parent"])
            if not parent:
                continue
            for field in dep.get("dataFields", []):
                if field in parent.outputs:
                    env[field] = str(parent.outputs[field])
            if parent.node_name and parent.pod_name:
                key = re.sub(r"[^A-Z0-9_]", "_", parent.name.upper())
                env[f"TS_PARENT_{key}_NODE"] = parent.node_name
                env[f"TS_PARENT_{key}_OUTPUTS_DIR"] = (
                    f"/{wf.name}/{parent.pod_name}"
                )
                env[f"TS_PARENT_{key}_FILESERVER_URL"] = (
                    f"http://{parent.node_ip or parent.node_name}:{fileserver_port}"
                    f"/{wf.name}/{parent.pod_name}"
                )
        return env

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------
    def _reconcile_workflow(self, wf: _WorkflowState):
        """Create pods for any task whose parents are FINISHED."""
        for task in wf.tasks.values():
            if not self._is_task_ready(task, wf):
                continue
            tmpl = self._get_template(task.template_name)
            if tmpl is None:
                task.state = "FAILED"
                wf.message = f"unknown TaskTemplate '{task.template_name}'"
                continue
            tmpl = dict(tmpl)
            tmpl["_template_name"] = task.template_name
            if task.ready_at is None:
                task.ready_at = _now_iso()
            env_vars = self._gather_env(task, wf)
            try:
                pod = self._build_pod(wf, task, tmpl, env_vars)
            except (KeyError, TypeError, ValueError) as exc:
                task.state = "FAILED"
                wf.message = f"invalid TaskTemplate '{task.template_name}': {exc}"
                continue
            try:
                self.core.create_namespaced_pod(namespace=wf.namespace, body=pod)
                task.pod_name = pod.metadata.name
                task.pod_created_at = _now_iso()
                task.attempts += 1
                task.state = "READY"
                print(f"[CTRL] Created pod '{pod.metadata.name}' "
                      f"({wf.name}/{task.name})")
                if wf.started_at is None:
                    wf.started_at = _now_iso()
                wf.state = "RUNNING"
            except ApiException as e:
                if e.status == 409:
                    # The Pod already exists (controller restart, or the
                    # previous attempt is still terminating). Adopt it; a
                    # DELETED event for a terminating Pod resets the task.
                    task.pod_name = pod.metadata.name
                    task.state = "READY"
                else:
                    print(f"[CTRL] create pod failed: {e.reason}")
                    task.state = "FAILED"
                    wf.message = f"create pod failed for {task.name}: {e.reason}"

        self._check_terminal(wf)
        self._patch_status(wf)

    def _check_terminal(self, wf: _WorkflowState):
        changed = True
        while changed:
            changed = False
            for task in wf.tasks.values():
                if task.state != "WAITING":
                    continue
                failed_parent = next(
                    (
                        wf.tasks.get(dependency["parent"])
                        for dependency in task.depends_on
                        if (wf.tasks.get(dependency["parent"]) is not None
                            and wf.tasks[dependency["parent"]].state == "FAILED")
                    ),
                    None,
                )
                if failed_parent is None:
                    continue
                task.state = "FAILED"
                task.finished_at = _now_iso()
                wf.message = (
                    f"task '{task.name}' blocked by failed parent "
                    f"'{failed_parent.name}'")
                changed = True

        states = [t.state for t in wf.tasks.values()]
        if all(s in ("FINISHED", "FAILED") for s in states):
            if any(s == "FAILED" for s in states):
                wf.state = "FAILED"
            else:
                wf.state = "FINISHED"
            if wf.finished_at is None:
                finished = [t.finished_at for t in wf.tasks.values() if t.finished_at]
                wf.finished_at = max(finished, key=_parse_iso) if finished else _now_iso()

    # ------------------------------------------------------------------
    # Pod completion handling
    # ------------------------------------------------------------------
    def _on_pod_event(self, pod, event_type: str = "MODIFIED"):
        labels = (pod.metadata.labels or {})
        wf_name = labels.get(LABEL_WORKFLOW)
        task_name = labels.get(LABEL_TASK_NAME)
        if not wf_name or not task_name:
            return

        with self._lock:
            wf = next((w for w in self.workflows.values() if w.name == wf_name), None)
            if wf is None:
                return
            task = wf.tasks.get(task_name)
            if task is None:
                return

            if event_type == "DELETED":
                if task.state not in ("FINISHED", "FAILED"):
                    task.state = "WAITING"
                    task.pod_name = None
                    task.node_name = None
                    task.node_ip = None
                    task.started_at = None
                    task.finished_at = None
                    print(f"[CTRL] Retrying interrupted task "
                          f"'{wf.name}/{task.name}'")
                return

            phase = pod.status.phase
            if pod.spec.node_name and task.node_name is None:
                task.node_name = pod.spec.node_name
            if getattr(pod.status, "host_ip", None) and task.node_ip is None:
                task.node_ip = pod.status.host_ip
            if pod.status.start_time and task.started_at is None:
                task.started_at = pod.status.start_time.isoformat()

            if phase == "Running" and task.state == "READY":
                task.state = "RUNNING"
            elif phase == "Succeeded" and task.state not in ("FINISHED", "FAILED"):
                task.finished_at = _now_iso()
                task.outputs = self._extract_outputs(pod.metadata.name, wf.namespace)
                required_fields = {
                    field
                    for child in wf.tasks.values()
                    for dependency in child.depends_on
                    if dependency.get("parent") == task.name
                    and dependency.get("dependencyType", "DATA") == "DATA"
                    for field in dependency.get("dataFields", [])
                }
                missing_fields = sorted(required_fields - task.outputs.keys())
                if missing_fields:
                    task.state = "FAILED"
                    wf.message = (
                        f"task '{task.name}' omitted required outputs: "
                        f"{', '.join(missing_fields)}"
                    )
                    print(f"[CTRL] Task '{wf.name}/{task.name}' FAILED: "
                          f"missing outputs={missing_fields}")
                else:
                    task.state = "FINISHED"
                    print(f"[CTRL] Task '{wf.name}/{task.name}' FINISHED "
                          f"on {task.node_name} outputs={list(task.outputs)}")
            elif phase == "Failed" and task.state not in ("FINISHED", "FAILED"):
                task.state = "FAILED"
                task.finished_at = _now_iso()
                print(f"[CTRL] Task '{wf.name}/{task.name}' FAILED on {task.node_name}")
                wf.message = f"task '{task.name}' failed"

    def _extract_outputs(self, pod_name: str, ns: str, *, attempts: int = 5,
                         retry_seconds: float = 0.2) -> Dict[str, Any]:
        """Pull the JSON object after ``__TS_OUTPUT__=`` from the Pod log."""
        for attempt in range(attempts):
            try:
                log = self.core.read_namespaced_pod_log(
                    name=pod_name, namespace=ns)
                for line in log.splitlines():
                    if line.startswith("__TS_OUTPUT__="):
                        payload = json.loads(line[len("__TS_OUTPUT__="):])
                        if isinstance(payload, dict):
                            return payload
                        print(f"[CTRL] output marker for {pod_name} is not a "
                              f"JSON object; ignoring it")
                        return {}
            except ApiException as e:
                print(f"[CTRL] log read failed for {pod_name}: {e.reason}")
            except (ValueError, TypeError) as e:
                print(f"[CTRL] output parse failed for {pod_name}: {e}")
                return {}
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
        return {}

    # ------------------------------------------------------------------
    # Status subresource update
    # ------------------------------------------------------------------
    def _status_body(self, wf: _WorkflowState) -> Dict[str, Any]:
        total = len(wf.tasks)
        finished = sum(1 for t in wf.tasks.values() if t.state == "FINISHED")
        failed = sum(1 for t in wf.tasks.values() if t.state == "FAILED")
        return {
            "state": wf.state,
            "admittedAt": wf.admitted_at,
            "tasksTotal": total,
            "tasksFinished": finished,
            "tasksFailed": failed,
            "startedAt": wf.started_at or "",
            "finishedAt": wf.finished_at or "",
            "message": wf.message,
            "tasks": [
                {
                    "name": t.name,
                    "state": t.state,
                    "podName": t.pod_name or "",
                    "nodeName": t.node_name or "",
                    "readyAt": t.ready_at or "",
                    "podCreatedAt": t.pod_created_at or "",
                    "startedAt": t.started_at or "",
                    "finishedAt": t.finished_at or "",
                    "attempts": t.attempts,
                    "runtimeSeconds": _runtime_s(t),
                }
                for t in wf.tasks.values()
            ],
        }

    def _patch_status(self, wf: _WorkflowState) -> bool:
        try:
            self.custom.patch_namespaced_custom_object_status(
                group=CRD_GROUP, version=CRD_VERSION, namespace=wf.namespace,
                plural=WORKFLOW_PLURAL, name=wf.name,
                body={"status": self._status_body(wf)},
            )
            return True
        except ApiException as e:
            if e.status == 404:
                with self._lock:
                    self.workflows.pop(wf.uid, None)
            else:
                print(f"[CTRL] status patch failed for {wf.name}: {e.reason}")
            return False

    # ------------------------------------------------------------------
    # Watch loops
    # ------------------------------------------------------------------
    def _watch_workflows(self):
        print("[CTRL] Watching Workflow CRDs...")
        w = watch.Watch()
        while True:
            try:
                stream = w.stream(
                    self.custom.list_namespaced_custom_object,
                    group=CRD_GROUP, version=CRD_VERSION,
                    namespace=NAMESPACE, plural=WORKFLOW_PLURAL,
                    timeout_seconds=300,
                )
                for ev in stream:
                    try:
                        obj = ev["object"]
                        etype = ev["type"]
                        uid = obj.get("metadata", {}).get("uid")
                        if not uid:
                            continue
                        with self._lock:
                            if etype in ("ADDED", "MODIFIED"):
                                if uid not in self.workflows:
                                    self.workflows[uid] = _WorkflowState(obj)
                                    print(f"[CTRL] Admitted workflow "
                                          f"'{obj['metadata']['name']}'")
                            elif etype == "DELETED":
                                self.workflows.pop(uid, None)
                                print(f"[CTRL] Workflow "
                                      f"'{obj['metadata']['name']}' deleted")
                    except Exception as event_error:
                        print(f"[CTRL] workflow event failed: {event_error}")
            except Exception as e:
                print(f"[CTRL] workflow watch error: {e}; retrying in 5s")
                time.sleep(5)

    def _watch_pods(self):
        print("[CTRL] Watching managed Pods...")
        w = watch.Watch()
        label_selector = f"{LABEL_MANAGED_BY}=ts-controller"
        while True:
            try:
                stream = w.stream(
                    self.core.list_namespaced_pod,
                    namespace=NAMESPACE,
                    label_selector=label_selector,
                    timeout_seconds=300,
                )
                for ev in stream:
                    try:
                        self._on_pod_event(ev["object"], ev["type"])
                    except Exception as event_error:
                        # One malformed Pod must not stop the watch (it would
                        # be replayed and fail again on every re-list).
                        print(f"[CTRL] pod event failed: {event_error}")
            except Exception as e:
                print(f"[CTRL] pod watch error: {e}; retrying in 5s")
                time.sleep(5)

    def _reconcile_loop(self):
        while True:
            try:
                with self._lock:
                    wfs = list(self.workflows.values())
                for wf in wfs:
                    push_final_status = False
                    with self._lock:
                        if wf.uid not in self.workflows:
                            continue
                        if wf.state not in ("FINISHED", "FAILED"):
                            self._reconcile_workflow(wf)
                        elif not wf.final_status_pushed:
                            push_final_status = True
                    # Terminal workflows get exactly one successful status
                    # push instead of a patch every reconcile interval.
                    if push_final_status and self._patch_status(wf):
                        wf.final_status_pushed = True
            except Exception as e:
                print(f"[CTRL] reconcile error: {e}")
            time.sleep(RECONCILE_INTERVAL_S)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self):
        threading.Thread(target=self._watch_workflows, daemon=True).start()
        threading.Thread(target=self._watch_pods, daemon=True).start()
        self._reconcile_loop()


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _parse_iso(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _runtime_s(t: _TaskState) -> float:
    if not t.started_at or not t.finished_at:
        return 0.0
    try:
        return (_parse_iso(t.finished_at) - _parse_iso(t.started_at)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def main():
    print("=" * 60)
    print("  ts-controller - Workflow CRD lifecycle controller")
    print("=" * 60)
    WorkflowController().run()


if __name__ == "__main__":
    main()
