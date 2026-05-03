import os
import json
import time
import hashlib

# ── Shared volume helpers ──
SHARED_DIR = os.environ.get("TS_SHARED_DIR", "/data/shared")
WF_ID = os.environ.get("TS_WORKFLOW_ID", "unknown")
WF_DIR = os.path.join(SHARED_DIR, WF_ID)


def read_input(field: str, default=None):
    """Read a single field from the shared volume, fall back to env var."""
    path = os.path.join(WF_DIR, f"{field}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return os.environ.get(field, default)


def save_output(data: dict):
    """Write each output field to the shared volume as <field>.json."""
    os.makedirs(WF_DIR, exist_ok=True)
    for field, value in data.items():
        path = os.path.join(WF_DIR, f"{field}.json")
        with open(path, "w") as f:
            json.dump(value, f)
        print(f"[SHARED] Saved '{field}' -> {path}")


# Read metadata from the Memory task via the shared volume
array_size = int(read_input("processed_array_size", "50"))
print(f"Starting CPU task. array_size={array_size}, computing SHA-256 hashes...")
start_time = time.time()

# Hold ~80 MB during computation to keep the task under its 256 Mi pod limit.
MB = 1024 * 1024
cpu_buffer = [b"C" * MB for _ in range(80)]
print(f"Allocated {len(cpu_buffer)} MB buffer")

# Simulate CPU Load: tight SHA-256 hashing loop
# array_size=600 -> 600*10000 = 6_000_000 iterations
target_loops = array_size * 10_000
final_hash = ""

for i in range(target_loops):
    h = hashlib.sha256(str(i).encode())
    if i == target_loops - 1:
        final_hash = h.hexdigest()

del cpu_buffer

print(f"CPU Task finished in {time.time() - start_time:.2f} seconds.")
print(f"Final hash: {final_hash}")

# Write outputs to the shared volume
output = {"status": "workflow_complete", "final_hash": final_hash}
save_output(output)
print(f"__TS_OUTPUT__={json.dumps(output)}")