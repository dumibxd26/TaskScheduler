"""
ts-controller — the workflow lifecycle controller.

Runs inside the cluster as a Deployment. Watches Workflow CRDs and creates
Pods for each ready task. When a task pod finishes successfully, it scrapes
the Channel A output (``__TS_OUTPUT__=...``) from the pod log and injects
those values as env vars into downstream child pods. Updates Workflow.status
on every state transition.

This replaces the old laptop-resident ``submit_workflows.py`` driver.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────
SCHEDULER_NAME = "ts-scheduler"
CRD_GROUP = "ts.io"
CRD_VERSION = "v1"
WORKFLOW_PLURAL = "workflows"
TASKTEMPLATE_PLURAL = "tasktemplates"
LABEL_WORKFLOW = "ts.io/workflow"
LABEL_TASK_NAME = "ts.io/task-name"
LABEL_MANAGED_BY = "ts.io/managed-by"
ANNOT_PRIORITY = "ts.scheduler/priority"

NAMESPACE = os.environ.get("TS_NAMESPACE", "default")
RECONCILE_INTERVAL_S = float(os.environ.get("TS_RECONCILE_INTERVAL_S", "2"))


# ─────────────────────────────────────────────────────────────────────────
# In-memory state for each Workflow we are managing.
# ─────────────────────────────────────────────────────────────────────────
class _TaskState:
    __slots__ = ("name", "template_name", "depends_on", "state",
                 "pod_name", "node_name", "started_at", "finished_at",
                 "outputs")

    def __init__(self, name: str, template_name: str,
                 depends_on: List[Dict[str, Any]]):
        self.name = name
        self.template_name = template_name
        # depends_on: list of {parent, dependencyType, dataFields}
        self.depends_on = depends_on
        self.state = "WAITING"
        self.pod_name: Optional[str] = None
        self.node_name: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.outputs: Dict[str, Any] = {}


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
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.message: str = ""


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
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # TaskTemplate lookup
    # ------------------------------------------------------------------
    def _get_template(self, name: str) -> Optional[Dict[str, Any]]:
        cached = self.template_cache.get(name)
        if cached:
            return cached
        try:
            obj = self.custom.get_namespaced_custom_object(
                group=CRD_GROUP, version=CRD_VERSION,
                namespace=NAMESPACE, plural=TASKTEMPLATE_PLURAL, name=name,
            )
            self.template_cache[name] = obj["spec"]
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
        # k8s names: ≤63 chars, lowercase, alphanumerics/dashes.
        short_uid = wf_uid.replace("-", "")[:8]
        base = f"{workflow_name}-{task_name}-{short_uid}".lower()
        return base[:63]

    def _build_pod(self, wf: _WorkflowState, task: _TaskState,
                   tmpl_spec: Dict[str, Any], env_vars: Dict[str, str]) -> client.V1Pod:
        pod_name = self._safe_pod_name(wf.name, task.name, wf.uid)

        cpu = tmpl_spec["cpuRequest"]
        mem = tmpl_spec["memoryRequest"]
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
        }
        if tmpl_spec.get("gangGroupId"):
            annotations["ts.scheduler/gang_group_id"] = tmpl_spec["gangGroupId"]
        if tmpl_spec.get("checkpointable"):
            annotations["ts.scheduler/checkpointable"] = "true"

        labels = {
            LABEL_WORKFLOW: wf.name,
            LABEL_TASK_NAME: task.name,
            LABEL_MANAGED_BY: "ts-controller",
        }

        # Standard env: producer-local data plane paths (Phase 1).
        # The main container writes outputs under TS_OUTPUTS_DIR, which is a
        # per-pod subdirectory on the node's local hostPath. Inputs (when
        # fetched from a remote node) land under TS_INPUTS_DIR via an
        # initContainer that the scheduler injects.
        env = list(client.V1EnvVar(name=k, value=v) for k, v in env_vars.items())
        env.extend([
            client.V1EnvVar(name="TS_WORKFLOW_ID", value=wf.name),
            client.V1EnvVar(name="TS_TASK_INSTANCE_ID", value=pod_name),
            client.V1EnvVar(name="TS_TASK_NAME", value=task.name),
            client.V1EnvVar(name="TS_INPUTS_DIR", value="/data/inputs"),
            client.V1EnvVar(
                name="TS_OUTPUTS_DIR",
                value=f"/data/outputs/{wf.name}/{pod_name}",
            ),
        ])

        # Mounts:
        #   - "inputs": emptyDir; populated by an initContainer injected by
        #     the scheduler when the pod needs to fetch parent outputs from
        #     other nodes. Empty otherwise (Channel C — same-node parent's
        #     outputs are mounted directly via subPath by the scheduler).
        #   - "outputs": node-local hostPath rooted at /var/lib/ts-data
        #     where the task writes its outputs; ts-fileserver DaemonSet
        #     serves this directory.
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
                requests={"cpu": str(cpu), "memory": str(mem)},
                limits={"cpu": str(cpu), "memory": str(mem)},
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
    # DAG → ready tasks
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

        Three kinds of variables are injected:
          - Channel A: ``<field>=<value>`` for each declared output field
            (small JSON-serialisable values produced via ``__TS_OUTPUT__=``).
          - Channel B: ``TS_PARENT_<name>_NODE`` and
            ``TS_PARENT_<name>_FILESERVER_URL`` so the task can curl bulk
            outputs from the parent's node-local fileserver if it wants.
          - ``TS_PARENT_<name>_OUTPUTS_DIR`` — path on the producer node where
            the parent's outputs live (under /var/lib/ts-data, served by the
            fileserver). Same-node parents can be read directly via the
            shared hostPath; cross-node parents must be fetched.
        """
        env: Dict[str, str] = {}
        fileserver_port = os.environ.get("TS_FILESERVER_PORT", "8080")
        for dep in task.depends_on:
            parent = wf.tasks.get(dep["parent"])
            if not parent:
                continue
            # Channel A — small payloads via env vars.
            for field in dep.get("dataFields", []):
                if field in parent.outputs:
                    env[field] = str(parent.outputs[field])
            # Channel B — parent location for bulk fetch.
            if parent.node_name and parent.pod_name:
                key = parent.name.replace("-", "_").upper()
                env[f"TS_PARENT_{key}_NODE"] = parent.node_name
                env[f"TS_PARENT_{key}_OUTPUTS_DIR"] = (
                    f"/{wf.name}/{parent.pod_name}"
                )
                env[f"TS_PARENT_{key}_FILESERVER_URL"] = (
                    f"http://{parent.node_name}:{fileserver_port}"
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
            # Stash template name back into the dict for annotation use.
            tmpl["_template_name"] = task.template_name
            env_vars = self._gather_env(task, wf)
            pod = self._build_pod(wf, task, tmpl, env_vars)
            try:
                self.core.create_namespaced_pod(namespace=wf.namespace, body=pod)
                task.pod_name = pod.metadata.name
                task.state = "READY"
                print(f"[CTRL] Created pod '{pod.metadata.name}' "
                      f"({wf.name}/{task.name})")
                if wf.started_at is None:
                    wf.started_at = _now_iso()
                wf.state = "RUNNING"
            except ApiException as e:
                if e.status == 409:
                    # Pod already exists (controller restart). Adopt it.
                    task.pod_name = pod.metadata.name
                    task.state = "READY"
                else:
                    print(f"[CTRL] create pod failed: {e.reason}")
                    task.state = "FAILED"
                    wf.message = f"create pod failed for {task.name}: {e.reason}"

        self._check_terminal(wf)
        self._patch_status(wf)

    def _check_terminal(self, wf: _WorkflowState):
        states = [t.state for t in wf.tasks.values()]
        if all(s in ("FINISHED", "FAILED") for s in states):
            if any(s == "FAILED" for s in states):
                wf.state = "FAILED"
            else:
                wf.state = "FINISHED"
            if wf.finished_at is None:
                wf.finished_at = _now_iso()

    # ------------------------------------------------------------------
    # Pod completion handling
    # ------------------------------------------------------------------
    def _on_pod_event(self, pod):
        labels = (pod.metadata.labels or {})
        wf_name = labels.get(LABEL_WORKFLOW)
        task_name = labels.get(LABEL_TASK_NAME)
        if not wf_name or not task_name:
            return

        # Find the workflow by name (uid lookup unavailable here).
        with self._lock:
            wf = next((w for w in self.workflows.values() if w.name == wf_name), None)
            if wf is None:
                return
            task = wf.tasks.get(task_name)
            if task is None:
                return

            phase = pod.status.phase
            if pod.spec.node_name and task.node_name is None:
                task.node_name = pod.spec.node_name
            if pod.status.start_time and task.started_at is None:
                task.started_at = pod.status.start_time.isoformat()

            if phase == "Running" and task.state == "READY":
                task.state = "RUNNING"
            elif phase == "Succeeded" and task.state != "FINISHED":
                task.state = "FINISHED"
                task.finished_at = _now_iso()
                task.outputs = self._extract_outputs(pod.metadata.name, wf.namespace)
                print(f"[CTRL] Task '{wf.name}/{task.name}' FINISHED "
                      f"on {task.node_name} outputs={list(task.outputs)}")
            elif phase == "Failed" and task.state != "FAILED":
                task.state = "FAILED"
                task.finished_at = _now_iso()
                print(f"[CTRL] Task '{wf.name}/{task.name}' FAILED on {task.node_name}")
                wf.message = f"task '{task.name}' failed"

    def _extract_outputs(self, pod_name: str, ns: str) -> Dict[str, Any]:
        """Pull __TS_OUTPUT__=... from pod logs (Channel A)."""
        try:
            log = self.core.read_namespaced_pod_log(name=pod_name, namespace=ns)
            for line in log.splitlines():
                if line.startswith("__TS_OUTPUT__="):
                    return json.loads(line[len("__TS_OUTPUT__="):])
        except ApiException as e:
            print(f"[CTRL] log read failed for {pod_name}: {e.reason}")
        except Exception as e:
            print(f"[CTRL] output parse failed for {pod_name}: {e}")
        return {}

    # ------------------------------------------------------------------
    # Status subresource update
    # ------------------------------------------------------------------
    def _patch_status(self, wf: _WorkflowState):
        total = len(wf.tasks)
        finished = sum(1 for t in wf.tasks.values() if t.state == "FINISHED")
        failed = sum(1 for t in wf.tasks.values() if t.state == "FAILED")
        status = {
            "state": wf.state,
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
                    "startedAt": t.started_at or "",
                    "finishedAt": t.finished_at or "",
                    "runtimeSeconds": _runtime_s(t),
                }
                for t in wf.tasks.values()
            ],
        }
        try:
            self.custom.patch_namespaced_custom_object_status(
                group=CRD_GROUP, version=CRD_VERSION, namespace=wf.namespace,
                plural=WORKFLOW_PLURAL, name=wf.name,
                body={"status": status},
            )
        except ApiException as e:
            # 404 if user deleted the Workflow; just forget about it.
            if e.status == 404:
                self.workflows.pop(wf.uid, None)
            else:
                print(f"[CTRL] status patch failed for {wf.name}: {e.reason}")

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
                    obj = ev["object"]
                    etype = ev["type"]
                    uid = obj.get("metadata", {}).get("uid")
                    if not uid:
                        continue
                    with self._lock:
                        if etype in ("ADDED", "MODIFIED"):
                            existing = self.workflows.get(uid)
                            if existing is None:
                                self.workflows[uid] = _WorkflowState(obj)
                                print(f"[CTRL] Admitted workflow '{obj['metadata']['name']}'")
                        elif etype == "DELETED":
                            self.workflows.pop(uid, None)
                            print(f"[CTRL] Workflow '{obj['metadata']['name']}' deleted")
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
                    self._on_pod_event(ev["object"])
            except Exception as e:
                print(f"[CTRL] pod watch error: {e}; retrying in 5s")
                time.sleep(5)

    def _reconcile_loop(self):
        while True:
            try:
                with self._lock:
                    wfs = list(self.workflows.values())
                for wf in wfs:
                    if wf.state in ("FINISHED", "FAILED"):
                        # Final status push then forget (keeps the CRD object
                        # but we stop reconciling it).
                        self._patch_status(wf)
                        continue
                    self._reconcile_workflow(wf)
            except Exception as e:
                print(f"[CTRL] reconcile error: {e}")
            time.sleep(RECONCILE_INTERVAL_S)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self):
        threading.Thread(target=self._watch_workflows, daemon=True).start()
        threading.Thread(target=self._watch_pods, daemon=True).start()
        # Reconcile loop runs on the main thread.
        self._reconcile_loop()


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _runtime_s(t: _TaskState) -> float:
    if not t.started_at or not t.finished_at:
        return 0.0
    try:
        import datetime
        s = datetime.datetime.fromisoformat(t.started_at.rstrip("Z"))
        f = datetime.datetime.fromisoformat(t.finished_at.rstrip("Z"))
        return (f - s).total_seconds()
    except Exception:
        return 0.0


def main():
    print("=" * 60)
    print("  ts-controller — Workflow CRD lifecycle controller")
    print("=" * 60)
    WorkflowController().run()


if __name__ == "__main__":
    main()
