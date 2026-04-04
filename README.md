Gemini said
Here is the definitive, logic-focused README for your custom scheduler. This strips away the Docker commands and Kubernetes setup, focusing entirely on the mathematical and architectural logic that makes your thesis novel.

You can drop this directly into your GitHub repository or use it as the core technical outline for your dissertation's methodology chapter.

Custom DAG-Aware Kubernetes Workflow Scheduler
The Core Philosophy
The native Kubernetes kube-scheduler is a myopic load balancer. It evaluates workloads one Pod at a time, strictly based on current node capacity, completely blind to workflow dependencies, historical execution times, and future resource bottlenecks.

This project completely bypasses the native scheduler, introducing a custom Predictive Control Plane written in Python. It manages complex Directed Acyclic Graphs (DAGs), enforces strict heterogeneous hardware constraints, and uses machine learning (EWMA) to predict workload behavior, drastically reducing scheduling latency for real-time applications.

Core Architectural Logic
1. Priority Ingestion & Queueing (Batch vs. Real-Time)
The system does not treat all workflows equally. It implements a multi-tiered priority queue (using a Python Min-Heap) to guarantee SLA (Service Level Agreement) compliance.

The Sorting Logic: Workflows are ingested and sorted by a computed weight. A REAL_TIME workflow has a strictly lower heap-weight (higher priority) than a BATCH workflow.

Queue Jumping: If a heavy BATCH workflow is being processed, and a CRITICAL REAL_TIME workflow arrives, the engine mathematically guarantees the real-time tasks will jump to the absolute front of the Task Queue, ensuring zero queue-wait latency for urgent requests.

2. DAG Resolution & Temporal Logic
Kubernetes natively lacks the ability to say "Only start Pod B when Pod A finishes." This engine implements a ReadinessResolver to handle temporal workflow states.

Dependency Unlocking: The engine maps the DependencyEdges of a workflow. A task remains in a BLOCKED state until the ExecutionObserver verifies that all parent tasks have exited with a success code (0).

Just-In-Time Scheduling: Only tasks flagged as READY are injected into the task queue, preventing the scheduler from wasting math cycles on tasks that cannot physically run yet.

3. The "Two-Layer" Resource Defense
To mathematically simulate a multi-million dollar AWS heterogeneous cluster (e.g., dedicated I/O, RAM, and CPU instances) on constrained local hardware, the algorithm uses a dual-layer enforcement strategy:

Layer 1: The Logical Ledger (The Bouncer): The Python Placement Algorithm maintains a strict internal ledger of available capacity per node (CPU_OPT, MEM_OPT, IO_OPT). It refuses to assign a task to a node if the ledger indicates the node is logically "full," perfectly simulating the boundaries of isolated Virtual Machines.

Layer 2: Physical Cgroups (The Police): To prevent running Pods from cheating the ledger, the algorithm translates its math into strict Kubernetes Requests and Limits. The Linux kernel (via cgroups) actively monitors the physical containers; if a memory-bound task exceeds its promised allocation by even a single byte, it is instantly terminated (OOMKilled), protecting the host hardware.

4. The Learning Engine: Warm Instances & EWMA
This is the "Brain" of the thesis. The scheduler does not just guess where to put tasks; it learns from reality.

The Execution Observer: When a Pod finishes, the orchestrator calculates the exact physical startup time (image pull + container boot) and runtime.

EWMA (Exponential Weighted Moving Average): The algorithm applies an EWMA formula to smooth out network anomalies and generate an accurate historical profile for that specific task type.

Predictive Caching ("Warm" Instances): If the scheduler recognizes a task template, it takes the Fast Path. Instead of running the heavy node-filtering and scoring algorithms, it pulls the EWMA profile from the cache and instantly maps the task to the optimal hardware. This mimics "Warm Instance" routing, drastically reducing the control-plane overhead for repeated workflows.

5. State Decoupling & Data Handoff
To prevent reliance on slow, shared network drives (NFS) between sequential tasks, the logic utilizes Log-Based Metadata Extraction.

When Task A finishes, it prints its critical output (e.g., processed_array_size: 200) to stdout with a specific JSON tag.

The orchestrator intercepts the Pod's log, parses the JSON, and dynamically injects that data as an Environment Variable into Task B's Kubernetes manifest right before scheduling it.

This mathematically completely decouples the Pods while maintaining strict data continuity.

Why this matters (The Thesis Conclusion)
By combining priority queueing, EWMA-based execution predictions, and strict physical constraint enforcement, this scheduler proves that complex, multi-stage workflows can be orchestrated faster and more efficiently than standard K8s load balancing, optimizing expensive specialized hardware for high-priority real-time workloads.