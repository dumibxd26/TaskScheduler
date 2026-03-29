import os
import json
import time

# 1. Read input injected by the Scheduler via Environment Variables!
file_path = os.environ.get("generated_file_path")
if not file_path:
    raise ValueError("Missing 'generated_file_path' environment variable!")

print(f"Starting Memory task using file: {file_path}")
start_time = time.time()

# 2. Simulate Memory Load: Load the 50MB file and duplicate it 5 times in RAM
memory_hog = []
with open(file_path, "r") as f:
    raw_data = f.read()

for i in range(5):
    # Appending strings creates new objects in memory (using ~250MB)
    memory_hog.append(raw_data + str(i))

array_size = len(memory_hog)
print(f"Memory Task finished in {time.time() - start_time:.2f} seconds.")

# 3. Output metadata for Task 3
with open("/scheduler_outputs/processed_array_size.json", "w") as out:
    json.dump(array_size, out)