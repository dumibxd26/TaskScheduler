"""Run repeatable policy comparisons on a live Kubernetes cluster."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from services.scheduler import legacy_weights_for_policy


GROUP = "ts.io"
VERSION = "v1"
WORKFLOWS = "workflows"
TASKTEMPLATES = "tasktemplates"
EXPERIMENT_PREFIX = "ts-exp-"
EXPERIMENT_NAME = "kind-policy-comparison"
SCHEDULER_DEPLOYMENT = "ts-scheduler"
SCHEDULER_NAMESPACE = "ts-system"
SUPPORTED_POLICIES = ("legacy", "legacy-v1", "legacy-tuned", "adaptive")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def elapsed_seconds(start, finish):
    start_dt = parse_timestamp(start)
    finish_dt = parse_timestamp(finish)
    if start_dt is None or finish_dt is None:
        return None
    return max(0.0, (finish_dt - start_dt).total_seconds())


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def policy_order_for_repetition(policies, repetition):
    ordered = list(policies)
    if not ordered:
        return []
    offset = (repetition - 1) % len(ordered)
    return ordered[offset:] + ordered[:offset]


def workflow_body(name, shape, priority, workflow_class, tasks,
                  *, preemptable=True, run_id=""):
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Workflow",
        "metadata": {
            "name": name,
            "labels": {
                "ts.io/experiment": EXPERIMENT_NAME,
                "ts.io/experiment-run": run_id,
                "ts.io/experiment-shape": shape,
            },
        },
        "spec": {
            "priority": priority,
            "preemptable": preemptable,
            "workflowClass": workflow_class,
            "tasks": tasks,
        },
    }


def workflow_batch(policy, repetition, run_id):
    prefix = f"{EXPERIMENT_PREFIX}{policy}-r{repetition}"
    pressure = workflow_body(
        f"{prefix}-pressure", "pressure", "BEST_EFFORT", "BURST",
        [
            {"name": f"long-{index}", "template": "experiment-cpu-long"}
            for index in range(1, 7)
        ],
        run_id=run_id,
    )
    critical = workflow_body(
        f"{prefix}-critical", "critical", "CRITICAL", "BURST",
        [
            {"name": "urgent-1", "template": "experiment-cpu"},
            {"name": "urgent-2", "template": "experiment-cpu"},
        ],
        preemptable=False,
        run_id=run_id,
    )
    pipeline = workflow_body(
        f"{prefix}-pipeline", "pipeline", "BATCH", "PIPELINE",
        [
            {"name": "ingest", "template": "experiment-io"},
            {
                "name": "stage",
                "template": "experiment-mem",
                "dependsOn": [{
                    "parent": "ingest",
                    "dependencyType": "DATA",
                    "dataFields": ["generated_file_path"],
                }],
            },
            {
                "name": "crunch",
                "template": "experiment-cpu",
                "dependsOn": [{
                    "parent": "stage",
                    "dependencyType": "DATA",
                    "dataFields": ["processed_array_size"],
                }],
            },
        ],
        run_id=run_id,
    )
    fanout = workflow_body(
        f"{prefix}-fanout", "fanout", "BATCH", "BATCH",
        [{"name": "source", "template": "experiment-io"}] + [
            {
                "name": f"leaf-{index}",
                "template": "experiment-cpu",
                "dependsOn": [{
                    "parent": "source",
                    "dependencyType": "EXECUTION",
                }],
            }
            for index in range(1, 5)
        ],
        run_id=run_id,
    )
    parallel = workflow_body(
        f"{prefix}-parallel", "parallel", "BATCH", "BATCH",
        [
            {"name": "io", "template": "experiment-io"},
            {"name": "memory", "template": "experiment-mem"},
            {"name": "cpu-1", "template": "experiment-cpu"},
            {"name": "cpu-2", "template": "experiment-cpu"},
        ],
        run_id=run_id,
    )
    return pressure, critical, [pipeline, fanout, parallel]


def task_metrics(log_text):
    result = {}
    for line in reversed(log_text.splitlines()):
        if line.startswith("__TS_METRICS__="):
            try:
                payload = json.loads(line[len("__TS_METRICS__="):])
                if isinstance(payload, dict):
                    result = payload
            except json.JSONDecodeError:
                pass
            break
    transfer_mode = None
    transfer_source = None
    for line in log_text.splitlines():
        if line.startswith("[LOCAL]"):
            transfer_mode = "local"
            transfer_source = line
        elif line.startswith("[FETCH]"):
            transfer_mode = "remote"
            transfer_source = line
    return result, transfer_mode, transfer_source


def summarize_policy(trials):
    workflows = [
        workflow
        for trial in trials
        for workflow in trial.get("workflows", [])
    ]
    successful = [
        workflow for workflow in workflows
        if workflow.get("state") == "FINISHED"
    ]
    responses = [
        workflow["response_seconds"] for workflow in successful
        if workflow.get("response_seconds") is not None
    ]
    makespans = [
        trial["makespan_seconds"] for trial in trials
        if trial.get("makespan_seconds") is not None
    ]
    trial_means = [
        trial["mean_response_seconds"] for trial in trials
        if trial.get("mean_response_seconds") is not None
    ]
    critical_responses = [
        workflow["response_seconds"] for workflow in successful
        if workflow.get("shape") == "critical"
        and workflow.get("response_seconds") is not None
    ]
    tasks = [task for workflow in workflows for task in workflow["tasks"]]
    runtimes = [
        task["container_runtime_seconds"] for task in tasks
        if task.get("container_runtime_seconds") is not None
    ]
    transfers = [
        float(task.get("metrics", {}).get("transfer_seconds", 0.0))
        for task in tasks
    ]
    placements = Counter(
        task["node_name"] for task in tasks if task.get("node_name"))
    by_shape = {}
    for shape in sorted({workflow.get("shape") for workflow in workflows}):
        shape_responses = [
            workflow["response_seconds"] for workflow in successful
            if workflow.get("shape") == shape
            and workflow.get("response_seconds") is not None
        ]
        by_shape[shape] = {
            "completed": len(shape_responses),
            "median_response_seconds": (
                statistics.median(shape_responses)
                if shape_responses else None),
        }
    return {
        "trials": len(trials),
        "workflows": len(workflows),
        "successful_workflows": len(successful),
        "completion_rate": len(successful) / len(workflows) if workflows else 0,
        "median_workflow_response_seconds": (
            statistics.median(responses) if responses else None),
        "p95_workflow_response_seconds": percentile(responses, 0.95),
        "median_trial_mean_response_seconds": (
            statistics.median(trial_means) if trial_means else None),
        "median_trial_makespan_seconds": (
            statistics.median(makespans) if makespans else None),
        "median_critical_response_seconds": (
            statistics.median(critical_responses)
            if critical_responses else None),
        "median_task_runtime_seconds": (
            statistics.median(runtimes) if runtimes else None),
        "preemptions": sum(trial.get("preemptions", 0) for trial in trials),
        "remote_fetches": sum(
            task.get("transfer_mode") == "remote" for task in tasks),
        "local_reads": sum(
            task.get("transfer_mode") == "local" for task in tasks),
        "total_transfer_seconds": sum(transfers),
        "placements_by_node": dict(sorted(placements.items())),
        "by_shape": by_shape,
    }


class KubernetesExperiment:
    def __init__(self, namespace, timeout_seconds):
        config.load_kube_config()
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()

    @staticmethod
    def delete_ignoring_not_found(delete_call, *args, **kwargs):
        try:
            return delete_call(*args, **kwargs)
        except ApiException as exc:
            if exc.status != 404:
                raise
            return None

    def apply_tasktemplates(self, path):
        for body in yaml.safe_load_all(Path(path).read_text(encoding="utf-8")):
            if not body:
                continue
            body.setdefault("metadata", {})["namespace"] = self.namespace
            name = body["metadata"]["name"]
            try:
                current = self.custom.get_namespaced_custom_object(
                    GROUP, VERSION, self.namespace, TASKTEMPLATES, name)
                body["metadata"]["resourceVersion"] = current["metadata"][
                    "resourceVersion"]
                self.custom.replace_namespaced_custom_object(
                    GROUP, VERSION, self.namespace, TASKTEMPLATES, name, body)
            except ApiException as exc:
                if exc.status != 404:
                    raise
                self.custom.create_namespaced_custom_object(
                    GROUP, VERSION, self.namespace, TASKTEMPLATES, body)

    def clean_experiment_objects(self):
        listing = self.custom.list_namespaced_custom_object(
            GROUP, VERSION, self.namespace, WORKFLOWS)
        for workflow in listing.get("items", []):
            name = workflow["metadata"]["name"]
            if name.startswith(EXPERIMENT_PREFIX):
                self.delete_ignoring_not_found(
                    self.custom.delete_namespaced_custom_object,
                    GROUP, VERSION, self.namespace, WORKFLOWS, name,
                    body={"propagationPolicy": "Background"},
                )
        for pod in self.core.list_namespaced_pod(self.namespace).items:
            workflow_name = (pod.metadata.labels or {}).get("ts.io/workflow", "")
            if workflow_name.startswith(EXPERIMENT_PREFIX):
                self.delete_ignoring_not_found(
                    self.core.delete_namespaced_pod,
                    pod.metadata.name, self.namespace,
                    grace_period_seconds=0,
                    body=client.V1DeleteOptions(grace_period_seconds=0),
                )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            workflows = self.custom.list_namespaced_custom_object(
                GROUP, VERSION, self.namespace, WORKFLOWS).get("items", [])
            experiment_workflows = [
                item for item in workflows
                if item["metadata"]["name"].startswith(EXPERIMENT_PREFIX)
            ]
            pods = self.core.list_namespaced_pod(self.namespace).items
            experiment_pods = [
                pod for pod in pods
                if (pod.metadata.labels or {}).get(
                    "ts.io/workflow", "").startswith(EXPERIMENT_PREFIX)
            ]
            if not experiment_workflows and not experiment_pods:
                self.clean_experiment_data()
                return
            time.sleep(1)
        raise TimeoutError("experiment objects did not terminate")

    def clean_experiment_data(self):
        fileservers = self.core.list_namespaced_pod(
            SCHEDULER_NAMESPACE,
            label_selector="app.kubernetes.io/name=ts-fileserver",
        ).items
        command = [
            "sh", "-c",
            "for path in /srv/data/ts-exp-*; do "
            "[ ! -e \"$path\" ] || rm -rf \"$path\"; done",
        ]
        for pod in fileservers:
            stream(
                self.core.connect_get_namespaced_pod_exec,
                pod.metadata.name,
                SCHEDULER_NAMESPACE,
                container="nginx",
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )

    def configure_scheduler(self, policy, seed, run_id):
        profile_suffix = f"{policy}-{run_id}"
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "ts.io/experiment-restarted-at": utc_now(),
                        },
                    },
                    "spec": {
                        "containers": [{
                            "name": "scheduler",
                            "env": [
                                {"name": "TS_POLICY", "value": policy},
                                {"name": "TS_RANDOM_SEED", "value": str(seed)},
                                {
                                    "name": "TS_PROFILE_PATH",
                                    "value": f"/data/profiles-{profile_suffix}.json",
                                },
                                {
                                    "name": "TS_PROFILE_CONFIGMAP",
                                    "value": f"ts-profiles-{profile_suffix}",
                                },
                                {
                                    "name": "TS_PERSIST_INTERVAL_S",
                                    "value": "5",
                                },
                            ],
                        }],
                    },
                },
            },
        }
        self.apps.patch_namespaced_deployment(
            SCHEDULER_DEPLOYMENT, SCHEDULER_NAMESPACE, patch)
        pod = self.wait_for_scheduler()
        expected = "AdaptivePolicy" if policy == "adaptive" else (
            "LegacyEightFactorPolicy")
        deadline = time.monotonic() + 60
        log_text = ""
        while time.monotonic() < deadline:
            log_text = self.core.read_namespaced_pod_log(
                pod.metadata.name, SCHEDULER_NAMESPACE)
            if f"Policy: {expected}" in log_text:
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"scheduler did not start requested policy {policy}: {log_text}")
        configuration = {
            "pod": pod.metadata.name,
            "policy": policy,
            "profile_path": f"/data/profiles-{profile_suffix}.json",
            "profile_configmap": f"ts-profiles-{profile_suffix}",
        }
        if policy != "adaptive":
            configuration["legacy_weights"] = asdict(
                legacy_weights_for_policy(policy))
        return configuration

    def wait_for_scheduler(self):
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            deployment = self.apps.read_namespaced_deployment(
                SCHEDULER_DEPLOYMENT, SCHEDULER_NAMESPACE)
            desired = deployment.spec.replicas or 1
            status = deployment.status
            if (status.observed_generation == deployment.metadata.generation
                    and (status.updated_replicas or 0) == desired
                    and (status.available_replicas or 0) == desired):
                pods = self.core.list_namespaced_pod(
                    SCHEDULER_NAMESPACE,
                    label_selector="app.kubernetes.io/name=ts-scheduler",
                ).items
                ready = [
                    pod for pod in pods
                    if pod.status.phase == "Running"
                    and any(
                        condition.type == "Ready" and condition.status == "True"
                        for condition in (pod.status.conditions or [])
                    )
                ]
                if ready:
                    return max(
                        ready,
                        key=lambda pod: pod.metadata.creation_timestamp)
            time.sleep(1)
        raise TimeoutError("scheduler rollout did not become ready")

    def create_workflow(self, body):
        return self.custom.create_namespaced_custom_object(
            GROUP, VERSION, self.namespace, WORKFLOWS, body)

    def wait_for_workflows(self, names):
        deadline = time.monotonic() + self.timeout_seconds
        states = {}
        while time.monotonic() < deadline:
            for name in names:
                if states.get(name) in ("FINISHED", "FAILED"):
                    continue
                workflow = self.custom.get_namespaced_custom_object(
                    GROUP, VERSION, self.namespace, WORKFLOWS, name)
                states[name] = workflow.get("status", {}).get("state", "QUEUED")
            if all(states.get(name) in ("FINISHED", "FAILED") for name in names):
                return states
            time.sleep(1)
        raise TimeoutError(f"workflows did not finish: {states}")

    def collect_workflow(self, name):
        workflow = self.custom.get_namespaced_custom_object(
            GROUP, VERSION, self.namespace, WORKFLOWS, name)
        status = workflow.get("status", {})
        metadata = workflow["metadata"]
        tasks = []
        for task_status in status.get("tasks", []):
            pod_name = task_status.get("podName")
            record = {
                "name": task_status.get("name"),
                "state": task_status.get("state"),
                "pod_name": pod_name,
                "node_name": task_status.get("nodeName"),
                "status_runtime_seconds": task_status.get("runtimeSeconds"),
                "container_runtime_seconds": None,
                "startup_seconds": None,
                "pod_phase": None,
                "restart_count": None,
                "metrics": {},
                "transfer_mode": None,
                "transfer_source": None,
            }
            if pod_name:
                try:
                    pod = self.core.read_namespaced_pod(pod_name, self.namespace)
                    record["pod_phase"] = pod.status.phase
                    statuses = pod.status.container_statuses or []
                    if statuses:
                        record["restart_count"] = statuses[0].restart_count
                        terminated = statuses[0].state.terminated
                        if terminated:
                            record["container_runtime_seconds"] = elapsed_seconds(
                                terminated.started_at, terminated.finished_at)
                            record["startup_seconds"] = elapsed_seconds(
                                pod.status.start_time, terminated.started_at)
                    logs = self.core.read_namespaced_pod_log(
                        pod_name, self.namespace)
                    (record["metrics"], record["transfer_mode"],
                     record["transfer_source"]) = task_metrics(logs)
                except ApiException as exc:
                    record["collection_error"] = exc.reason
            tasks.append(record)
        return {
            "name": name,
            "shape": metadata.get("labels", {}).get(
                "ts.io/experiment-shape"),
            "state": status.get("state"),
            "creation_timestamp": metadata.get("creationTimestamp"),
            "started_at": status.get("startedAt"),
            "finished_at": status.get("finishedAt"),
            "response_seconds": elapsed_seconds(
                metadata.get("creationTimestamp"), status.get("finishedAt")),
            "execution_seconds": elapsed_seconds(
                status.get("startedAt"), status.get("finishedAt")),
            "tasks_finished": status.get("tasksFinished", 0),
            "tasks_failed": status.get("tasksFailed", 0),
            "message": status.get("message", ""),
            "tasks": tasks,
        }

    def scheduler_events(self, workflow_prefix):
        pod = self.wait_for_scheduler()
        logs = self.core.read_namespaced_pod_log(
            pod.metadata.name, SCHEDULER_NAMESPACE)
        relevant = [
            line for line in logs.splitlines() if workflow_prefix in line
        ]
        return {
            "preemption_lines": [
                line for line in relevant if "[PREEMPT]" in line],
            "binding_lines": [line for line in relevant if "[BIND]" in line],
            "error_lines": [
                line for line in relevant
                if "ERROR" in line or "failed" in line.lower()
            ],
        }

    def cluster_metadata(self):
        nodes = []
        for node in self.core.list_node().items:
            labels = node.metadata.labels or {}
            annotations = node.metadata.annotations or {}
            nodes.append({
                "name": node.metadata.name,
                "node_type": labels.get("node-type"),
                "capacity_cpu": labels.get("ts.capacity/cpu"),
                "capacity_memory_mib": labels.get("ts.capacity/memory"),
                "cooling_class": labels.get("ts.io/cooling-class"),
                "thermal_throttle_c": labels.get("ts.io/thermal-throttle-c"),
                "cpu_temperature_c": annotations.get("ts.io/cpu-temp-c"),
                "bandwidth_annotation_count": sum(
                    key.startswith("ts.io/bw-from-")
                    for key in annotations),
            })
        _, active = config.list_kube_config_contexts()
        return {
            "context": active["name"] if active else None,
            "nodes": nodes,
            "evidence_scope": (
                "single-host Kind worker containers with Docker cgroup caps; "
                "not independent physical machines"),
        }


def run_trial(experiment, policy, repetition, run_id, priority_delay_seconds):
    pressure, critical, regular = workflow_batch(policy, repetition, run_id)
    trial_started = utc_now()
    experiment.create_workflow(pressure)
    time.sleep(priority_delay_seconds)
    experiment.create_workflow(critical)
    for workflow in regular:
        experiment.create_workflow(workflow)
    bodies = [pressure, critical] + regular
    names = [body["metadata"]["name"] for body in bodies]
    experiment.wait_for_workflows(names)
    workflows = [experiment.collect_workflow(name) for name in names]
    finished = [
        parse_timestamp(workflow["finished_at"])
        for workflow in workflows if workflow.get("finished_at")
    ]
    created = [
        parse_timestamp(workflow["creation_timestamp"])
        for workflow in workflows if workflow.get("creation_timestamp")
    ]
    responses = [
        workflow["response_seconds"] for workflow in workflows
        if workflow.get("response_seconds") is not None
    ]
    prefix = f"{EXPERIMENT_PREFIX}{policy}-r{repetition}"
    events = experiment.scheduler_events(prefix)
    return {
        "policy": policy,
        "repetition": repetition,
        "started_at": trial_started,
        "finished_at": utc_now(),
        "priority_delay_seconds": priority_delay_seconds,
        "makespan_seconds": (
            (max(finished) - min(created)).total_seconds()
            if finished and created else None),
        "mean_response_seconds": (
            statistics.fmean(responses) if responses else None),
        "preemptions": len(events["preemption_lines"]),
        "scheduler_events": events,
        "workflows": workflows,
    }


def write_results(path, document):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policies", nargs="+", default=["legacy-v1", "adaptive"],
        choices=SUPPORTED_POLICIES)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--topology-name",
        default="unspecified",
        help="stable label for the Kind or physical cluster topology",
    )
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--priority-delay-seconds", type=float, default=7)
    parser.add_argument(
        "--templates", default="experiments/k8s/tasktemplates.yaml")
    parser.add_argument(
        "--output", default="results/k8s-kind/policy-comparison.json")
    parser.add_argument("--keep-workflows", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    experiment = KubernetesExperiment(args.namespace, args.timeout_seconds)
    experiment.apply_tasktemplates(args.templates)
    experiment.clean_experiment_objects()
    document = {
        "schema_version": 1,
        "experiment": EXPERIMENT_NAME,
        "run_id": run_id,
        "started_at": utc_now(),
        "completed_at": None,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "priority_delay_seconds": args.priority_delay_seconds,
        "topology_name": args.topology_name,
        "cluster": experiment.cluster_metadata(),
        "policies": {},
        "summary": {},
    }
    write_results(args.output, document)

    try:
        for policy in args.policies:
            document["policies"][policy] = {
                "scheduler_runs": [],
                "trials": [],
            }
        for repetition in range(1, args.repetitions + 1):
            for policy in policy_order_for_repetition(
                    args.policies, repetition):
                experiment.clean_experiment_objects()
                scheduler = experiment.configure_scheduler(
                    policy, args.seed, run_id)
                policy_result = document["policies"][policy]
                policy_result["scheduler_runs"].append({
                    "repetition": repetition,
                    **scheduler,
                })
                trial = run_trial(
                    experiment, policy, repetition, run_id,
                    args.priority_delay_seconds)
                policy_result["trials"].append(trial)
                write_results(args.output, document)
                time.sleep(6)
        for policy in args.policies:
            policy_result = document["policies"][policy]
            policy_result["summary"] = summarize_policy(
                policy_result["trials"])
            document["summary"][policy] = policy_result["summary"]
            write_results(args.output, document)
        document["completed_at"] = utc_now()
        write_results(args.output, document)
    except Exception as exc:
        document["error"] = f"{type(exc).__name__}: {exc}"
        document["completed_at"] = utc_now()
        write_results(args.output, document)
        raise
    finally:
        if not args.keep_workflows:
            experiment.clean_experiment_objects()

    for policy in args.policies:
        summary = document["summary"][policy]
        print(
            f"{policy}: completion={summary['completion_rate']:.0%}, "
            f"median_response={summary['median_workflow_response_seconds']:.2f}s, "
            f"critical={summary['median_critical_response_seconds']:.2f}s, "
            f"makespan={summary['median_trial_makespan_seconds']:.2f}s, "
            f"preemptions={summary['preemptions']}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()