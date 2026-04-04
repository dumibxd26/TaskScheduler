import os
import json
import time

print("Starting I/O task...")
start_time = time.time()

# Write a 50MB file to local /tmp (container-local, stresses disk IO)
file_path = "/tmp/io_stress.dat"
chunk = b"A" * 1024  # 1KB chunk

with open(file_path, "wb") as f:
    for _ in range(50 * 1024):  # 50MB total
        f.write(chunk)

# Read it back to stress read IO as well
with open(file_path, "rb") as f:
    _ = f.read()

os.remove(file_path)

print(f"I/O Task finished in {time.time() - start_time:.2f} seconds.")

# Signal output to the orchestrator via stdout (log-based extraction)
output = {"generated_file_path": "/tmp/io_stress.dat", "bytes_written": 50 * 1024 * 1024}
print(f"__TS_OUTPUT__={json.dumps(output)}")