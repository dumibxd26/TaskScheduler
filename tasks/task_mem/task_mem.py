"""task_mem — memory-bound stage 2 of the demo pipeline.

Reads parent output metadata via env vars (Channel A — injected by the
controller from the IO task's ``__TS_OUTPUT__=`` log line). If it needs the
parent's bulk output file it can fetch it through the parent's node-local
fileserver using ``$TS_PARENT_<NAME>_FILESERVER_URL``.
"""

import json
import os
import shutil
import time
import urllib.request

OUTPUTS_DIR = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
INPUTS_DIR = os.environ.get("TS_INPUTS_DIR", "/data/inputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)
os.makedirs(INPUTS_DIR, exist_ok=True)

# Channel A: small metadata from parent.
prev_file_path = os.environ.get("generated_file_path", "unknown")
print(f"Starting Memory task. Received upstream file: {prev_file_path}")

parent_urls = sorted(
    (key[:-len("_FILESERVER_URL")], value)
    for key, value in os.environ.items()
    if key.startswith("TS_PARENT_") and key.endswith("_FILESERVER_URL")
)
transfer_seconds = 0.0
input_bytes = 0
if parent_urls and prev_file_path != "unknown":
    parent_prefix, parent_url = parent_urls[0]
    parent_dir = os.environ.get(f"{parent_prefix}_OUTPUTS_DIR", "")
    local_source = os.path.join(
        "/data/outputs", parent_dir.lstrip("/"), prev_file_path)
    destination = os.path.join(INPUTS_DIR, prev_file_path)
    if os.path.isfile(local_source):
        print(f"[LOCAL] {local_source} -> {destination}")
        shutil.copyfile(local_source, destination)
    else:
        url = f"{parent_url}/{prev_file_path}"
        print(f"[FETCH] {url} -> {destination}")
        transfer_started = time.time()
        urllib.request.urlretrieve(url, destination)
        transfer_seconds = time.time() - transfer_started
    input_bytes = os.path.getsize(destination)

start_time = time.time()

# Allocate ~400 MB in RAM (1 MB chunks).
MB = 1024 * 1024
chunk_size_mb = 400
memory_hog = [b"M" * MB for _ in range(chunk_size_mb)]

array_size = len(memory_hog)
print(f"Held {array_size} MB in RAM.")
time.sleep(2)
del memory_hog

print(f"Memory Task finished in {time.time() - start_time:.2f} seconds.")

output = {"processed_array_size": array_size}
print(f"__TS_OUTPUT__={json.dumps(output)}")
metrics = {
    "io_bytes_read": input_bytes,
    "io_bytes_written": 0,
    "output_bytes_by_field": {},
    "transfer_seconds": transfer_seconds,
}
print(f"__TS_METRICS__={json.dumps(metrics)}")