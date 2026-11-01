import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"C:\work_proj\TaskScheduler")
sys.path.insert(0, str(ROOT))

from run_k8s_experiment import (
    EXPERIMENT_PREFIX, KubernetesExperiment, batch_bodies, load_batches,
    run_workflows, trial_summary, utc_now, write_results,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    args = parser.parse_args()
    result = ROOT / "results" / "ryzen-HS"
    path = result / "kind-validation" / f"{args.profile}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    invalid = [
        i for i, trial in enumerate(document["trials"])
        if trial["scheduler"].get("warm_profile_loaded") is not True
    ]
    if not invalid:
        raise ValueError(f"No invalid startup-confirmation trials in {args.profile}")
    experiment = KubernetesExperiment("default", 900)
    experiment.apply_tasktemplates(ROOT / "experiments" / "k8s" / "tasktemplates.yaml")
    experiment.clean_experiment_objects()
    experiment.refresh_bandwidth()
    batches = load_batches(ROOT / "experiments" / "k8s" / "batches.yaml")
    retry_id = datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    document.setdefault("initial_completed_at", document["completed_at"])
    document.setdefault("integrity_repairs", [])
    document["repair_cluster_metadata"] = experiment.cluster_metadata()
    for index in invalid:
        previous = document["trials"][index]
        trial_id, policy, batch = (
            previous["trial_id"], previous["policy"], previous["batch"]
        )
        configmap = f"ts-profiles-retry-{retry_id}-{trial_id}"
        experiment.write_profile_configmap(
            configmap, document["calibration"]["profiles_json"])
        scheduler = experiment.configure_scheduler(
            policy, document["seed"], configmap, f"/data/{configmap}.json")
        if scheduler["warm_profile_loaded"] is not True:
            raise RuntimeError("Retry did not confirm loading the exact warm ConfigMap")
        bodies = batch_bodies(
            batches, batch, document["run_id"], trial_id, policy)
        started, workflows = run_workflows(
            experiment, bodies, float(batches.get("priority_delay_s", 0)))
        events = experiment.scheduler_events(f"{EXPERIMENT_PREFIX}{trial_id}-")
        document["trials"][index] = {
            "trial_id": trial_id,
            "repetition": previous["repetition"],
            "batch": batch, "policy": policy, "started_at": started,
            "finished_at": utc_now(), "scheduler": scheduler,
            "scheduler_events": events,
            "preemptions": len(events["preemptions"]),
            "workflows": workflows, **trial_summary(workflows),
        }
        document["integrity_repairs"].append({
            "trial_id": trial_id, "policy": policy, "batch": batch,
            "reason": "Missing warm-profile startup confirmation; readiness log race",
            "original_finished_at": previous["finished_at"],
            "rerun_finished_at": document["trials"][index]["finished_at"],
            "original_artifact": f"integrity-first-matrix/{args.profile}.json",
            "warm_calibration_reused": True,
        })
        document["completed_at"] = utc_now()
        write_results(path, document)
        experiment.clean_experiment_objects()
        print(f"Replaced invalid {args.profile}/{trial_id}/{batch}/{policy} "
              f"with confirmed-warm rerun, makespan="
              f"{document['trials'][index]['makespan_seconds']:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
