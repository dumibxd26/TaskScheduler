import os
import json
import time

# ── Shared volume helpers ──
SHARED_DIR = os.environ.get("TS_SHARED_DIR", "/data/shared")
WF_ID = os.environ.get("TS_WORKFLOW_ID", "unknown")
WF_DIR = os.path.join(SHARED_DIR, WF_ID)


def save_output(data: dict):
    """Write each output field to the shared volume as <field>.json."""
    os.makedirs(WF_DIR, exist_ok=True)
    for field, value in data.items():
        path = os.path.join(WF_DIR, f"{field}.json")
        with open(path, "w") as f:
            json.dump(value, f)
        print(f"[SHARED] Saved '{field}' -> {path}")


print("Starting I/O task...")
start_time = time.time()

# Hold ~150 MB in RAM while doing IO (stresses both memory and disk).
# Sized to fit comfortably under a 512 Mi pod limit (Python overhead + buffer
# + 100 MB tmpfs file ≈ 320 MB peak).
MB = 1024 * 1024
io_buffer = [b"A" * MB for _ in range(150)]
print(f"Allocated {len(io_buffer)} MB buffer for IO")

# Write a 100MB file to local /tmp (container-local, stresses disk IO)
file_path = "/tmp/io_stress.dat"
chunk = b"A" * (1024 * 64)  # 64KB chunks for faster writes

with open(file_path, "wb") as f:
    for _ in range(100 * 16):  # 100MB total
        f.write(chunk)

# Read it back to stress read IO as well
with open(file_path, "rb") as f:
    _ = f.read()

os.remove(file_path)
del io_buffer

print(f"I/O Task finished in {time.time() - start_time:.2f} seconds.")

# Write outputs to the shared volume so downstream tasks can read them directly
output = {"generated_file_path": "/tmp/io_stress.dat", "bytes_written": 100 * 1024 * 1024}
save_output(output)
print(f"__TS_OUTPUT__={json.dumps(output)}")