"""
TaskScheduler — Unified K8s Workflow Server

A REST API that accepts workflow submissions and orchestrates them on a
kind / K8s cluster.  Combines the scheduling brain, DAG resolution,
pod lifecycle management, and learning loop into one process.

Endpoints:
    POST /workflows           Submit a new workflow
    GET  /workflows           List all workflows with status
    GET  /workflows/<id>      Detailed single-workflow status
    POST /templates           Register a workflow template
    GET  /templates           List registered templates
    GET  /cluster             Current cluster state

Run:
    python server.py                     (local kind)
    python server.py --simulate          (no K8s, fake task execution)
"""

import argparse
import json
import threading
import time
import uuid

from flask import Flask, request, jsonify

from models.enums import (
    TaskClass, NodeType, WorkflowClass, PriorityClass,
    TaskState, WorkflowState, DependencyType,
)
from models.cluster import Node, ClusterScenario
from models.workload import (
    WorkflowTemplate, TaskTemplate, WorkflowInstance, TaskInstance, DependencyEdge,
)
from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver
from services.queue_manager import QueueManager
from services.observer import ExecutionObserver
from services.data_manager import FileStoreDataManager
from engine import SchedulerEngine

# ---------------------------------------------------------------------------
# Enum lookup maps (string → enum, used when parsing JSON)
# ---------------------------------------------------------------------------
_NODE_TYPE = {e.name: e for e in NodeType}
_TASK_CLASS = {e.name: e for e in TaskClass}
_WF_CLASS = {e.name: e for e in WorkflowClass}
_PRIORITY = {e.name: e for e in PriorityClass}
_DEP_TYPE = {e.name: e for e in DependencyType}

# ---------------------------------------------------------------------------
# JSON → model helpers
# ---------------------------------------------------------------------------

def _parse_template(data: dict) -> WorkflowTemplate:
    """Convert a JSON dict into a WorkflowTemplate object."""
    tasks = {}
    for tid, tdata in data["tasks"].items():
        tasks[tid] = TaskTemplate(
            task_template_id=tid,
            name=tdata.get("name", tid),
            task_class=_TASK_CLASS[tdata["task_class"]],
            cpu_request=float(tdata["cpu_request"]),
            memory_request=float(tdata["memory_request"]),
            image_name=tdata["image_name"],
            compatible_node_types=[_NODE_TYPE[n] for n in tdata["compatible_node_types"]],
            min_cores=int(tdata.get("min_cores", 1)),
            max_cores=int(tdata["max_cores"]) if tdata.get("max_cores") is not None else None,
            command=tdata.get("command", []),
            args=tdata.get("args", []),
        )

    edges = []
    for edata in data.get("edges", []):
        edges.append(DependencyEdge(
            parent_task_id=edata["parent_task_id"],
            child_task_id=edata["child_task_id"],
            dependency_type=_DEP_TYPE[edata["dependency_type"]],
            data_field_names=edata.get("data_field_names", []),
        ))

    priority_raw = data.get("default_priority", "BATCH")
    if isinstance(priority_raw, int):
        priority = PriorityClass(priority_raw)
    else:
        priority = _PRIORITY.get(priority_raw, PriorityClass.BATCH)

    return WorkflowTemplate(
        workflow_template_id=data["workflow_template_id"],
        name=data.get("name", data["workflow_template_id"]),
        workflow_class=_WF_CLASS.get(data.get("workflow_class", "BATCH"), WorkflowClass.BATCH),
        default_priority=priority,
        default_preemptable=data.get("default_preemptable", True),
        tasks=tasks,
        edges=edges,
    )


