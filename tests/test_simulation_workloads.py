from pathlib import Path

from simulation.engine import Simulator
from simulation.policies import AdaptivePolicy, FCFSPolicy
from simulation.schema import load_cluster
from simulation.workloads import load_workload, submission_results


ROOT = Path(__file__).parents[1]


def test_workload_loader_namespaces_repeated_workflows_and_arrivals():
    workload = load_workload(
        ROOT / "scenarios" / "workloads" / "mixed-arrivals.yaml")
    compiled = workload.compile()

    assert len(workload.submissions) == 6
    assert len(compiled.tasks) == sum(
        len(submission.workflow.tasks) for submission in workload.submissions)
    assert len({task.task_id for task in compiled.tasks}) == len(compiled.tasks)
    assert min(task.release_s for task in compiled.tasks
               if task.workflow_instance_id == "urgent") == 7


def test_priority_burst_preemption_reduces_urgent_response_time():
    cluster = load_cluster(
        ROOT / "scenarios" / "clusters" / "local-i5-8300h.yaml")
    workload = load_workload(
        ROOT / "scenarios" / "workloads" / "priority-burst.yaml")
    compiled = workload.compile()

    adaptive = Simulator(cluster, compiled, AdaptivePolicy()).run()
    fcfs = Simulator(cluster, compiled, FCFSPolicy()).run()
    adaptive_metrics = submission_results(workload, adaptive)
    fcfs_metrics = submission_results(workload, fcfs)

    assert adaptive_metrics["urgent"].response_s < fcfs_metrics["urgent"].response_s
    assert sum(task.preemptions for task in adaptive.tasks.values()) >= 1