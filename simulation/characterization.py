"""Deterministic workload features for policy selection and experiment stratification."""

from __future__ import annotations

from dataclasses import dataclass

from simulation.schema import WorkflowSpec


@dataclass(frozen=True)
class WorkflowFeatures:
    task_count: int
    dependency_count: int
    topology: str
    root_count: int
    leaf_count: int
    critical_path_tasks: int
    max_parallelism: int
    max_fan_in: int
    max_fan_out: int
    dependency_density: float
    data_dependency_mib: float
    release_span_s: float
    checkpointable_fraction: float
    gang_fraction: float
    task_class_fractions: tuple[tuple[str, float], ...]

    def task_class_fraction(self, class_name: str) -> float:
        normalized = class_name.upper()
        return dict(self.task_class_fractions).get(normalized, 0.0)


def characterize_workflow(workflow: WorkflowSpec) -> WorkflowFeatures:
    """Summarize task mix and DAG shape without using execution outcomes."""
    task_ids = [task.task_id for task in workflow.tasks]
    parents = {task_id: set() for task_id in task_ids}
    children = {task_id: set() for task_id in task_ids}
    for dependency in workflow.dependencies:
        parents[dependency.child].add(dependency.parent)
        children[dependency.parent].add(dependency.child)

    depths: dict[str, int] = {}

    def depth(task_id: str) -> int:
        if task_id not in depths:
            depths[task_id] = 1 + max(
                (depth(parent) for parent in parents[task_id]),
                default=0,
            )
        return depths[task_id]

    for task_id in task_ids:
        depth(task_id)
    level_widths: dict[int, int] = {}
    for task_depth in depths.values():
        level_widths[task_depth] = level_widths.get(task_depth, 0) + 1

    task_count = len(task_ids)
    dependency_count = len(workflow.dependencies)
    max_fan_in = max((len(values) for values in parents.values()), default=0)
    max_fan_out = max((len(values) for values in children.values()), default=0)
    if dependency_count == 0:
        topology = "independent"
    elif (dependency_count == task_count - 1
          and max_fan_in <= 1 and max_fan_out <= 1):
        topology = "linear"
    elif max_fan_out > 1 and max_fan_in <= 1:
        topology = "fanout"
    elif max_fan_in > 1 and max_fan_out <= 1:
        topology = "fanin"
    else:
        topology = "dag"

    class_counts: dict[str, int] = {}
    for task in workflow.tasks:
        class_counts[task.task_class.name] = (
            class_counts.get(task.task_class.name, 0) + 1
        )
    class_fractions = tuple(
        (name, count / task_count)
        for name, count in sorted(class_counts.items())
    )
    maximum_edges = task_count * (task_count - 1) / 2
    release_times = [task.release_s for task in workflow.tasks]
    return WorkflowFeatures(
        task_count=task_count,
        dependency_count=dependency_count,
        topology=topology,
        root_count=sum(not values for values in parents.values()),
        leaf_count=sum(not values for values in children.values()),
        critical_path_tasks=max(depths.values()),
        max_parallelism=max(level_widths.values()),
        max_fan_in=max_fan_in,
        max_fan_out=max_fan_out,
        dependency_density=(dependency_count / maximum_edges
                            if maximum_edges else 0.0),
        data_dependency_mib=sum(
            dependency.data_mib for dependency in workflow.dependencies),
        release_span_s=max(release_times) - min(release_times),
        checkpointable_fraction=(
            sum(task.checkpointable for task in workflow.tasks) / task_count),
        gang_fraction=(
            sum(task.gang_group_id is not None for task in workflow.tasks)
            / task_count),
        task_class_fractions=class_fractions,
    )