def _make_instance(template: WorkflowTemplate, wf_id: str,
                   priority: PriorityClass = None) -> WorkflowInstance:
    """Instantiate a workflow from a template with fresh task instances."""
    pri = priority or template.default_priority
    task_instances = {}
    for tid in template.tasks:
        task_instances[tid] = TaskInstance(
            task_instance_id=f"{wf_id}-{tid}",
            workflow_instance_id=wf_id,
            task_template_id=tid,
        )
    return WorkflowInstance(
        workflow_instance_id=wf_id,
        workflow_template_id=template.workflow_template_id,
        workflow_class=template.workflow_class,
        priority=pri,
        preemptable=template.default_preemptable,
        task_instances=task_instances,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers (model → JSON)
# ---------------------------------------------------------------------------

def _task_to_dict(t: TaskInstance) -> dict:
    return {
        "task_instance_id": t.task_instance_id,
        "task_template_id": t.task_template_id,
        "state": t.state.name,
        "assigned_node_id": t.assigned_node_id,
        "start_time": t.start_time,
        "finish_time": t.finish_time,
    }


def _workflow_to_dict(wf: WorkflowInstance) -> dict:
    return {
        "workflow_instance_id": wf.workflow_instance_id,
        "workflow_template_id": wf.workflow_template_id,
        "workflow_class": wf.workflow_class.name,
        "priority": wf.priority.name,
        "state": wf.state.name,
        "tasks": {tid: _task_to_dict(t) for tid, t in wf.task_instances.items()},
    }


def _node_to_dict(n: Node) -> dict:
    return {
        "node_id": n.node_id,
        "node_type": n.node_type.name,
        "total_cpu": n.total_cpu,
        "total_memory": n.total_memory,
        "free_cpu": n.free_cpu,
        "free_memory": n.free_memory,
        "running_tasks": n.running_tasks,
        "warm_images": list(n.warm_images),
    }


# ===========================================================================
# K8s pod helpers (imported from k8s_main.py logic, self-contained here)
# ===========================================================================

def _create_k8s_pod(v1_api, pod_name, node_name, image_name, command, args,
                    cpu_req, mem_req, env_vars: dict = None, namespace="default"):
    from kubernetes import client
    env = [client.V1EnvVar(name=k, value=str(v)) for k, v in (env_vars or {}).items()]
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name),
        spec=client.V1PodSpec(
            containers=[client.V1Container(
                name="worker", image=image_name, image_pull_policy="Never",
                command=command or None, args=args or None, env=env or None,
                resources=client.V1ResourceRequirements(
                    requests={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"},
                    limits={"cpu": str(cpu_req), "memory": f"{int(mem_req)}Mi"},
                ),
            )],
            node_name=node_name, restart_policy="Never",
        ),
    )
    v1_api.create_namespaced_pod(namespace=namespace, body=pod)
    print(f"[K8S] Pod '{pod_name}' -> '{node_name}'")


def _poll_pod(v1_api, pod_name, namespace="default"):
    """
    Returns (phase, startup_secs, runtime_secs) once the pod reaches a
    terminal state ('Succeeded' or 'Failed').  Non-blocking caller must
    handle the loop externally; this returns the current snapshot.
    """
    status = v1_api.read_namespaced_pod_status(name=pod_name, namespace=namespace).status
    return status.phase


def _extract_output(v1_api, pod_name, namespace="default") -> dict:
    try:
        logs = v1_api.read_namespaced_pod_log(name=pod_name, namespace=namespace)
        for line in logs.splitlines():
            if line.startswith("__TS_OUTPUT__="):
                return json.loads(line[len("__TS_OUTPUT__="):])
    except Exception as e:
        print(f"[WARN] logs for '{pod_name}': {e}")
    return {}


# ===========================================================================
# Core server class — holds all state and the background loop
# ===========================================================================

