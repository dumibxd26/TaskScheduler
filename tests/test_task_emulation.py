import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

from tasks.emulation import EmulationConfig, NodeSpeed, emulated_count


ROOT = Path(__file__).parents[1]


def _payload(stdout: str, prefix: str) -> dict:
    line = next(
        line for line in stdout.splitlines() if line.startswith(prefix))
    return json.loads(line[len(prefix):])


def _run_task(script: str, tmp_path: Path, **overrides: str
              ) -> subprocess.CompletedProcess[str]:
    env = {
        **{key: value for key, value in os.environ.items()
           if not key.startswith("TS_")},
        "PYTHONPATH": str(ROOT),
        "TS_OUTPUTS_DIR": str(tmp_path / "outputs"),
        "TS_INPUTS_DIR": str(tmp_path / "inputs"),
        **overrides,
    }
    return subprocess.run(
        [sys.executable, str(ROOT / "tasks" / script / f"{script}.py")],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


SPEEDS = json.dumps({
    "slow": {"cpu": 0.5, "memory": 0.25, "io": 0.5},
    "fast": {"cpu": 1.0, "memory": 1.0, "io": 2.0},
})


def test_emulation_config_rejects_invalid_scales(monkeypatch):
    monkeypatch.setenv("TS_WORK_SCALE", "0")

    with pytest.raises(ValueError, match="TS_WORK_SCALE"):
        EmulationConfig.from_env()


def test_node_speed_reads_the_node_entry_and_defaults_to_one(monkeypatch):
    monkeypatch.setenv("TS_NODE_SPEEDS", SPEEDS)
    monkeypatch.setenv("TS_NODE_NAME", "slow")
    assert NodeSpeed.from_env().factor("memory") == 0.25
    monkeypatch.setenv("TS_NODE_NAME", "unknown")
    assert NodeSpeed.from_env().factor("cpu") == 1.0
    monkeypatch.delenv("TS_NODE_SPEEDS")
    assert NodeSpeed.from_env().factor("io") == 1.0
    assert emulated_count(1000, EmulationConfig(), 0.5) == 2000


def test_cpu_task_inflates_work_on_a_slow_node(tmp_path):
    fast = _run_task("task_cpu", tmp_path, TS_CPU_ITERATIONS="2000",
                     TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="fast")
    slow = _run_task("task_cpu", tmp_path, TS_CPU_ITERATIONS="2000",
                     TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="slow")

    fast_metrics = _payload(fast.stdout, "__TS_METRICS__=")
    slow_metrics = _payload(slow.stdout, "__TS_METRICS__=")
    assert fast_metrics["emulation"]["physical_cpu_iterations"] == 2000
    assert slow_metrics["emulation"]["physical_cpu_iterations"] == 4000
    assert slow_metrics["emulation"]["speed_factor"] == 0.5
    assert slow_metrics["node_name"] == "slow"
    assert slow_metrics["runtime_seconds"] > 0
    assert slow_metrics["finished_at_epoch"] >= slow_metrics["started_at_epoch"]
    assert _payload(slow.stdout, "__TS_OUTPUT__=")["status"] == "workflow_complete"


def test_cpu_time_budget_scales_with_emulated_speed(tmp_path):
    fast = _run_task("task_cpu", tmp_path, TS_CPU_SECONDS="0.2",
                     TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="fast")
    slow = _run_task("task_cpu", tmp_path, TS_CPU_SECONDS="0.2",
                     TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="slow")

    fast_metrics = _payload(fast.stdout, "__TS_METRICS__=")["emulation"]
    slow_metrics = _payload(slow.stdout, "__TS_METRICS__=")["emulation"]
    assert fast_metrics["target_cpu_seconds"] == pytest.approx(0.2)
    assert slow_metrics["target_cpu_seconds"] == pytest.approx(0.4)
    assert slow_metrics["physical_cpu_iterations"] > fast_metrics[
        "physical_cpu_iterations"]


def test_memory_cpu_time_budget_uses_memory_speed(tmp_path):
    completed = _run_task(
        "task_mem", tmp_path, TS_MEMORY_MIB="1", TS_MEMORY_CPU_SECONDS="0.1",
        TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="slow")

    metrics = _payload(completed.stdout, "__TS_METRICS__=")["emulation"]
    assert metrics["target_cpu_seconds"] == pytest.approx(0.4)
    assert metrics["physical_passes"] >= 1


def test_cpu_task_keeps_legacy_array_size_contract(tmp_path):
    completed = _run_task("task_cpu", tmp_path, processed_array_size="2",
                          TS_WORK_SCALE="0.01")

    metrics = _payload(completed.stdout, "__TS_METRICS__=")["emulation"]
    assert metrics["logical_cpu_iterations"] == 20_000
    assert metrics["physical_cpu_iterations"] == 200


def test_memory_task_scales_copy_passes_by_memory_speed(tmp_path):
    completed = _run_task(
        "task_mem", tmp_path, TS_MEMORY_MIB="1", TS_MEMORY_PASSES="3",
        TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="slow")

    output = _payload(completed.stdout, "__TS_OUTPUT__=")
    metrics = _payload(completed.stdout, "__TS_METRICS__=")["emulation"]
    assert output == {"processed_array_size": 1}
    assert metrics["physical_passes"] == 12
    assert metrics["speed_kind"] == "memory"


def test_io_task_paces_writes_to_the_emulated_device_rate(tmp_path):
    started = time.perf_counter()
    completed = _run_task(
        "task_io", tmp_path, TS_IO_MIB="2", TS_IO_RATE_MIB_S="2",
        TS_NODE_SPEEDS=SPEEDS, TS_NODE_NAME="fast")
    elapsed = time.perf_counter() - started

    output = _payload(completed.stdout, "__TS_OUTPUT__=")
    metrics = _payload(completed.stdout, "__TS_METRICS__=")
    size = 2 * 1024 * 1024
    # 2 MiB at 2 MiB/s on a node with io speed 2.0 takes about half a second.
    assert 0.45 <= metrics["runtime_seconds"] <= elapsed
    assert output["bytes_written"] == size
    assert metrics["output_bytes_by_field"] == {"generated_file_path": size}
    assert (tmp_path / "outputs" / "generated_file.dat").stat().st_size == size


def test_consumer_stages_a_co_located_parent_file(tmp_path, monkeypatch):
    producer_dir = tmp_path / "producer"
    producer_dir.mkdir()
    (producer_dir / "generated_file.dat").write_bytes(b"x" * 4096)

    from tasks import emulation

    real_join = os.path.join

    def join(first, *rest):
        if first == "/data/outputs":
            return real_join(str(tmp_path), *[part.lstrip("/") for part in rest])
        return real_join(first, *rest)

    monkeypatch.setattr(emulation.os.path, "join", join)
    monkeypatch.setenv("generated_file_path", "generated_file.dat")
    monkeypatch.setenv("TS_PARENT_INGEST_OUTPUTS_DIR", "/producer")
    monkeypatch.setenv("TS_PARENT_INGEST_FILESERVER_URL", "http://unused:8080/x")

    staged = emulation.stage_parent_inputs(str(tmp_path / "inputs"))

    assert staged.mode == "local"
    assert staged.input_bytes == 4096
    assert staged.lines[0].startswith("[LOCAL] ")


def test_kind_templates_use_real_size_work_and_one_slot_each():
    templates = list(yaml.safe_load_all(
        (ROOT / "experiments" / "k8s" / "tasktemplates.yaml").read_text(
            encoding="utf-8")
    ))

    assert {item["metadata"]["name"] for item in templates} == {
        "experiment-io",
        "experiment-io-large",
        "experiment-mem",
        "experiment-cpu",
        "experiment-cpu-long",
    }
    for template in templates:
        spec = template["spec"]
        assert spec["cpuRequest"] == "0.5"
        assert spec["physicalResources"]["cpuLimit"] == "500m"
        assert "emulation" not in spec
        assert spec["env"]
    io = next(item for item in templates
              if item["metadata"]["name"] == "experiment-io")["spec"]
    assert io["expectedOutputBytes"] == int(io["env"]["TS_IO_MIB"]) * 1024 * 1024
