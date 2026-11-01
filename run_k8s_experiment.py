"""Run the same-host Kind validation protocol for one cluster profile.

Protocol (per profile):

1. Install TaskTemplates, wait until every worker has measured bandwidth
   annotations, and clean previous experiment objects.
2. Calibration: run the ``calibrate`` policy on independent tasks until each
   (template, node) pair has three successful runs. The resulting learned
   profile is the shared warm start of every trial, and the measured task
   work, startup, and orchestration latency calibrate the simulator twin.
3. Trials: for each repetition, run every (batch, policy) pair once in a
   seeded random order. Each trial restarts ts-scheduler with the policy and
   a fresh copy of the warm profile, submits one batch from
   ``experiments/k8s/batches.yaml``, and records every workflow, task, Pod,
   binding, preemption, and transfer.

All timings come from the cluster clock (controller, scheduler, and task
containers share the Docker VM kernel clock): workflow response is the latest
task finish minus the controller's sub-second admission time.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
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
EXPERIMENT_NAME = "kind-validation"
SCHEDULER_DEPLOYMENT = "ts-scheduler"
SCHEDULER_NAMESPACE = "ts-system"
SUPPORTED_POLICIES = (
    "kube-default", "legacy-v1", "legacy-tuned", "eft", "heft", "heft-ra",
    "heft-ra-cap", "adaptive-no-preempt", "adaptive",
)
# Scheduler log lines that signal a problem anywhere in the scheduler, not
# tied to one Pod; they carry no trial prefix and must not be filtered out.
SCHEDULER_ERROR_MARKERS = (
    "tick error", "Traceback", "watch error", "refresh failed",
    "event failed", "failed to parse pod", "invalid workflow DAG",
)
CALIBRATION_SAMPLES = 3
_AT = re.compile(r"at=([0-9.]+)")
_POD = re.compile(r"'([^']+)'")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def epoch(value) -> float | None:
    parsed = parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def elapsed_seconds(start, finish):
    start_dt = parse_timestamp(start)
    finish_dt = parse_timestamp(finish)
    if start_dt is None or finish_dt is None:
        return None
    return max(0.0, (finish_dt - start_dt).total_seconds())


def percentile(values, fraction):
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def trial_schedule(batches, policies, repetitions, seed):
    """Seeded random order of every (batch, policy) pair per repetition."""
    rng = random.Random(seed)
    schedule = []
    for repetition in range(1, repetitions + 1):
        pairs = [(batch, policy) for batch in batches for policy in policies]
        rng.shuffle(pairs)
        schedule.extend((repetition, batch, policy) for batch, policy in pairs)
    return schedule


def load_batches(path):
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported batch schema_version")
    return document


def workflow_body(name, workflow, run_id, trial_id, batch, policy):
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Workflow",
        "metadata": {
            "name": name,
            "labels": {
                "ts.io/experiment": EXPERIMENT_NAME,
                "ts.io/experiment-run": run_id,
                "ts.io/experiment-trial": trial_id,
                "ts.io/experiment-batch": batch,
                "ts.io/experiment-policy": policy,
                "ts.io/experiment-shape": workflow["name"],
            },
        },
        "spec": {
            "priority": workflow["priority"],
            "preemptable": bool(workflow.get("preemptable", True)),
            "workflowClass": workflow.get("workflow_class", "BATCH"),
            "tasks": [
                {key: value for key, value in task.items()} for task in workflow["tasks"]
            ],
        },
    }


def batch_bodies(document, batch, run_id, trial_id, policy):
    """Return (arrival, body) pairs for one trial."""
    bodies = []
    for workflow in document["batches"][batch]["workflows"]:
        name = f"{EXPERIMENT_PREFIX}{trial_id}-{workflow['name']}"
        bodies.append((workflow.get("arrival", "start"),
                       workflow_body(name, workflow, run_id, trial_id, batch, policy)))
    return bodies


def calibration_bodies(templates, workers, run_id, samples=CALIBRATION_SAMPLES):
    bodies = []
    for template in templates:
        workflow = {
            "name": f"cal-{template.removeprefix('experiment-')}",
            "priority": "BATCH",
            "workflow_class": "BATCH",
            "tasks": [
                {"name": f"sample-{index:02d}", "template": template}
                for index in range(1, samples * workers + 1)
            ],
        }
        name = f"{EXPERIMENT_PREFIX}{workflow['name']}"
        bodies.append(workflow_body(
            name, workflow, run_id, "calibration", "calibration", "calibrate"))
    return bodies


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
            transfer_mode = transfer_mode or "local"
            transfer_source = line
        elif line.startswith("[FETCH]"):
            transfer_mode = "remote"
            transfer_source = line
    return result, transfer_mode, transfer_source


def parse_scheduler_events(log_text, prefix):
    """Bind and preemption events (with cluster-clock epochs) for a trial."""
    binds, preemptions, errors, scheduler_errors, completions = [], [], [], [], []
    for line in log_text.splitlines():
        if any(marker in line for marker in SCHEDULER_ERROR_MARKERS):
            scheduler_errors.append(line)
        if prefix not in line:
            continue
        at = _AT.search(line)
        pod = _POD.search(line)
        if line.startswith("[BIND]") and pod:
            node = line.split("->")[-1].split("'")[1] if "->" in line else None
            binds.append({"pod": pod.group(1), "node": node,
                          "at": float(at.group(1)) if at else None})
        elif line.startswith("[DONE]") and pod:
            completions.append({"pod": pod.group(1),
                                "at": float(at.group(1)) if at else None})
        elif line.startswith("[PREEMPT]") and pod:
            preemptions.append({"pod": pod.group(1),
                                "at": float(at.group(1)) if at else None,
                                "line": line})
        if "ERROR" in line or "tick error" in line or "failed" in line.lower():
            errors.append(line)
    return {"binds": binds, "preemptions": preemptions, "error_lines": errors,
            "scheduler_errors": scheduler_errors, "completions": completions}


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
        names = []
        for body in yaml.safe_load_all(Path(path).read_text(encoding="utf-8")):
            if not body:
                continue
            body.setdefault("metadata", {})["namespace"] = self.namespace
            name = body["metadata"]["name"]
            names.append(name)
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
        return names

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
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            workflows = self.custom.list_namespaced_custom_object(
                GROUP, VERSION, self.namespace, WORKFLOWS).get("items", [])
            remaining = [
                item for item in workflows
                if item["metadata"]["name"].startswith(EXPERIMENT_PREFIX)]
            pods = [
                pod for pod in self.core.list_namespaced_pod(self.namespace).items
                if (pod.metadata.labels or {}).get(
                    "ts.io/workflow", "").startswith(EXPERIMENT_PREFIX)]
            if not remaining and not pods:
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
                pod.metadata.name, SCHEDULER_NAMESPACE,
                container="nginx", command=command,
                stderr=True, stdin=False, stdout=True, tty=False,
            )

    def worker_nodes(self):
        return [
            node for node in self.core.list_node().items
            if "node-type" in (node.metadata.labels or {})
        ]

    def refresh_bandwidth(self, interval_s=86400, timeout_seconds=900):
        """Run one fresh probe round, then keep the probe idle during trials.

        The DaemonSet is restarted with a long interval; each probe measures
        every peer once at start-up. Waiting for new timestamps on every
        worker guarantees the scheduler and the twin see the shaped links.
        """
        stamp = "ts.io/bw-last-probed-at"
        before = {
            node.metadata.name: (node.metadata.annotations or {}).get(stamp)
            for node in self.worker_nodes()}
        patch = {"spec": {"template": {
            "metadata": {"annotations": {"ts.io/probe-restarted-at": utc_now()}},
            "spec": {"containers": [{
                "name": "probe",
                "env": [{"name": "TS_PROBE_INTERVAL_S", "value": str(interval_s)}],
            }]},
        }}}
        self.apps.patch_namespaced_daemon_set("ts-bw-probe", SCHEDULER_NAMESPACE, patch)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            nodes = self.worker_nodes()
            if all(
                (node.metadata.annotations or {}).get(stamp)
                not in (None, before.get(node.metadata.name))
                for node in nodes
            ):
                self.wait_for_bandwidth(timeout_seconds=60)
                return
            time.sleep(5)
        raise TimeoutError("bandwidth probe did not complete a fresh round")

    def wait_for_bandwidth(self, timeout_seconds=600):
        """Wait until every worker reports a measured rate from every peer."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            nodes = self.worker_nodes()
            names = {node.metadata.name for node in nodes}
            complete = True
            for node in nodes:
                annotations = node.metadata.annotations or {}
                peers = {
                    key.removeprefix("ts.io/bw-from-")
                    for key in annotations if key.startswith("ts.io/bw-from-")}
                if not (names - {node.metadata.name}) <= peers:
                    complete = False
            if complete:
                return
            time.sleep(5)
        raise TimeoutError("bandwidth annotations did not appear for all workers")

    def write_profile_configmap(self, name, raw_profiles):
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name),
            data={"profiles": raw_profiles} if raw_profiles else {},
        )
        try:
            self.core.replace_namespaced_config_map(name, self.namespace, body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_config_map(self.namespace, body)

    def read_profile_configmap(self, name):
        try:
            return (self.core.read_namespaced_config_map(
                name, self.namespace).data or {}).get("profiles", "")
        except ApiException as exc:
            if exc.status == 404:
                return ""
            raise

    def configure_scheduler(self, policy, seed, configmap, profile_path):
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
                                {"name": "TS_PROFILE_PATH", "value": profile_path},
                                {"name": "TS_PROFILE_CONFIGMAP", "value": configmap},
                                {"name": "TS_PERSIST_INTERVAL_S", "value": "5"},
                            ],
                        }],
                    },
                },
            },
        }
        self.apps.patch_namespaced_deployment(
            SCHEDULER_DEPLOYMENT, SCHEDULER_NAMESPACE, patch)
        pod = self.wait_for_scheduler()
        deadline = time.monotonic() + 90
        log_text = ""
        while time.monotonic() < deadline:
            log_text = self.core.read_namespaced_pod_log(
                pod.metadata.name, SCHEDULER_NAMESPACE)
            if f"Policy: " in log_text and f"({policy})" in log_text:
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"scheduler did not start requested policy {policy}: {log_text}")
        loaded = "Loaded" in log_text and configmap in log_text
        configuration = {
            "pod": pod.metadata.name,
            "policy": policy,
            "profile_configmap": configmap,
            "profile_path": profile_path,
            "warm_profile_loaded": loaded,
        }
        if policy.startswith("legacy"):
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
                    and (status.available_replicas or 0) == desired
                    and (status.replicas or 0) == desired):
                pods = self.core.list_namespaced_pod(
                    SCHEDULER_NAMESPACE,
                    label_selector="app.kubernetes.io/name=ts-scheduler",
                ).items
                ready = [
                    pod for pod in pods
                    if pod.status.phase == "Running"
                    and pod.metadata.deletion_timestamp is None
                    and any(
                        condition.type == "Ready" and condition.status == "True"
                        for condition in (pod.status.conditions or [])
                    )
                ]
                if len(ready) == 1:
                    return ready[0]
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
        labels = metadata.get("labels", {})
        tasks = []
        for task_status in status.get("tasks", []):
            pod_name = task_status.get("podName")
            record = {
                "name": task_status.get("name"),
                "state": task_status.get("state"),
                "pod_name": pod_name,
                "node_name": task_status.get("nodeName"),
                "attempts": task_status.get("attempts"),
                "ready_at": task_status.get("readyAt"),
                "pod_created_at": task_status.get("podCreatedAt"),
                "started_at": task_status.get("startedAt"),
                "finished_at": task_status.get("finishedAt"),
                "template": None,
                "pod_phase": None,
                "restart_count": None,
                "metrics": {},
                "transfer_mode": None,
                "transfer_source": None,
            }
            if pod_name:
                try:
                    pod = self.core.read_namespaced_pod(pod_name, self.namespace)
                    record["template"] = (pod.metadata.annotations or {}).get(
                        "ts.scheduler/task_template_id")
                    record["pod_phase"] = pod.status.phase
                    statuses = pod.status.container_statuses or []
                    if statuses:
                        record["restart_count"] = statuses[0].restart_count
                    logs = self.core.read_namespaced_pod_log(
                        pod_name, self.namespace)
                    (record["metrics"], record["transfer_mode"],
                     record["transfer_source"]) = task_metrics(logs)
                except ApiException as exc:
                    record["collection_error"] = exc.reason
            tasks.append(record)
        finished = [epoch(task["finished_at"]) for task in tasks if task["finished_at"]]
        admitted = epoch(status.get("admittedAt"))
        completion = max(finished) if finished else None
        return {
            "name": name,
            "shape": labels.get("ts.io/experiment-shape"),
            "priority": workflow["spec"].get("priority"),
            "state": status.get("state"),
            "creation_timestamp": metadata.get("creationTimestamp"),
            "admitted_at": status.get("admittedAt"),
            "started_at": status.get("startedAt"),
            "finished_at": status.get("finishedAt"),
            "completion_epoch": completion,
            "admitted_epoch": admitted,
            "response_seconds": (
                completion - admitted
                if completion is not None and admitted is not None else None),
            "tasks_finished": status.get("tasksFinished", 0),
            "tasks_failed": status.get("tasksFailed", 0),
            "message": status.get("message", ""),
            "tasks": tasks,
        }

    def scheduler_events(self, prefix):
        pod = self.wait_for_scheduler()
        logs = self.core.read_namespaced_pod_log(
            pod.metadata.name, SCHEDULER_NAMESPACE)
        return parse_scheduler_events(logs, prefix)

    def cluster_metadata(self):
        nodes = []
        for node in self.worker_nodes():
            labels = node.metadata.labels or {}
            annotations = node.metadata.annotations or {}
            nodes.append({
                "name": node.metadata.name,
                "node_type": labels.get("node-type"),
                "capacity_cpu": labels.get("ts.capacity/cpu"),
                "capacity_memory_mib": labels.get("ts.capacity/memory"),
                "kind_profile": labels.get("ts.io/kind-profile"),
                "cpu_temperature_c": annotations.get("ts.io/cpu-temp-c"),
                "bandwidth_mib_s_by_peer": {
                    key.removeprefix("ts.io/bw-from-"): value
                    for key, value in annotations.items()
                    if key.startswith("ts.io/bw-from-")
                },
                "netem_delay_ms": annotations.get("ts.io/netem-delay-ms"),
                "netem_rate_mbit": annotations.get("ts.io/netem-rate-mbit"),
                "emulated_speed": annotations.get("ts.io/emulated-speed"),
            })
        return {
            "nodes": nodes,
            "evidence_scope": (
                "single-host Kind worker containers with Docker cgroup caps, "
                "emulated speeds, and tc-netem links; not independent machines"),
        }