class SchedulerServer:
    def __init__(self, cluster: ClusterScenario, simulate: bool = False):
        self.cluster = cluster
        self.simulate = simulate

        # Core services
        self.store = ProfileStore()
        self.algo = PlacementAlgorithm(self.store)
        self.runner = WorkflowSchedulerRunner(self.store, self.algo)
        self.resolver = ReadinessResolver()
        self.queue = QueueManager()
        self.observer = ExecutionObserver(self.store)
        self.data_mgr = FileStoreDataManager()

        # Template registry: template_id -> WorkflowTemplate
        self.templates: dict[str, WorkflowTemplate] = {}

        # Engine
        self.engine = SchedulerEngine(self.queue, self.resolver, self.runner, self.templates)

        # All workflow instances ever created (for status queries)
        self.all_workflows: dict[str, WorkflowInstance] = {}

        # In-flight pod tracking: pod_name -> metadata dict
        self._pods_in_flight: dict[str, dict] = {}
        self._lock = threading.Lock()

        # K8s client (lazy init)
        self._v1 = None
        if not simulate:
            self._init_k8s()

    def _init_k8s(self):
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        self._v1 = client.CoreV1Api()
        print("[K8S] Connected to cluster")

    # ------------------------------------------------------------------
    # Template management
    # ------------------------------------------------------------------
    def register_template(self, template: WorkflowTemplate):
        self.templates[template.workflow_template_id] = template
        print(f"[TEMPLATE] Registered '{template.workflow_template_id}' "
              f"with {len(template.tasks)} tasks")

    # ------------------------------------------------------------------
    # Workflow submission
    # ------------------------------------------------------------------
    def submit_workflow(self, template_id: str,
                        priority: PriorityClass = None) -> WorkflowInstance:
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"Unknown template '{template_id}'")

        wf_id = f"{template_id}-{uuid.uuid4().hex[:8]}"
        wf = _make_instance(template, wf_id, priority)
        self.all_workflows[wf_id] = wf

        self.data_mgr.provision_shared_workspace(wf_id)
        self.queue.submit_workflow(wf)
        return wf

    # ------------------------------------------------------------------
    # Background orchestration loop
    # ------------------------------------------------------------------
    def _run_loop(self):
        print("[LOOP] Background orchestration started")
        while True:
            try:
                self._tick()
            except Exception as e:
                print(f"[LOOP ERROR] {e}")
            time.sleep(1)

    def _tick(self):
        with self._lock:
            # 1. Engine tick: admit workflows, resolve DAGs, dispatch ready tasks
            self.engine.run_tick(self.cluster)

            # 2. Find newly dispatched tasks (state=RUNNING, no pod yet)
            for wf in list(self.queue.admitted_workflows.values()):
                template = self.templates.get(wf.workflow_template_id)
                if not template:
                    continue
                for task in wf.task_instances.values():
                    if task.state != TaskState.RUNNING:
                        continue
                    pod_key = task.task_instance_id
                    if pod_key in self._pods_in_flight:
                        continue

                    task_tmpl = template.tasks[task.task_template_id]

                    # Collect env vars from parent outputs
                    env_vars = {}
                    parent_edges = [e for e in template.edges
                                    if e.child_task_id == task.task_template_id]
                    for edge in parent_edges:
                        if edge.dependency_type == DependencyType.DATA and edge.data_field_names:
                            inputs = self.data_mgr.get_inputs_for_task(
                                wf.workflow_instance_id,
                                task.task_template_id,
                                edge.data_field_names,
                            )
                            env_vars.update({k: str(v) for k, v in inputs.items()
                                             if v is not None})

                    # Log warm/cold state
                    node = next((n for n in self.cluster.nodes
                                 if n.node_id == task.assigned_node_id), None)
                    was_warm = node and task_tmpl.image_name in node.warm_images
                    print(f"[WARM]  '{task_tmpl.image_name}' on "
                          f"'{task.assigned_node_id}': "
                          f"{'WARM' if was_warm else 'COLD'}")

                    pod_name = f"{task.task_instance_id}-{int(time.time())}"
                    task.start_time = time.time()

                    if self.simulate:
                        # In simulation mode, mock immediate success after a delay
                        self._pods_in_flight[pod_key] = {
                            "pod_name": pod_name,
                            "task": task,
                            "task_tmpl": task_tmpl,
                            "wf": wf,
                            "node": node,
                            "submit_time": time.time(),
                            "sim_runtime": 2.0,      # fake 2s runtime
                            "sim_startup": 0.5,       # fake 0.5s startup
                        }
                    else:
                        _create_k8s_pod(
                            self._v1, pod_name, task.assigned_node_id,
                            task_tmpl.image_name, task_tmpl.command, task_tmpl.args,
                            task_tmpl.cpu_request, task_tmpl.memory_request,
                            env_vars=env_vars,
                        )
                        self._pods_in_flight[pod_key] = {
                            "pod_name": pod_name,
                            "task": task,
                            "task_tmpl": task_tmpl,
                            "wf": wf,
                            "node": node,
                            "submit_time": time.time(),
                            "t_running": None,
                        }

            # 3. Poll in-flight pods for completion
            completed_keys = []
            for pod_key, info in self._pods_in_flight.items():
                if self.simulate:
                    elapsed = time.time() - info["submit_time"]
                    if elapsed >= info["sim_runtime"] + info["sim_startup"]:
                        self._handle_completion(
                            info, info["sim_startup"], info["sim_runtime"])
                        completed_keys.append(pod_key)
                else:
                    phase = _poll_pod(self._v1, info["pod_name"])
                    if phase == "Running" and info["t_running"] is None:
                        info["t_running"] = time.time()
                    elif phase == "Succeeded":
                        now = time.time()
                        t_run = info["t_running"]
                        startup = (t_run - info["submit_time"]) if t_run else 0.0
                        runtime = (now - t_run) if t_run else (now - info["submit_time"])
                        self._handle_completion(info, startup, runtime)
                        completed_keys.append(pod_key)
                    elif phase == "Failed":
                        self._handle_failure(info, "Pod failed")
                        completed_keys.append(pod_key)

            for k in completed_keys:
                del self._pods_in_flight[k]

            # 4. Mark completed workflows in our tracking dict
            for wf_id, wf in list(self.all_workflows.items()):
                if wf.state in (WorkflowState.QUEUED, WorkflowState.ADMITTED,
                                WorkflowState.RUNNING):
                    self.resolver.check_workflow_terminal(wf)

    def _handle_completion(self, info: dict, startup: float, runtime: float):
        task = info["task"]
        task_tmpl = info["task_tmpl"]
        wf = info["wf"]
        node = info["node"]

        # Unregister from node capacity tracker
        if node:
            node.unregister_task(task.task_instance_id)

        # Extract output from pod logs (K8s only)
        if not self.simulate and self._v1:
            output = _extract_output(self._v1, info["pod_name"])
            if output:
                self.data_mgr.save_small_output(
                    wf.workflow_instance_id, task.task_instance_id, output)

        # Record in learning system
        node_type = node.node_type if node else None
        node_id = node.node_id if node else None
        self.observer.record_task_completion(
            task, actual_runtime=runtime, actual_startup=startup,
            node_id=node_id, node_type=node_type,
            node_cpu_at_start=node.cpu_usage_ratio if node else 0.0,
            node_memory_at_start=node.memory_usage_ratio if node else 0.0,
        )

        # Mark image warm
        if node:
            was_warm = task_tmpl.image_name in node.warm_images
            node.warm_images.add(task_tmpl.image_name)
            if not was_warm:
                print(f"[WARM]  '{task_tmpl.image_name}' now warm on '{node.node_id}'")

    def _handle_failure(self, info: dict, reason: str):
        task = info["task"]
        node = info["node"]

        if node:
            node.unregister_task(task.task_instance_id)

        self.observer.record_task_failure(
            task,
            node_id=node.node_id if node else None,
            node_type=node.node_type if node else None,
            reason=reason,
        )

    def start_background_loop(self):
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()


