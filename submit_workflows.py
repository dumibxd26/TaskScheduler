"""
Submit one or more DAG workflows to the K8s custom scheduler.

Each workflow is the 3-task pipeline:  task-io → task-mem → task-cpu
Pods are created with schedulerName=ts-scheduler so the DEPLOYED
K8sScheduler picks them up, scores nodes, and binds them.

This script handles DAG ordering (waits for parents), extracts
inter-task data from logs, and injects it as env vars into children.

Usage:
    python submit_workflows.py                  # 1 workflow
    python submit_workflows.py 5                # 5 workflows (staggered)
    python submit_workflows.py 3 --parallel     # 3 workflows all at once
"""
import sys
import time
import json
import threading
from kubernetes import client, config

NAMESPACE = "default"
SCHEDULER_NAME = "ts-scheduler"

# ── DAG definition ────────────────────────────────────────────────
# Each task: (template_id, task_class, image, cpu, mem_Mi, compatible_types)
TASKS = {
    "task-io": {
        "task_class": "IO_BOUND",
        "image": "ts-task-io:v1",
        "cpu": "0.5",
        "memory_mi": 512,
        "compatible": "IO_OPT,CPU_OPT,MEM_OPT",
    },
    "task-mem": {
        "task_class": "MEMORY_BOUND",
        "image": "ts-task-mem:v1",
        "cpu": "0.5",
        "memory_mi": 768,
        "compatible": "MEM_OPT,CPU_OPT,IO_OPT",
    },
    "task-cpu": {
        "task_class": "CPU_BOUND",
        "image": "ts-task-cpu:v1",
        "cpu": "1.0",
        "memory_mi": 256,
        "compatible": "CPU_OPT,MEM_OPT,IO_OPT",
    },
}

# DAG edges: (parent, child, data_fields)
EDGES = [
    ("task-io",  "task-mem", ["generated_file_path"]),
    ("task-mem", "task-cpu", ["processed_array_size"]),
]

# Topological order
TASK_ORDER = ["task-io", "task-mem", "task-cpu"]


def create_workflow_pod(v1: client.CoreV1Api, wf_id: str, task_id: str,
                        suffix: str, env_vars: dict = None):
    """Create a pod with annotations for the custom scheduler."""
    spec = TASKS[task_id]
    pod_name = f"{wf_id}-{task_id}-{suffix}"

    annotations = {
        "ts.scheduler/task_template_id": task_id,
        "ts.scheduler/task_name": task_id,
        "ts.scheduler/task_class": spec["task_class"],
        "ts.scheduler/compatible_node_types": spec["compatible"],
        "ts.scheduler/workflow_instance_id": wf_id,
    }

    env = [client.V1EnvVar(name=k, value=str(v))
           for k, v in (env_vars or {}).items()]

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, annotations=annotations),
        spec=client.V1PodSpec(
            scheduler_name=SCHEDULER_NAME,
            containers=[
                client.V1Container(
                    name="worker",
                    image=spec["image"],
                    image_pull_policy="Never",
                    env=env or None,
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": spec["cpu"],
                                  "memory": f"{spec['memory_mi']}Mi"},
                        limits={"cpu": spec["cpu"],
                                "memory": f"{spec['memory_mi']}Mi"},
                    ),
                )
            ],
            restart_policy="Never",
        ),
    )

    v1.create_namespaced_pod(namespace=NAMESPACE, body=pod)
    return pod_name


def wait_for_pod(v1: client.CoreV1Api, pod_name: str, timeout: int = 300):
    """Block until the pod reaches Succeeded or Failed."""
    start = time.time()
    while time.time() - start < timeout:
        pod = v1.read_namespaced_pod_status(name=pod_name, namespace=NAMESPACE)
        phase = pod.status.phase
        node = pod.spec.node_name or "(pending)"

        if phase == "Succeeded":
            return "Succeeded", node
        if phase == "Failed":
            return "Failed", node
        time.sleep(1)
    return "Timeout", "(unknown)"


def extract_output(v1: client.CoreV1Api, pod_name: str) -> dict:
    """Extract __TS_OUTPUT__=<json> from pod logs."""
    try:
        logs = v1.read_namespaced_pod_log(name=pod_name, namespace=NAMESPACE)
        for line in logs.splitlines():
            if line.startswith("__TS_OUTPUT__="):
                return json.loads(line[len("__TS_OUTPUT__="):])
    except Exception as e:
        print(f"  [WARN] Could not read logs for '{pod_name}': {e}")
    return {}


