"""task_cpu — CPU-bound stage 3 of the demo pipeline."""

import hashlib
import json
import os
import time

OUTPUTS_DIR = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Channel A: small metadata from parent.
array_size = int(os.environ.get("processed_array_size", "50"))
print(f"Starting CPU task. array_size={array_size}, computing SHA-256 hashes...")
start_time = time.time()

# Hold ~80 MB during computation.
MB = 1024 * 1024
cpu_buffer = [b"C" * MB for _ in range(80)]
print(f"Allocated {len(cpu_buffer)} MB buffer")

target_loops = array_size * 10_000
final_hash = ""
for i in range(target_loops):
    h = hashlib.sha256(str(i).encode())
    if i == target_loops - 1:
        final_hash = h.hexdigest()

del cpu_buffer

print(f"CPU Task finished in {time.time() - start_time:.2f} seconds.")
print(f"Final hash: {final_hash}")

output = {"status": "workflow_complete", "final_hash": final_hash}
print(f"__TS_OUTPUT__={json.dumps(output)}")