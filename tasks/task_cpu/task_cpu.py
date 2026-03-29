import os
import json
import time
import hashlib

# 1. Read input from Task 2
array_size_str = os.environ.get("processed_array_size")
if not array_size_str:
    raise ValueError("Missing 'processed_array_size' environment variable!")

array_size = int(array_size_str)
print(f"Starting CPU task. Will compute hashes based on size: {array_size}")
start_time = time.time()

# 2. Simulate CPU Load: Calculate SHA-256 hashes in a tight loop
# We loop a few million times. Adjust the multiplier to make it run for ~3-5 seconds
target_loops = array_size * 1000000  
final_hash = ""

for i in range(target_loops):
    # Hashing is pure CPU work
    hash_obj = hashlib.sha256(str(i).encode('utf-8'))
    if i == target_loops - 1:
        final_hash = hash_obj.hexdigest()

print(f"CPU Task finished in {time.time() - start_time:.2f} seconds. Final hash: {final_hash}")