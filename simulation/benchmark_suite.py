"""Declarative factorial workflow generation for scheduler research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from simulation.schema import DependencySpec, ModelValidationError, TaskSpec, WorkflowSpec


WORK_FIELDS = (
    "compute_gflop",
    "memory_read_mib",
    "memory_write_mib",
    "disk_read_mib",
    "disk_write_mib",
)
SUPPORTED_TOPOLOGIES = frozenset({
    "independent", "linear", "fanout", "fanin", "forkjoin",
})


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    split: str
    resource_pattern: str
    topology: str
    task_count: int
    work_scale: float
    data_scale: float
    workflow: WorkflowSpec


def _positive_values(name: str, values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or any(value <= 0 for value in result):
        raise ModelValidationError(f"{name} must contain positive values")
    return result


def _dependencies(topology: str, task_ids: list[str], data_mib: float):
    def edge(parent_index: int, child_index: int) -> DependencySpec:
        return DependencySpec(
            parent=task_ids[parent_index],
            child=task_ids[child_index],
            data_mib=data_mib,
        )

    if topology == "independent":
        return ()
    if topology == "linear":
        return tuple(edge(index, index + 1) for index in range(len(task_ids) - 1))
    if topology == "fanout":
        return tuple(edge(0, index) for index in range(1, len(task_ids)))
    if topology == "fanin":
        return tuple(edge(index, len(task_ids) - 1)
                     for index in range(len(task_ids) - 1))
    if topology == "forkjoin":
        sink = len(task_ids) - 1
        return tuple(
            [edge(0, index) for index in range(1, sink)]
            + [edge(index, sink) for index in range(1, sink)]
        )
    raise ModelValidationError(f"unsupported benchmark topology '{topology}'")


def _task(archetype: Mapping[str, Any], task_id: str,
          work_scale: float, output_mib: float) -> TaskSpec:
    data = dict(archetype)
    data["id"] = task_id
    for field in WORK_FIELDS:
        data[field] = float(data.get(field, 0.0)) * work_scale
    data["output_mib"] = {"result": output_mib}
    return TaskSpec.from_mapping(data)


def load_benchmark_suite(path: str | Path) -> tuple[BenchmarkCase, ...]:
    """Expand a compact YAML matrix into immutable, validated workflows."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, Mapping):
        raise ModelValidationError(f"'{path}' must contain a YAML mapping")
    if int(document.get("schema_version", 1)) != 1:
        raise ModelValidationError("unsupported benchmark suite schema_version")
    suite = document.get("suite", document)
    suite_id = str(suite["id"])
    archetypes = suite.get("archetypes", {})
    patterns = suite.get("resource_patterns", {})
    topologies = tuple(str(value).lower() for value in suite.get("topologies", ()))
    unknown_topologies = set(topologies) - SUPPORTED_TOPOLOGIES
    if not topologies or unknown_topologies:
        raise ModelValidationError(
            f"invalid benchmark topologies: {sorted(unknown_topologies)}")
    base_data_mib = float(suite.get("base_data_mib", 0.0))
    if base_data_mib < 0:
        raise ModelValidationError("suite.base_data_mib must be >= 0")

    normalized_patterns: dict[str, tuple[str, ...]] = {}
    for pattern_name, sequence in patterns.items():
        names = tuple(str(value) for value in sequence)
        if not names or set(names) - set(archetypes):
            raise ModelValidationError(
                f"resource pattern '{pattern_name}' references unknown archetypes")
        normalized_patterns[str(pattern_name)] = names
    if not normalized_patterns:
        raise ModelValidationError("suite.resource_patterns must not be empty")

    cases = []
    seen_ids: set[str] = set()
    for split, split_data in suite.get("splits", {}).items():
        task_counts = tuple(int(value) for value in split_data.get("task_counts", ()))
        if not task_counts or any(value < 4 for value in task_counts):
            raise ModelValidationError(
                f"suite.splits.{split}.task_counts must be >= 4")
        work_scales = _positive_values(
            f"suite.splits.{split}.work_scales",
            split_data.get("work_scales", ()),
        )
        data_scales = _positive_values(
            f"suite.splits.{split}.data_scales",
            split_data.get("data_scales", ()),
        )
        for pattern_name, pattern in normalized_patterns.items():
            for topology in topologies:
                for task_count in task_counts:
                    for work_scale in work_scales:
                        for data_scale in data_scales:
                            scale_label = str(work_scale).replace(".", "p")
                            data_label = str(data_scale).replace(".", "p")
                            case_id = (
                                f"{suite_id}-{split}-{pattern_name}-{topology}-"
                                f"n{task_count}-w{scale_label}-d{data_label}"
                            )
                            if case_id in seen_ids:
                                raise ModelValidationError(
                                    f"duplicate generated benchmark id '{case_id}'")
                            seen_ids.add(case_id)
                            task_ids = [f"task-{index + 1:02d}"
                                        for index in range(task_count)]
                            output_mib = base_data_mib * data_scale
                            tasks = tuple(
                                _task(
                                    archetypes[pattern[index % len(pattern)]],
                                    task_id,
                                    work_scale,
                                    output_mib,
                                )
                                for index, task_id in enumerate(task_ids)
                            )
                            dependencies = _dependencies(
                                topology, task_ids, output_mib)
                            workflow = WorkflowSpec(
                                workflow_id=case_id,
                                tasks=tasks,
                                dependencies=dependencies,
                                family=f"factorial-{pattern_name}-{topology}",
                                tags=frozenset({
                                    "factorial", str(split), pattern_name, topology,
                                }),
                                description=(
                                    f"Generated {pattern_name} {topology} workflow "
                                    f"with {task_count} long virtual tasks."
                                ),
                            )
                            cases.append(BenchmarkCase(
                                case_id=case_id,
                                split=str(split),
                                resource_pattern=pattern_name,
                                topology=topology,
                                task_count=task_count,
                                work_scale=work_scale,
                                data_scale=data_scale,
                                workflow=workflow,
                            ))
    if not cases:
        raise ModelValidationError("benchmark suite generated no cases")
    return tuple(cases)