# ===========================================================================
# Flask application
# ===========================================================================

def create_app(server: SchedulerServer) -> Flask:
    app = Flask(__name__)

    @app.post("/templates")
    def register_template():
        data = request.get_json(force=True)
        try:
            template = _parse_template(data)
            server.register_template(template)
            return jsonify({"status": "ok",
                            "template_id": template.workflow_template_id}), 201
        except (KeyError, ValueError) as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/templates")
    def list_templates():
        result = {}
        for tid, t in server.templates.items():
            result[tid] = {
                "name": t.name,
                "workflow_class": t.workflow_class.name,
                "default_priority": t.default_priority.name,
                "tasks": list(t.tasks.keys()),
                "edges": len(t.edges),
            }
        return jsonify(result)

    @app.post("/workflows")
    def submit_workflow():
        data = request.get_json(force=True)
        template_id = data.get("template_id")
        priority_str = data.get("priority")

        if not template_id:
            return jsonify({"error": "template_id is required"}), 400

        priority = _PRIORITY.get(priority_str) if priority_str else None

        try:
            with server._lock:
                wf = server.submit_workflow(template_id, priority)
            return jsonify({
                "status": "submitted",
                "workflow_instance_id": wf.workflow_instance_id,
            }), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/workflows")
    def list_workflows():
        with server._lock:
            result = []
            for wf in server.all_workflows.values():
                result.append({
                    "workflow_instance_id": wf.workflow_instance_id,
                    "template_id": wf.workflow_template_id,
                    "priority": wf.priority.name,
                    "state": wf.state.name,
                })
            return jsonify(result)

    @app.get("/workflows/<wf_id>")
    def get_workflow(wf_id):
        with server._lock:
            wf = server.all_workflows.get(wf_id)
            if not wf:
                return jsonify({"error": "not found"}), 404
            return jsonify(_workflow_to_dict(wf))

    @app.get("/cluster")
    def cluster_state():
        with server._lock:
            return jsonify({
                "scenario_id": server.cluster.scenario_id,
                "nodes": [_node_to_dict(n) for n in server.cluster.nodes],
            })

    @app.get("/profiles")
    def profiles():
        with server._lock:
            result = {}
            for tid in server.store._profiles:
                profile = server.store.get_profile(tid)
                if profile:
                    result[tid] = {
                        "exploration_level": profile.exploration_level,
                        "preferred_node_order": [n.name for n in profile.preferred_node_order],
                        "preferred_node_ids": profile.preferred_node_ids,
                    }
            return jsonify(result)

    return app


