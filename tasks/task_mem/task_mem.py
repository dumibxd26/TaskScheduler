import os
import json
import time

# Receive metadata from the IO task (passed as env var by the orchestrator)
prev_file_path = os.environ.get("generated_file_path", "unknown")
print(f"Starting Memory task. Received upstream path: {prev_file_path}")
start_time = time.time()

# Allocate ~200MB in RAM using 1MB string chunks
# This is the actual memory stress — 200 objects of 1MB each
MB = 1024 * 1024
chunk_size_mb = 200
memory_hog = [b"M" * MB for _ in range(chunk_size_mb)]

array_size = len(memory_hog)  # = 200
print(f"Held {array_size} MB in RAM.")

# Keep it allocated for a moment to ensure the OS actually pages it in
time.sleep(1)
del memory_hog

print(f"Memory Task finished in {time.time() - start_time:.2f} seconds.")

# Signal output to the orchestrator via stdout
output = {"processed_array_size": array_size}
print(f"__TS_OUTPUT__={json.dumps(output)}")