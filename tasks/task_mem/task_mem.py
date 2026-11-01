"""task_mem — memory-bound benchmark stage.

Stages the parent's file (Channel A ``generated_file_path``), then streams a
``TS_MEMORY_MIB`` buffer through full copies. Two work modes:

* ``TS_MEMORY_CPU_SECONDS`` (validation matrix): copy until the process has
  used ``TS_MEMORY_CPU_SECONDS * work_scale / speed`` CPU-seconds, where
  ``speed`` is the node's emulated memory speed;
* ``TS_MEMORY_PASSES``: a fixed number of passes scaled by
  ``work_scale / speed``.

An optional ``TS_MEMORY_HOLD_SECONDS`` keeps the footprint resident.
"""

import os
import time

from tasks.emulation import (
    TaskReport,
    emulated_count,
    run_for_cpu_seconds,
    stage_parent_inputs,
)


def main() -> None:
    report = TaskReport("memory")
    outputs_dir = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
    inputs_dir = os.environ.get("TS_INPUTS_DIR", "/data/inputs")
    os.makedirs(outputs_dir, exist_ok=True)
    print("Starting memory task. Received upstream file: "
          f"{os.environ.get('generated_file_path', 'unknown')}")
    staged = stage_parent_inputs(inputs_dir)

    logical_memory_mib = int(os.environ.get("TS_MEMORY_MIB", "64"))
    logical_hold_s = float(os.environ.get("TS_MEMORY_HOLD_SECONDS", "0"))
    if logical_memory_mib < 1 or logical_hold_s < 0:
        raise ValueError(
            "TS_MEMORY_MIB must be positive and TS_MEMORY_HOLD_SECONDS non-negative")
    physical_memory_mib = report.config.work_count(logical_memory_mib)
    physical_hold_s = report.config.duration_s(logical_hold_s)

    mib = 1024 * 1024
    buffers = {
        "source": bytearray(b"M" * (physical_memory_mib * mib)),
        "target": bytearray(physical_memory_mib * mib),
    }

    def copy_pass() -> None:
        buffers["target"][:] = buffers["source"]
        buffers["source"], buffers["target"] = buffers["target"], buffers["source"]

    if os.environ.get("TS_MEMORY_CPU_SECONDS"):
        logical_cpu_s = float(os.environ["TS_MEMORY_CPU_SECONDS"])
        if logical_cpu_s <= 0:
            raise ValueError("TS_MEMORY_CPU_SECONDS must be positive")
        target_cpu_s = logical_cpu_s * report.config.work_scale / report.speed_factor
        passes = run_for_cpu_seconds(target_cpu_s, copy_pass)
        report.emulation.update({
            "logical_cpu_seconds": logical_cpu_s,
            "target_cpu_seconds": target_cpu_s,
        })
    else:
        logical_passes = int(os.environ.get("TS_MEMORY_PASSES", "8"))
        if logical_passes < 1:
            raise ValueError("TS_MEMORY_PASSES must be positive")
        passes = emulated_count(logical_passes, report.config, report.speed_factor)
        for _ in range(passes):
            copy_pass()
        report.emulation["logical_passes"] = logical_passes
    time.sleep(physical_hold_s)
    print(f"Copied {physical_memory_mib} MiB {passes} times "
          f"on {report.speed.node or '?'} (speed={report.speed_factor:g})")
    buffers.clear()

    report.emulation.update({
        "logical_memory_mib": logical_memory_mib,
        "physical_memory_mib": physical_memory_mib,
        "physical_passes": passes,
        "logical_hold_seconds": logical_hold_s,
        "physical_hold_seconds": physical_hold_s,
    })
    report.emit(
        {"processed_array_size": logical_memory_mib},
        io_bytes_read=staged.input_bytes,
        transfer_seconds=staged.transfer_seconds,
    )


if __name__ == "__main__":
    main()
