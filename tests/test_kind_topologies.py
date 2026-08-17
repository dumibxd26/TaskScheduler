from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def _workers(name):
    document = yaml.safe_load((ROOT / "clusters" / name).read_text())
    return [node for node in document["nodes"] if node["role"] == "worker"]


def test_homogeneous_kind_topology_has_equal_workers():
    workers = _workers("kind_homogeneous.yaml")
    capacities = {
        (
            worker["labels"]["node-type"],
            worker["labels"]["ts.capacity/cpu"],
            worker["labels"]["ts.capacity/memory"],
            worker["labels"]["ts.capacity/io"],
        )
        for worker in workers
    }

    assert len(workers) == 3
    assert len(capacities) == 1


def test_skewed_kind_topology_exercises_three_resource_types():
    workers = _workers("kind_resource_skewed.yaml")
    labels = [worker["labels"] for worker in workers]

    assert {item["node-type"] for item in labels} == {
        "CPU_OPT", "MEM_OPT", "IO_OPT"}
    assert len({item["ts.capacity/cpu"] for item in labels}) == 2
    assert len({item["ts.capacity/memory"] for item in labels}) == 3
    assert len({item["ts.capacity/io"] for item in labels}) == 2