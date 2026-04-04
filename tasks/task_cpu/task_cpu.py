import os
import json
import time
import hashlib

# Receive metadata from the Memory task (passed as env var by the orchestrator)
array_size_str = os.environ.get("processed_array_size", "50")
array_size = int(array_size_str)
print(f"Starting CPU task. array_size={array_size}, computing SHA-256 hashes...")
start_time = time.time()

# Simulate CPU Load: tight SHA-256 hashing loop
# array_size=200 -> 200*10000 = 2_000_000 iterations (~2-4s on M2)
target_loops = array_size * 10_000
final_hash = ""

for i in range(target_loops):
    h = hashlib.sha256(str(i).encode())
    if i == target_loops - 1:
        final_hash = h.hexdigest()

print(f"CPU Task finished in {time.time() - start_time:.2f} seconds.")
print(f"Final hash: {final_hash}")

# Signal output to the orchestrator via stdout
output = {"status": "workflow_complete", "final_hash": final_hash}
print(f"__TS_OUTPUT__={json.dumps(output)}")