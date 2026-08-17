"""Canonical multi-workflow arrival descriptions and simulation compilation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from models.enums import PriorityClass
from simulation.engine import SimulationResult
from simulation.schema import (
    DependencySpec,
    ModelValidationError,
    WorkflowSpec,
    load_workflow,
)


@dataclass(frozen=True)
class WorkflowSubmission:
    submission_id: str
    workflow: WorkflowSpec
    arrival_s: float = 0.0
    priority: PriorityClass | None = None
    preemptable: bool | None = None
    deadline_s: float | None = None

    def __post_init__(self):
        if not self.submission_id:
            raise ModelValidationError("submission id must not be empty")
        if self.arrival_s < 0:
            raise ModelValidationError("submission arrival_s must be >= 0")
        if self.deadline_s is not None and self.deadline_s <= 0:
            raise ModelValidationError("submission deadline_s must be > 0")


@dataclass(frozen=True)
class WorkloadSpec:
    workload_id: str
    submissions: tuple[WorkflowSubmission, ...]
    description: str = ""
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1:
            raise ModelValidationError(
                f"unsupported workload schema_version {self.schema_version}")
        if not self.workload_id or not self.submissions:
            raise ModelValidationError(
                "workload id and at least one submission are required")
        ids = [submission.submission_id for submission in self.submissions]
        if len(ids) != len(set(ids)):
            raise ModelValidationError("workload submission ids must be unique")

    def compile(self) -> WorkflowSpec:
        tasks = []
        dependencies = []
        tags = {"multi-workflow"}
        for submission in self.submissions:
            workflow = submission.workflow
            priority = submission.priority or workflow.priority
            preemptable = (
                submission.preemptable
                if submission.preemptable is not None else workflow.preemptable
            )
            prefix = f"{submission.submission_id}::"
            tags.update(workflow.tags)
            for task in workflow.tasks:
                tasks.append(replace(
                    task,
                    task_id=prefix + task.task_id,
                    gang_group_id=(prefix + task.gang_group_id
                                   if task.gang_group_id else None),
                    release_s=submission.arrival_s + task.release_s,
                    priority=task.priority or priority,
                    preemptable=(task.preemptable
                                 if task.preemptable is not None else preemptable),
                    workflow_instance_id=submission.submission_id,
                ))
            for dependency in workflow.dependencies:
                dependencies.append(DependencySpec(
                    parent=prefix + dependency.parent,
                    child=prefix + dependency.child,
                    data_mib=dependency.data_mib,
                ))
        return WorkflowSpec(
            workflow_id=self.workload_id,
            tasks=tuple(tasks),
            dependencies=tuple(dependencies),
            priority=PriorityClass.BATCH,
            preemptable=True,
            family="multi-workflow",
            tags=frozenset(tags),
            description=self.description,
        )


@dataclass(frozen=True)
class SubmissionResult:
    submission_id: str
    arrival_s: float
    completion_s: float
    response_s: float
    deadline_s: float | None
    deadline_met: bool | None
    lateness_s: float


def submission_results(workload: WorkloadSpec,
                       result: SimulationResult) -> dict[str, SubmissionResult]:
    metrics = {}
    for submission in workload.submissions:
        prefix = f"{submission.submission_id}::"
        completion_s = max(
            task.finished_at_s for task_id, task in result.tasks.items()
            if task_id.startswith(prefix)
        )
        relative_deadline = (
            submission.deadline_s
            if submission.deadline_s is not None else submission.workflow.deadline_s
        )
        absolute_deadline = (
            submission.arrival_s + relative_deadline
            if relative_deadline is not None else None
        )
        metrics[submission.submission_id] = SubmissionResult(
            submission_id=submission.submission_id,
            arrival_s=submission.arrival_s,
            completion_s=completion_s,
            response_s=completion_s - submission.arrival_s,
            deadline_s=relative_deadline,
            deadline_met=(completion_s <= absolute_deadline
                          if absolute_deadline is not None else None),
            lateness_s=(max(0.0, completion_s - absolute_deadline)
                        if absolute_deadline is not None else 0.0),
        )
    return metrics


def _priority(raw: Any) -> PriorityClass | None:
    if raw is None:
        return None
    try:
        return PriorityClass[str(raw).upper()]
    except KeyError as exc:
        raise ModelValidationError(f"unknown submission priority '{raw}'") from exc


def load_workload(path: str | Path) -> WorkloadSpec:
    path = Path(path)
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, Mapping):
        raise ModelValidationError(f"'{path}' must contain a YAML mapping")
    workload = data.get("workload", data)
    submissions = []
    for raw in workload.get("submissions", ()):
        workflow_path = (path.parent / str(raw["workflow_file"])).resolve()
        workflow = load_workflow(workflow_path)
        count = int(raw.get("count", 1))
        interval_s = float(raw.get("interval_s", 0.0))
        if count < 1 or interval_s < 0:
            raise ModelValidationError(
                "submission count must be >= 1 and interval_s must be >= 0")
        for index in range(count):
            base_id = str(raw["id"])
            submission_id = base_id if count == 1 else f"{base_id}-{index + 1}"
            submissions.append(WorkflowSubmission(
                submission_id=submission_id,
                workflow=workflow,
                arrival_s=float(raw.get("arrival_s", 0.0)) + index * interval_s,
                priority=_priority(raw.get("priority")),
                preemptable=(bool(raw["preemptable"])
                             if "preemptable" in raw else None),
                deadline_s=(float(raw["deadline_s"])
                            if raw.get("deadline_s") is not None else None),
            ))
    return WorkloadSpec(
        workload_id=str(workload["id"]),
        submissions=tuple(submissions),
        description=str(workload.get("description", "")),
        schema_version=int(data.get("schema_version", 1)),
    )