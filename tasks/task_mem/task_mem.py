import os
import json
import time

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


# Read metadata from the IO task via the shared volume
prev_file_path = read_input("generated_file_path", "unknown")
print(f"Starting Memory task. Received upstream path: {prev_file_path}")
start_time = time.time()

# Allocate ~400 MB in RAM using 1MB string chunks.
# Sized to fit under the 768 Mi pod limit while still being heavy enough
# that a 768 MB-cap node (small profile's IO_OPT) cannot host it — forcing
# the scheduler to learn that MEM_OPT / CPU_OPT are the only viable choices.
MB = 1024 * 1024
chunk_size_mb = 400
memory_hog = [b"M" * MB for _ in range(chunk_size_mb)]

array_size = len(memory_hog)  # = 400
print(f"Held {array_size} MB in RAM.")

# Keep it allocated to ensure the OS actually pages it in
time.sleep(2)
del memory_hog

print(f"Memory Task finished in {time.time() - start_time:.2f} seconds.")

# Write outputs to the shared volume so downstream tasks can read them directly
output = {"processed_array_size": array_size}
save_output(output)
print(f"__TS_OUTPUT__={json.dumps(output)}")