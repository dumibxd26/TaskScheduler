"""task_io — I/O-bound benchmark stage.

Writes a ``TS_IO_MIB`` file into the producer-local data plane
(``$TS_OUTPUTS_DIR/generated_file.dat``), paced to an emulated device rate of
``TS_IO_RATE_MIB_S`` times the node's I/O speed, then reads it back. The file
name is published through Channel A for DATA children, which read it locally
or fetch it from this node's fileserver.
"""

import os
import time

from tasks.emulation import TaskReport, stage_parent_inputs

CHUNK = 1024 * 1024


def _paced_write(path: str, total_bytes: int, rate_bytes_s: float) -> None:
    started = time.perf_counter()
    written = 0
    block = b"A" * CHUNK
    with open(path, "wb") as stream:
        while written < total_bytes:
            size = min(CHUNK, total_bytes - written)
            stream.write(block[:size])
            written += size
            # Token-bucket pacing: never run ahead of the emulated device.
            ahead = written / rate_bytes_s - (time.perf_counter() - started)
            if ahead > 0:
                time.sleep(ahead)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    report = TaskReport("io")
    outputs_dir = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
    inputs_dir = os.environ.get("TS_INPUTS_DIR", "/data/inputs")
    os.makedirs(outputs_dir, exist_ok=True)
    staged = stage_parent_inputs(inputs_dir)

    logical_output_mib = int(os.environ.get("TS_IO_MIB", "100"))
    base_rate_mib_s = float(os.environ.get("TS_IO_RATE_MIB_S", "0"))
    if logical_output_mib < 1 or base_rate_mib_s < 0:
        raise ValueError("TS_IO_MIB must be positive and TS_IO_RATE_MIB_S >= 0")
    logical_bytes = logical_output_mib * CHUNK
    physical_bytes = report.config.work_count(logical_bytes)
    rate_bytes_s = (
        base_rate_mib_s * CHUNK * report.speed_factor
        if base_rate_mib_s > 0 else float("inf"))
    print(f"Starting I/O task on {report.speed.node or '?'} "
          f"(speed={report.speed_factor:g}): physical_bytes={physical_bytes}")

    output_file = os.path.join(outputs_dir, "generated_file.dat")
    _paced_write(output_file, physical_bytes, rate_bytes_s)
    with open(output_file, "rb") as stream:
        while stream.read(CHUNK):
            pass

    report.emulation.update({
        "logical_output_bytes": logical_bytes,
        "physical_output_bytes": physical_bytes,
        "device_rate_mib_s": (
            base_rate_mib_s * report.speed_factor if base_rate_mib_s > 0 else None),
    })
    report.emit(
        {
            "generated_file_path": "generated_file.dat",
            "bytes_written": physical_bytes,
            "logical_bytes_written": logical_bytes,
        },
        io_bytes_read=physical_bytes + staged.input_bytes,
        io_bytes_written=physical_bytes,
        output_bytes_by_field={"generated_file_path": physical_bytes},
        transfer_seconds=staged.transfer_seconds,
    )


if __name__ == "__main__":
    main()