def run_workflows(experiment, timed_bodies, delay_seconds):
    started = utc_now()
    names = []
    for arrival, body in timed_bodies:
        if arrival == "start":
            experiment.create_workflow(body)
            names.append(body["metadata"]["name"])
    delayed = [body for arrival, body in timed_bodies if arrival != "start"]
    if delayed:
        time.sleep(delay_seconds)
        for body in delayed:
            experiment.create_workflow(body)
            names.append(body["metadata"]["name"])
    experiment.wait_for_workflows(names)
    return started, [experiment.collect_workflow(name) for name in names]


def trial_summary(workflows):
    responses = [w["response_seconds"] for w in workflows
                 if w.get("response_seconds") is not None]
    finished = [w["completion_epoch"] for w in workflows if w.get("completion_epoch")]
    admitted = [w["admitted_epoch"] for w in workflows if w.get("admitted_epoch")]
    return {
        "makespan_seconds": (max(finished) - min(admitted)
                             if finished and admitted else None),
        "mean_response_seconds": statistics.fmean(responses) if responses else None,
        "median_response_seconds": statistics.median(responses) if responses else None,
        "p95_response_seconds": percentile(responses, 0.95),
    }


def write_results(path, document):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")


def calibrate(experiment, templates, run_id, seed, workers):
    """Run the warm-up phase and return its raw evidence and learned profile."""
    configmap = f"ts-profiles-{run_id}-calibration"
    experiment.write_profile_configmap(configmap, "")
    scheduler = experiment.configure_scheduler(
        "calibrate", seed, configmap, f"/data/profiles-{run_id}-calibration.json")
    bodies = calibration_bodies(templates, workers, run_id)
    started = utc_now()
    for body in bodies:
        experiment.create_workflow(body)
    names = [body["metadata"]["name"] for body in bodies]
    experiment.wait_for_workflows(names)
    workflows = [experiment.collect_workflow(name) for name in names]
    events = experiment.scheduler_events(f"{EXPERIMENT_PREFIX}cal-")
    deadline = time.monotonic() + 60
    profiles = ""
    while time.monotonic() < deadline:
        time.sleep(6)
        profiles = experiment.read_profile_configmap(configmap)
        if profiles and all(template in profiles for template in templates):
            break
    if not profiles:
        raise RuntimeError("calibration profile was not persisted")
    return {
        "started_at": started,
        "finished_at": utc_now(),
        "scheduler": scheduler,
        "workflows": workflows,
        "scheduler_events": events,
        "profiles_json": profiles,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", required=True,
                        help="Kind profile name from clusters/kind_profiles.yaml")
    parser.add_argument("--policies", nargs="+", default=list(SUPPORTED_POLICIES),
                        choices=SUPPORTED_POLICIES)
    parser.add_argument("--batches", nargs="+", default=["priority", "dag"])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--templates", default="experiments/k8s/tasktemplates.yaml")
    parser.add_argument("--batch-file", default="experiments/k8s/batches.yaml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")

    batches = load_batches(args.batch_file)
    for batch in args.batches:
        if batch not in batches["batches"]:
            parser.error(f"unknown batch {batch!r}")
    run_id = datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    experiment = KubernetesExperiment(args.namespace, args.timeout_seconds)
    templates = experiment.apply_tasktemplates(args.templates)
    experiment.clean_experiment_objects()
    experiment.refresh_bandwidth()
    schedule = trial_schedule(args.batches, args.policies, args.repetitions, args.seed)
    document = {
        "schema_version": 2,
        "experiment": EXPERIMENT_NAME,
        "profile": args.profile,
        "run_id": run_id,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "policies": args.policies,
        "batches": args.batches,
        "batch_definitions": {name: batches["batches"][name] for name in args.batches},
        "priority_delay_seconds": batches.get("priority_delay_s", 0),
        "templates": templates,
        "schedule": [list(item) for item in schedule],
        "started_at": utc_now(),
        "completed_at": None,
        "cluster": experiment.cluster_metadata(),
        "calibration": None,
        "trials": [],
    }
    write_results(args.output, document)

    try:
        workers = len(experiment.worker_nodes())
        document["calibration"] = calibrate(
            experiment, templates, run_id, args.seed, workers)
        write_results(args.output, document)
        warm_profiles = document["calibration"]["profiles_json"]
        experiment.clean_experiment_objects()
        for index, (repetition, batch, policy) in enumerate(schedule, start=1):
            trial_id = f"t{index:03d}"
            configmap = f"ts-profiles-{run_id}-{trial_id}"
            experiment.write_profile_configmap(configmap, warm_profiles)
            scheduler = experiment.configure_scheduler(
                policy, args.seed, configmap, f"/data/profiles-{run_id}-{trial_id}.json")
            bodies = batch_bodies(batches, batch, run_id, trial_id, policy)
            started, workflows = run_workflows(
                experiment, bodies, float(batches.get("priority_delay_s", 0)))
            events = experiment.scheduler_events(f"{EXPERIMENT_PREFIX}{trial_id}-")
            document["trials"].append({
                "trial_id": trial_id,
                "repetition": repetition,
                "batch": batch,
                "policy": policy,
                "started_at": started,
                "finished_at": utc_now(),
                "scheduler": scheduler,
                "scheduler_events": events,
                "preemptions": len(events["preemptions"]),
                "workflows": workflows,
                **trial_summary(workflows),
            })
            write_results(args.output, document)
            experiment.clean_experiment_objects()
            print(f"[{index}/{len(schedule)}] {batch}/{policy} rep {repetition}: "
                  f"makespan={document['trials'][-1]['makespan_seconds']:.2f}s "
                  f"mean_response={document['trials'][-1]['mean_response_seconds']:.2f}s "
                  f"preemptions={len(events['preemptions'])}", flush=True)
        document["completed_at"] = utc_now()
        write_results(args.output, document)
    except Exception as exc:
        document["error"] = f"{type(exc).__name__}: {exc}"
        document["completed_at"] = utc_now()
        write_results(args.output, document)
        raise
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
