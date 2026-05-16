"""task_mem — memory-bound stage 2 of the demo pipeline.

Reads parent output metadata via env vars (Channel A — injected by the
controller from the IO task's ``__TS_OUTPUT__=`` log line). If it needs the
parent's bulk output file it can fetch it through the parent's node-local
fileserver using ``$TS_PARENT_<NAME>_FILESERVER_URL``.
"""

import json
import os
import time
import urllib.request

OUTPUTS_DIR = os.environ.get("TS_OUTPUTS_DIR", "/data/outputs")
INPUTS_DIR = os.environ.get("TS_INPUTS_DIR", "/data/inputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)
os.makedirs(INPUTS_DIR, exist_ok=True)

# Channel A: small metadata from parent.
prev_file_path = os.environ.get("generated_file_path", "unknown")
print(f"Starting Memory task. Received upstream file: {prev_file_path}")

# Channel B (optional): if a 'parent fileserver URL' is provided, fetch the
# bulk file into INPUTS_DIR. Same-node parents would normally be served via
# a sub-path mount; this branch is the cross-node fallback.
parent_url = os.environ.get("TS_PARENT_TASK_IO_FILESERVER_URL")
if parent_url and prev_file_path != "unknown":
    try:
        url = f"{parent_url}/{prev_file_path}"
        dst = os.path.join(INPUTS_DIR, prev_file_path)
        print(f"[FETCH] {url} -> {dst}")
        urllib.request.urlretrieve(url, dst)
    except Exception as e:
        print(f"[FETCH] skipped (parent same-node?): {e}")

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