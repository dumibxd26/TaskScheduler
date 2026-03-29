import os
import json
import time

# 1. Simulate IO: Write a 50MB file to the shared volume
shared_dir = "/shared_volume/wf-current"
os.makedirs(shared_dir, exist_ok=True)
file_path = os.path.join(shared_dir, "dummy_data.txt")

print("Starting I/O task...")
start_time = time.time()

# Write 50MB of data (50,000 chunks of 1KB)
with open(file_path, "w") as f:
    for _ in range(50000):
        f.write("A" * 1024)

print(f"I/O Task finished in {time.time() - start_time:.2f} seconds.")

# 2. Output the metadata for the Scheduler to pick up
output_data = {
    "generated_file_path": file_path
}
# The scheduler will intercept this file
with open("/scheduler_outputs/generated_file_path.json", "w") as out:
    json.dump(output_data["generated_file_path"], out)