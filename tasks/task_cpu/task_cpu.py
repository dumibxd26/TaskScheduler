"""task_cpu — CPU-bound benchmark stage (single-threaded SHA-256 work).

Two work modes:

* ``TS_CPU_SECONDS`` (validation matrix): hash until the process has used
  ``TS_CPU_SECONDS * work_scale / speed`` CPU-seconds, where ``speed`` is the
  emulated CPU speed of the node. Durations then depend on the emulated speed
  and the Pod's CPU quota, not on host hyperthreading or Python build speed.
* ``TS_CPU_ITERATIONS`` (or the older ``processed_array_size * 10_000``):
  hash a fixed number of times, scaled by ``work_scale / speed``.

Parent files named by Channel A are staged first, so data-dependent fan-outs
exercise the real network path.
"""

import hashlib
import os

from tasks.emulation import (
    TaskReport,
    emulated_count,
    run_for_cpu_seconds,
    stage_parent_inputs,
)

CHUNK_HASHES = 2_000


def main() -> None:
    report = TaskReport("cpu")
    outputs_dir = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
    inputs_dir = os.environ.get("TS_INPUTS_DIR", "/data/inputs")
    os.makedirs(outputs_dir, exist_ok=True)
    staged = stage_parent_inputs(inputs_dir)
    logical_buffer_mib = int(os.environ.get("TS_CPU_BUFFER_MIB", "16"))
    physical_buffer_mib = report.config.work_count(logical_buffer_mib)
    mib = 1024 * 1024
    cpu_buffer = [b"C" * mib for _ in range(physical_buffer_mib)]
    state = {"index": 0, "digest": ""}

    def chunk() -> None:
        start = state["index"]
        for index in range(start, start + CHUNK_HASHES):
            state["digest"] = hashlib.sha256(str(index).encode()).hexdigest()
        state["index"] = start + CHUNK_HASHES

    if os.environ.get("TS_CPU_SECONDS"):
        logical_cpu_s = float(os.environ["TS_CPU_SECONDS"])
        if logical_cpu_s <= 0:
            raise ValueError("TS_CPU_SECONDS must be positive")
        target_cpu_s = logical_cpu_s * report.config.work_scale / report.speed_factor
        print(f"Starting CPU task on {report.speed.node or '?'} "
              f"(speed={report.speed_factor:g}): target_cpu_seconds={target_cpu_s:.3f}")
        run_for_cpu_seconds(target_cpu_s, chunk)
        report.emulation.update({
            "logical_cpu_seconds": logical_cpu_s,
            "target_cpu_seconds": target_cpu_s,
            "physical_cpu_iterations": state["index"],
        })
    else:
        if os.environ.get("TS_CPU_ITERATIONS"):
            logical_iterations = int(os.environ["TS_CPU_ITERATIONS"])
        else:
            array_size = int(os.environ.get("processed_array_size", "50"))
            if array_size < 1:
                raise ValueError("processed_array_size must be at least 1")
            logical_iterations = array_size * 10_000
        if logical_iterations < 1:
            raise ValueError("TS_CPU_ITERATIONS must be at least 1")
        physical_iterations = emulated_count(
            logical_iterations, report.config, report.speed_factor)
        print(f"Starting CPU task on {report.speed.node or '?'} "
              f"(speed={report.speed_factor:g}): logical_iterations="
              f"{logical_iterations}, physical_iterations={physical_iterations}")
        for index in range(physical_iterations):
            state["digest"] = hashlib.sha256(str(index).encode()).hexdigest()
        state["index"] = physical_iterations
        report.emulation.update({
            "logical_cpu_iterations": logical_iterations,
            "physical_cpu_iterations": physical_iterations,
        })
    del cpu_buffer

    report.emulation.update({
        "logical_memory_mib": logical_buffer_mib,
        "physical_memory_mib": physical_buffer_mib,
    })
    report.emit(
        {"status": "workflow_complete", "final_hash": state["digest"]},
        io_bytes_read=staged.input_bytes,
        transfer_seconds=staged.transfer_seconds,
    )


if __name__ == "__main__":
    main()
