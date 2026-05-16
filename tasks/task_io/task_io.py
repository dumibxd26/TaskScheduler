"""task_io — IO-bound stage 1 of the demo pipeline.

Writes its bulk output (a 100 MB file) into the producer-local data plane
at ``$TS_OUTPUTS_DIR/<field>.dat``. Emits path metadata via Channel A
(``__TS_OUTPUT__=...``) for downstream stages.
"""

import json
import os
import time

# ── Producer-local data plane ──
# Outputs go to the per-pod subdirectory the controller created under the
# node's hostPath at /var/lib/ts-data/<wf>/<pod>. The fileserver DaemonSet
# on this node serves it via HTTP for cross-node child fetches.
OUTPUTS_DIR = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

print(f"Starting I/O task. OUTPUTS_DIR={OUTPUTS_DIR}")
start_time = time.time()

# Hold ~150 MB in RAM while doing IO (stresses both memory and disk).
MB = 1024 * 1024
io_buffer = [b"A" * MB for _ in range(150)]
print(f"Allocated {len(io_buffer)} MB buffer for IO")

# Write a 100 MB output file. This is the artifact a downstream task would
# fetch via the fileserver if it ends up running on a different node.
output_file = os.path.join(OUTPUTS_DIR, "generated_file.dat")
chunk = b"A" * (1024 * 64)
with open(output_file, "wb") as f:
    for _ in range(100 * 16):  # 100 MB total
        f.write(chunk)
with open(output_file, "rb") as f:
    _ = f.read()

del io_buffer

print(f"I/O Task finished in {time.time() - start_time:.2f} seconds.")

# Channel A: small metadata blob. The controller scrapes this line from the
# pod's log and injects each field as an env var on the downstream pod.
output = {
    "generated_file_path": "generated_file.dat",   # filename under OUTPUTS_DIR
    "bytes_written": 100 * 1024 * 1024,
}
print(f"__TS_OUTPUT__={json.dumps(output)}")