def run_workflow(v1: client.CoreV1Api, wf_id: str, results: dict):
    """Run a single 3-task DAG workflow end-to-end."""
    suffix = str(int(time.time() * 1000))[-6:]
    saved_outputs = {}  # task_id -> dict of outputs
    wf_results = []

    print(f"\n{'='*60}")
    print(f"  WORKFLOW: {wf_id}")
    print(f"{'='*60}")

    for task_id in TASK_ORDER:
        # Gather env vars from parent outputs
        env_vars = {}
        for parent_id, child_id, fields in EDGES:
            if child_id == task_id and parent_id in saved_outputs:
                for f in fields:
                    if f in saved_outputs[parent_id]:
                        env_vars[f] = saved_outputs[parent_id][f]

        # Create the pod
        t0 = time.time()
        pod_name = create_workflow_pod(v1, wf_id, task_id, suffix, env_vars)
        print(f"\n  [{wf_id}] Submitted: {pod_name}")

        # Wait for completion
        status, node = wait_for_pod(v1, pod_name)
        elapsed = time.time() - t0
        print(f"  [{wf_id}] {task_id:10s} -> {node:20s}  "
              f"{status:10s}  ({elapsed:.1f}s)")

        wf_results.append({
            "task": task_id,
            "pod": pod_name,
            "node": node,
            "status": status,
            "time": round(elapsed, 2),
        })

        if status == "Failed":
            print(f"  [{wf_id}] FAILED at {task_id} — aborting workflow")
            break

        # Extract outputs for downstream tasks
        output = extract_output(v1, pod_name)
        if output:
            saved_outputs[task_id] = output
            print(f"  [{wf_id}] Output: {output}")

    results[wf_id] = wf_results


def print_summary(results: dict):
    """Print a placement summary across all workflows."""
    print(f"\n{'='*70}")
    print(f"  PLACEMENT SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Workflow':<20} {'Task':<12} {'Node':<22} {'Status':<10} {'Time':>6}")
    print(f"  {'-'*20} {'-'*12} {'-'*22} {'-'*10} {'-'*6}")

    node_usage = {}
    for wf_id, tasks in sorted(results.items()):
        for t in tasks:
            print(f"  {wf_id:<20} {t['task']:<12} {t['node']:<22} "
                  f"{t['status']:<10} {t['time']:>5.1f}s")
            node_usage.setdefault(t["node"], []).append(t["task"])
        print()

    print(f"  {'─'*70}")
    print(f"  NODE DISTRIBUTION:")
    for node, tasks in sorted(node_usage.items()):
        print(f"    {node:<22} ran {len(tasks)} task(s): {', '.join(tasks)}")


def main():
    num_workflows = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    parallel = "--parallel" in sys.argv

    config.load_kube_config()
    v1 = client.CoreV1Api()

    # Verify scheduler is running
    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE, label_selector="app=ts-scheduler"
    )
    if not pods.items:
        print("[ERROR] No ts-scheduler pod found. Did you run:")
        print("        kubectl apply -f scheduler-deployment.yaml")
        sys.exit(1)

    sched_pod = pods.items[0]
    print(f"[OK] Scheduler pod: {sched_pod.metadata.name} "
          f"({sched_pod.status.phase})")

    results = {}

    if parallel:
        # Launch all workflows concurrently
        threads = []
        for i in range(1, num_workflows + 1):
            wf_id = f"wf-{i:03d}"
            t = threading.Thread(target=run_workflow, args=(v1, wf_id, results))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
    else:
        # Staggered: start each workflow 2 seconds apart
        threads = []
        for i in range(1, num_workflows + 1):
            wf_id = f"wf-{i:03d}"
            t = threading.Thread(target=run_workflow, args=(v1, wf_id, results))
            threads.append(t)
            t.start()
            if i < num_workflows:
                time.sleep(2)
        for t in threads:
            t.join()

    print_summary(results)

    # Cleanup hint
    print(f"\n  Cleanup: kubectl delete pods -l '!app'")


if __name__ == "__main__":
    main()