# ===========================================================================
# CLI entrypoint
# ===========================================================================

def _build_cluster_from_k8s() -> ClusterScenario:
    """Discover nodes from the live K8s cluster."""
    from kubernetes import client, config
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()

    v1 = client.CoreV1Api()
    k8s_nodes = v1.list_node().items
    nodes = []

    _nt = {e.name: e for e in NodeType}

    for n in k8s_nodes:
        labels = n.metadata.labels or {}
        nt_str = labels.get("node-type")
        if nt_str is None:
            continue
        node_type = _nt.get(nt_str, NodeType.GENERAL)

        total_cpu = float(labels.get("ts.capacity/cpu", "1"))
        total_mem = float(labels.get("ts.capacity/memory", "1024"))

        alloc = n.status.allocatable or {}
        free_cpu = float(alloc.get("cpu", total_cpu))
        free_mem_ki = alloc.get("memory", f"{int(total_mem * 1024)}Ki")
        if isinstance(free_mem_ki, str) and free_mem_ki.endswith("Ki"):
            free_mem = float(free_mem_ki[:-2]) / 1024
        else:
            free_mem = total_mem

        # NOTE: We intentionally do NOT read n.status.images here.
        # In kind, `kind load docker-image` pushes images to ALL nodes
        # simultaneously, so Docker-level cache is identical everywhere
        # and provides zero scheduling signal.
        # Instead, warm_images starts empty and is populated only when a
        # task actually runs on a node (see _handle_completion), modelling
        # real execution-level warmth (page cache, JIT, cgroup reuse).

        nodes.append(Node(
            node_id=n.metadata.name, node_type=node_type,
            total_cpu=total_cpu, total_memory=total_mem,
            free_cpu=free_cpu, free_memory=free_mem,
            # warm_images defaults to empty set via dataclass
        ))

    print(f"[CLUSTER] Discovered {len(nodes)} worker node(s): "
          f"{[f'{nd.node_id}({nd.node_type.name})' for nd in nodes]}")
    return ClusterScenario(
        scenario_id="live-k8s", name="Live Cluster",
        description="Auto-discovered", nodes=nodes,
    )


def _build_simulated_cluster() -> ClusterScenario:
    return ClusterScenario(
        scenario_id="sim", name="Simulated Cluster",
        description="3 virtual nodes",
        nodes=[
            Node("node-cpu", NodeType.CPU_OPT, 4.0, 1024.0, 4.0, 1024.0),
            Node("node-mem", NodeType.MEM_OPT, 1.0, 4096.0, 1.0, 4096.0),
            Node("node-io",  NodeType.IO_OPT,  2.0, 2048.0, 2.0, 2048.0),
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TaskScheduler Server")
    parser.add_argument("--simulate", action="store_true",
                        help="Run without K8s (fake task execution)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    if args.simulate:
        cluster = _build_simulated_cluster()
    else:
        cluster = _build_cluster_from_k8s()

    srv = SchedulerServer(cluster, simulate=args.simulate)
    srv.start_background_loop()

    app = create_app(srv)
    print(f"\n{'=' * 60}")
    print(f"  TaskScheduler server listening on {args.host}:{args.port}")
    print(f"  Mode: {'SIMULATION' if args.simulate else 'KUBERNETES'}")
    print(f"{'=' * 60}\n")
    app.run(host=args.host, port=args.port, threaded=True)
