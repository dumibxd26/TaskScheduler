

- Realised it is stupid to choose best node for a task, it is obvious that one task will run faster on a computer that is better anytime.

it is hard to put each task on a different machine because it can really run differently depending on the heat of the system(throttling, etc). We can somehow use that cache in case we find great differences and we want to put it on another machine, but for example give a bigger advantage on specific machines. we can use an algorithm which adds advantage if the image is cached, how much it takes to load that image etc.

- Situation: the task x can run faster on machine Y if it wait until in has resources on that machine, rather than start working on another machine(bcs runtime on that machine is higher). So in this case even if the other machine becomes available this task still waits, but the problem now is if a task with a higher priority arrives, because it theoretically preempts this one.

- changed actual docker container specs to mimic vms 

- think about adding checkpointing for preemption.

- Smarter scheduling proposal. Maybe use a cheap AI model to detect if for example we have a matrix multiplication/SHA calculation/smth and put these on specific nodes.

- add predictive pre-warmingup

- 2. Cluster Health & Defragmentation
As tasks arrive and finish at different times, your cluster will suffer from "Swiss Cheese" fragmentation. You might have 10 nodes, each with 1 free CPU, meaning you have 10 CPUs total—but you cannot schedule a single 4-CPU task because the space is fragmented.

Continuous Active Repacking (The Descheduler): Instead of only making decisions when a task arrives, add a background loop that constantly evaluates the cluster state. If it detects fragmentation, it uses the Checkpoint/Restore feature we discussed earlier to pause small, low-priority tasks, migrate them to pack them tightly onto a few nodes, and free up entire large nodes for massive tasks.

- Gang Scheduling (All-or-Nothing): Distributed Machine Learning training (like PyTorch/TensorFlow) requires all workers to talk to each other simultaneously. If a workflow needs 10 GPUs, scheduling 9 of them is useless—the 9 will just sit there indefinitely waiting for the 10th, wasting money. Gang scheduling introduces a rule: the scheduler must find resources for all 10 tasks at the exact same engine tick. If it can't, it provisions 0 and waits until space clears.

- Task/Workflow Embeddings: Instead of starting from scratch, generate a vector embedding for every new task based on its metadata (e.g., Docker image size, library dependencies, DAG position, environment variables).

How it works: When task-etl-v2 is submitted for the first time, the scheduler does a cosine similarity search against the ProfileStore. It finds that task-etl-v2 is 98% similar to task-etl-v1. Instead of random exploration, the scheduler instantly inherits the learned weights of v1. You achieve near-perfect placement on the very first execution.

- Deep Hardware Interference Modeling
Your 8-factor score is currently blind to why a task performs poorly on a specific node. It just sees that the runtime was slower.

eBPF Telemetry Integration: Deploy a lightweight eBPF (Extended Berkeley Packet Filter) agent on your nodes. Instead of just returning actual_runtime, the node reports micro-architectural metrics back to the ProfileStore: L3 Cache Misses, Context Switches, Disk Queue Length, and Network Packet Drops.

The Value: The scheduler begins to learn the interference profile of tasks. It learns that task-data-process causes massive L3 cache thrashing. It then creates an automatic anti-affinity rule: "Never schedule two cache-thrashing tasks on the same CPU socket." This eliminates the "noisy neighbor" problem entirely.

- Memory Deduplication (Transparent Page Sharing)
This is a black-magic technique used in hypervisors like VMware, but rarely in generic task schedulers.

The Concept: If you schedule 5 identical ML tasks on the same node, they all load the exact same 10GB PyTorch library and 50GB foundation model into RAM. Normally, this costs 300GB of RAM. The scheduler can instruct the Linux kernel to use KSM (Kernel Samepage Merging).

The Value: The OS scans the RAM, realizes the 5 tasks are holding identical data, and merges them into a single read-only pointer in physical memory. Those 5 tasks now only consume 60GB of RAM total, allowing you to schedule 5x more tasks on the exact same hardware.

- Fractal / Elastic Workloads (Task-Scheduler Negotiation)
Right now, your tasks are rigid. A task says, "I need 4 CPUs." The scheduler either provides them or makes the task wait.

The Concept: Change the API so tasks become "Fractal." A task declares a curve of execution: "I can run on 1 CPU in 60 minutes, 10 CPUs in 6 minutes, or 100 CPUs in 30 seconds."

The Value: The scheduler no longer just plays Tetris; it acts as a central bank. If the cluster is empty, it assigns the task 100 CPUs so it finishes instantly. If the cluster is under massive load, the scheduler dynamically squashes the task down to 1 CPU, allowing it to slowly make progress in the background rather than sitting frozen in a queue.

- LLM-Native Intent Scheduling (No More DAGs)
Currently, human engineers have to manually design the DAG (Task A -> Task B -> Task C) and define the dependencies, priority, and resource requests.

The Concept: Replace the manual YAML/JSON workflow definitions with an LLM agent that sits in front of your ProfileStore. A developer types: "Extract the daily logs from the payment gateway, mask the PII, and generate a summary report before I wake up tomorrow."

The Value: The LLM breaks that natural language prompt down into discrete tasks, checks the ProfileStore to see how long those tasks usually take, constructs the DAG dynamically, assigns the exact Priority and Deadline needed to finish before 8:00 AM, and pushes it to the engine. The scheduler becomes an intelligent operating system for natural language.  ---- Basically having access to multiple tasks, the user can design workflows using NLP