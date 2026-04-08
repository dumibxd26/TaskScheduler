<div align="center">

# Task Scheduler

### Adaptive DAG-Aware Workflow Scheduler for Heterogeneous Kubernetes Clusters

*Master's Thesis — University Politehnica of Bucharest*
*"Task Scheduling in Distributed Systems"*

---

**Learning-based** · **DAG-aware** · **Priority-driven** · **Heterogeneous hardware**

</div>

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Scheduling Algorithm](#scheduling-algorithm)
- [Exploration & Learning](#exploration--learning)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quick Start — Local Simulation](#quick-start--local-simulation)
- [Kubernetes Deployment](#kubernetes-deployment)
  - [1. Create the Kind Cluster](#1-create-the-kind-cluster)
  - [2. Enforce Physical Resource Limits](#2-enforce-physical-resource-limits)
  - [3. Install Metrics Server](#3-install-metrics-server)
  - [4. Build & Load Container Images](#4-build--load-container-images)
  - [5. Deploy the Scheduler](#5-deploy-the-scheduler)
  - [6. Verify Labels & Nodes](#6-verify-labels--nodes)
  - [7. Submit a Workflow](#7-submit-a-workflow)
  - [8. Run from Local Machine (Development)](#8-run-from-local-machine-development)
- [Monitoring & Debugging](#monitoring--debugging)
- [Testing](#testing)
- [Cleanup](#cleanup)

---

## Overview

The native Kubernetes `kube-scheduler` is a **stateless, per-Pod load balancer**. It evaluates workloads in isolation — blind to workflow dependencies, historical performance, and the topology of heterogeneous hardware.

This project replaces it with a **custom Predictive Control Plane** that:

- Orchestrates **DAG-based workflows** (multi-step pipelines with dependencies)
- Places tasks on nodes **optimized for their workload class** (CPU, memory, IO)
- **Learns from every execution** — building per-task, per-node runtime profiles
- **Explores intelligently** — guaranteeing full coverage of all nodes before exploiting learned preferences
- Enforces **priority queuing** with aging, preemption support, and CRITICAL overrides
- Tracks **real cluster resources** via the Kubernetes Metrics API

### The Pipeline

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  task-io  │────▶│ task-mem  │────▶│ task-cpu  │
│ (IO_BOUND)│     │(MEM_BOUND)│     │(CPU_BOUND)│
└──────────┘     └──────────┘     └──────────┘
   writes          allocates         computes
   50 MB file      200 MB array      2M SHA-256
```

Data flows between tasks via **log-based metadata extraction** (`__TS_OUTPUT__=<json>`) — no shared filesystems required.

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │               Entry Points                  │
                 │                                             │
                 │  run_simulation.py    (local, no K8s)       │
                 │  k8s_main.py         (direct pod creation)  │
                 │  k8s_scheduler.py    (K8s Deployment)       │
                 │  server.py           (Flask REST API)       │
                 └─────────────┬───────────────────────────────┘
                               │
                               ▼
                 ┌─────────────────────────────────┐
                 │     engine.py (SchedulerEngine)  │
                 │         run_tick() loop          │
                 └──┬──────┬──────┬────────┬───────┘
                    │      │      │        │
          ┌─────────┘      │      │        └──────────┐
          ▼                ▼      ▼                    ▼
   ┌─────────────┐  ┌──────────┐  ┌──────────────┐  ┌───────────┐
   │QueueManager │  │Workflow  │  │  Scheduler   │  │ Observer  │
   │ admission   │  │Manager   │  │  (scoring +  │  │ (records  │
   │ heap + task │  │(DAG +    │  │  placement)  │  │ outcomes) │
   │ priority Q  │  │failures) │  │              │  │           │
   └─────────────┘  └──────────┘  └──────┬───────┘  └─────┬─────┘
                                         │                 │
                                         ▼                 ▼
                               ┌───────────────────────────────┐
                               │        ProfileStore           │
                               │  (per-task, per-node learning │
                               │   rolling window of 20 obs.)  │
                               └───────────────────────────────┘
```

| Mode | Entry Point | Description |
|------|-------------|-------------|
| **Simulation** | `run_simulation.py` | No K8s. Fake runtimes. 6 nodes, 10 workflows in 3 bursts. |
| **K8s Orchestrator** | `k8s_main.py` | Creates pods directly via API. Blocks per-task. Sequential DAG walk. |
| **K8s Custom Scheduler** | `k8s_scheduler.py` | Deploys as a K8s Deployment. Watches Pending pods, binds to chosen nodes. |
| **REST Server** | `server.py` | Flask API with `--simulate` mode. Background tick loop every 1s. |

---

## Scheduling Algorithm

### 8-Factor Scoring Model

Every candidate node is scored across **8 weighted factors**:

| Factor | Weight | Description |
|--------|-------:|-------------|
| **Type Affinity** | +30 | Learned preference — which node *type* runs this task fastest |
| **Node Availability** | +25 | How soon current tasks on this node will finish |
| **Resource Fit** | +20 | CPU/memory headroom + core-scaling bonus |
| **Warm Image** | +10 | Container image already cached from a previous run |
| **Load Balance** | +10 | Fewer running tasks → less contention |
| **Data Locality** | +5 | Prefer the node where the parent task ran |
| **Failure Penalty** | −10 | Historical failure rate on this node |
| **Memory Pressure** | −15 | Sharp penalty when free memory drops below 15% |

### Score Computation

```
Final Score = Σ (factor_score × weight)
```

- **Type Affinity**: Rank-based from `preferred_node_order` (sorted by median total cost). Unknown types get 50%.
- **Resource Fit**: 35% CPU fit + 35% memory fit + 30% core-scaling bonus.
- **Availability**: Decays linearly over 30s based on `estimated_free_in` (soonest task ETA).
- **Load Balance**: `(1 − running_tasks / max_concurrent)` where `max_concurrent = max(total_cpu × 2, 4)`.
- **Memory Pressure**: Linear penalty when `free_memory / total_memory < 0.15`.

---

## Exploration & Learning

### Three-Phase Exploration Strategy

The scheduler avoids premature exploitation through a phased approach:

| Phase | Trigger | Action |
|-------|---------|--------|
| **A — Coverage** | Any compatible node has **0 observations** | Pick a random unseen node |
| **B — Depth** | Any node has **< 3 observations** | Pick a random underexplored node |
| **C — Normal** | All nodes sufficiently explored | 10% random explore · 90% score-based exploit |

### Learning System

Each task execution produces an **Observation**:
- `runtime` — actual task execution time
- `startup` — image pull + container boot time
- `cpu_usage_ratio` / `memory_usage_ratio` — resource utilization at start

Observations are stored in a **rolling window of 20** per (task, node) pair. The scheduler computes **median** runtime and startup to smooth outliers, then ranks nodes by **total cost** (runtime + startup).

```
Observation → NodeMetrics (per node, median of 20)
           → NodeTypeMetrics (per node type, median across nodes)
           → preferred_node_order (ranked list of node types)
           → preferred_node_ids (ranked list of specific nodes)
```

---

## Project Structure

```
TaskScheduler/
├── engine.py                    # SchedulerEngine — main tick loop
├── server.py                    # Flask REST API server
├── k8s_main.py                  # Direct K8s pod orchestration
├── k8s_scheduler.py             # Custom K8s scheduler (Deployment mode)
├── run_simulation.py            # Local simulation (no K8s)
├── test_simulation.py           # Test suite (7 tests, 25 assertions)
│
├── models/
│   ├── enums.py                 # NodeType, TaskClass, PriorityClass, TaskState...
│   ├── cluster.py               # Node, RunningTask, ClusterScenario
│   ├── workload.py              # WorkflowTemplate, TaskTemplate, instances
│   ├── profile.py               # Observation, NodeMetrics, TaskProfile
│   └── profile_store.py         # ProfileStore — the learning database
│
├── services/
│   ├── scheduler.py             # PlacementAlgorithm + WorkflowSchedulerRunner
│   ├── queue_manager.py         # Priority heap + task queue with aging
│   ├── workflow_manager.py      # DAG resolution + failure propagation
│   ├── observer.py              # ExecutionObserver — records outcomes
│   ├── data_manager.py          # Inter-task data passing via filesystem
│   └── k8s_cluster.py           # Live K8s resource polling (Metrics API)
│
├── tasks/
│   ├── Dockerfile               # Combined image with all 3 task scripts
│   ├── task_cpu/
│   │   ├── Dockerfile           # ts-task-cpu:v1
│   │   └── task_cpu.py          # SHA-256 computation workload
│   ├── task_io/
│   │   ├── Dockerfile           # ts-task-io:v1
│   │   └── task_io.py           # File I/O workload (50 MB write/read)
│   └── task_mem/
│       ├── Dockerfile           # ts-task-mem:v1
│       └── task_mem.py          # Memory allocation workload (200 MB)
│
├── workflows/
│   └── 3taskworkflow.json       # DAG: task-io → task-mem → task-cpu
│
├── kind-cluster.yaml            # 6-worker heterogeneous kind cluster
├── scheduler-deployment.yaml    # K8s RBAC + Deployment for the scheduler
├── Dockerfile.scheduler         # Container image for k8s_scheduler.py
└── requirements.txt             # Python dependencies
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.9+ | Scheduler runtime |
| **Docker Desktop** | Latest | Container engine |
| **kind** | 0.20+ | Local Kubernetes clusters |
| **kubectl** | 1.28+ | Kubernetes CLI |

---

## Quick Start — Local Simulation

No Kubernetes required. Simulates 6 heterogeneous nodes and 10 workflows:

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Activate (Git Bash / Linux / macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the simulation
python run_simulation.py

# Run tests
python test_simulation.py
```

The simulation uses realistic per-node-type runtimes (tasks run 2–3× faster on their matching node type):

| Task | IO_OPT | CPU_OPT | MEM_OPT |
|------|-------:|--------:|--------:|
| task-io | **1.5s** | 4.0s | 3.0s |
| task-mem | 4.5s | 5.0s | **2.0s** |
| task-cpu | 5.5s | **2.0s** | 6.0s |

---

## Kubernetes Deployment

### Cluster Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                     ts-cluster (kind)                           │
│                                                                 │
│  ┌──────────────┐                                               │
│  │ control-plane│                                               │
│  └──────────────┘                                               │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐     CPU_OPT                │
│  │ts-node-cpu-1 │  │ts-node-cpu-2 │     2 vCPU · 1 GB RAM      │
│  └──────────────┘  └──────────────┘                             │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐     MEM_OPT                │
│  │ts-node-mem-1 │  │ts-node-mem-2 │     1 vCPU · 2 GB RAM      │
│  └──────────────┘  └──────────────┘                             │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐     IO_OPT                 │
│  │ts-node-io-1  │  │ts-node-io-2  │     1 vCPU · 1 GB RAM      │
│  └──────────────┘  └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

### 1. Create the Kind Cluster

```bash
kind create cluster --config kind-cluster.yaml
```

Verify nodes are ready:

```bash
kubectl get nodes -o wide
```

Expected output — 6 workers + 1 control-plane, all `Ready`:

```
NAME                       STATUS   ROLES           AGE   VERSION
ts-cluster-control-plane   Ready    control-plane   1m    v1.31.x
ts-node-cpu-1              Ready    <none>          1m    v1.31.x
ts-node-cpu-2              Ready    <none>          1m    v1.31.x
ts-node-io-1               Ready    <none>          1m    v1.31.x
ts-node-io-2               Ready    <none>          1m    v1.31.x
ts-node-mem-1              Ready    <none>          1m    v1.31.x
ts-node-mem-2              Ready    <none>          1m    v1.31.x
```

### 2. Enforce Physical Resource Limits

Kind nodes run as Docker containers. By default they share the host's full resources. Apply **cgroup limits** to simulate real heterogeneous hardware.

> **Important:** Kind names Docker containers differently from K8s node names.
> Workers are named `ts-cluster-worker`, `ts-cluster-worker2`, ..., `ts-cluster-worker6`
> in the order they appear in `kind-cluster.yaml`.

First, verify the mapping between Docker containers and K8s nodes:

```bash
# List all kind containers with their K8s node names
for container in $(docker ps --filter "label=io.x-k8s.kind.cluster=ts-cluster" --format "{{.Names}}"); do
  node_name=$(docker exec "$container" hostname)
  echo "$container  →  $node_name"
done
```

Then apply resource limits:

```bash
# CPU-Optimised nodes (2 vCPU, 1 GB RAM)
docker update --cpus 2 --memory 1g --memory-swap 1g ts-cluster-worker     # ts-node-cpu-1
docker update --cpus 2 --memory 1g --memory-swap 1g ts-cluster-worker2    # ts-node-cpu-2

# Memory-Optimised nodes (1 vCPU, 2 GB RAM)
docker update --cpus 1 --memory 2g --memory-swap 2g ts-cluster-worker3    # ts-node-mem-1
docker update --cpus 1 --memory 2g --memory-swap 2g ts-cluster-worker4    # ts-node-mem-2

# IO-Optimised nodes (1 vCPU, 1 GB RAM)
docker update --cpus 1 --memory 1g --memory-swap 1g ts-cluster-worker5    # ts-node-io-1
docker update --cpus 1 --memory 1g --memory-swap 1g ts-cluster-worker6    # ts-node-io-2
```

> **Note:** The container-to-node mapping above assumes kind creates workers in YAML order,
> which is the default behavior. Always verify with the `for` loop above after cluster creation.

### 3. Install Metrics Server

The scheduler polls real CPU/memory usage via the Kubernetes Metrics API. Install metrics-server with flags for kind compatibility:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch for kind (self-signed certs + hostname resolution)
kubectl patch deployment metrics-server -n kube-system --type=json \
  -p '[
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"},
    {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-preferred-address-types=InternalIP"}
  ]'
```

Wait for it to be ready (~60 seconds):

```bash
kubectl rollout status deployment/metrics-server -n kube-system --timeout=120s

# Verify metrics are available
kubectl top nodes
```

### 4. Build & Load Container Images

Build the task images and load them into the kind cluster (no registry needed):

```bash
# Build task images
docker build -t ts-task-cpu:v1 tasks/task_cpu/
docker build -t ts-task-mem:v1 tasks/task_mem/
docker build -t ts-task-io:v1  tasks/task_io/

# Build the scheduler image
docker build -t ts-scheduler:v1 -f Dockerfile.scheduler .

# Load all images into kind
kind load docker-image ts-task-cpu:v1 ts-task-mem:v1 ts-task-io:v1 ts-scheduler:v1 \
  --name ts-cluster
```

### 5. Deploy the Scheduler

The deployment manifest creates: ServiceAccount, ClusterRole, ClusterRoleBinding, and Deployment.

```bash
kubectl apply -f scheduler-deployment.yaml
```

Verify the scheduler pod is running:

```bash
kubectl get pods -l app=ts-scheduler
kubectl logs -l app=ts-scheduler -f
```

### 6. Verify Labels & Nodes

```bash
# Check node types are correctly labelled
kubectl get nodes --show-labels | grep -E "node-type|ts.capacity"

# Compact view
kubectl get nodes -L node-type -L ts.capacity/cpu -L ts.capacity/memory
```

Expected:

```
NAME              STATUS   node-type   ts.capacity/cpu   ts.capacity/memory
ts-node-cpu-1     Ready    CPU_OPT     2.0               1024
ts-node-cpu-2     Ready    CPU_OPT     2.0               1024
ts-node-mem-1     Ready    MEM_OPT     1.0               2048
ts-node-mem-2     Ready    MEM_OPT     1.0               2048
ts-node-io-1      Ready    IO_OPT      1.0               1024
ts-node-io-2      Ready    IO_OPT      1.0               1024
```

### 7. Submit a Workflow

Create a test pod using the custom scheduler:

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: test-io-task
  annotations:
    ts.scheduler/task_class: "IO_BOUND"
    ts.scheduler/compatible_node_types: "IO_OPT,CPU_OPT,MEM_OPT"
spec:
  schedulerName: ts-scheduler
  containers:
    - name: worker
      image: ts-task-io:v1
      imagePullPolicy: Never
      resources:
        requests:
          cpu: "500m"
          memory: "128Mi"
        limits:
          cpu: "1"
          memory: "256Mi"
  restartPolicy: Never
EOF
```

Watch the scheduler assign it:

```bash
kubectl get pod test-io-task -w
kubectl logs test-io-task
```

### 8. Run from Local Machine (Development)

For faster iteration, run the scheduler locally while connected to the kind cluster:

```bash
# Ensure kubeconfig points to kind
kubectl config use-context kind-ts-cluster

# Option A: Direct orchestrator (sequential DAG execution)
python k8s_main.py

# Option B: REST API server (simulated mode)
python server.py --simulate

# Option C: REST API server (live K8s mode)
python server.py
```

---

## Monitoring & Debugging

```bash
# Watch all pods in real time
kubectl get pods -A -w

# Check scheduler logs
kubectl logs -l app=ts-scheduler -f --tail=100

# View node resource usage (requires metrics-server)
kubectl top nodes
kubectl top pods

# Debug a specific pod
kubectl describe pod <pod-name>
kubectl logs <pod-name>

# Check events for scheduling decisions
kubectl get events --sort-by=.metadata.creationTimestamp | tail -20

# Inspect a node's running pods
kubectl get pods --field-selector spec.nodeName=ts-node-cpu-1
```

---

## Testing

The test suite runs **7 tests with 25 assertions** — no external dependencies required:

```bash
python test_simulation.py
```

| Test | What it validates |
|------|-------------------|
| `test_basic_dag` | IO → MEM → CPU executes in correct DAG order |
| `test_concurrent_workflows` | Two workflows run independently in parallel |
| `test_warm_image_bonus` | Image warmth score is 0 before first run, 10 after |
| `test_failure_propagation` | Failed parent cascades FAILED to all descendants |
| `test_learning_convergence` | After 15 runs, IO tasks prefer IO_OPT nodes |
| `test_exploration_covers_all_nodes` | First 3 runs visit 3 distinct node types |
| `test_priority_ordering` | CRITICAL tasks dispatch before BATCH |

---

## Cleanup

```bash
# Delete all test pods
kubectl delete pods --all

# Delete the entire cluster
kind delete cluster --name ts-cluster
```

---

<div align="center">

*Built for the Master's Thesis — University Politehnica of Bucharest, 2026*

</div>
