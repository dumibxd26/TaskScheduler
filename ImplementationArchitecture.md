# Implementation Architecture — Adaptive Workflow Scheduler

> **Status:** Draft v1 · May 2026
> **Companion to:** [ProblemSpecification.md](ProblemSpecification.md) (problem definition).
> **Audience:** Implementer (you), thesis defense reviewers, future maintainers.
> **Goal of this document:** Describe **how** the scheduler is built — every component, every file, every interface, every wire — including (a) what already exists in the repo today, (b) what must be added, (c) the exact code/logic shape of the additions, and (d) the rollout phases.

This document deliberately complements `ProblemSpecification.md`:
- *Problem spec* answers: **what** must the scheduler decide, **why**, under **which constraints**.
- *This document* answers: **which classes/files/processes/pods** will physically exist and **how** they communicate.

If anything in this file appears to contradict the problem spec, the problem spec wins; raise an issue and re-align.

---

## Table of Contents

- [Part I — Topology (Bird's-Eye View)](#part-i--topology-birds-eye-view)
- [Part II — Implementation Status Audit](#part-ii--implementation-status-audit)
- [Part III — Data Channels A / B / C](#part-iii--data-channels-a--b--c)
- [Part IV — Component Inventory](#part-iv--component-inventory)
- [Part V — New Components To Build](#part-v--new-components-to-build)
- [Part VI — Data Plane: Producer-Local Storage](#part-vi--data-plane-producer-local-storage)
- [Part VII — Bandwidth Probe Subsystem](#part-vii--bandwidth-probe-subsystem)
- [Part VIII — Thermal Subsystem](#part-viii--thermal-subsystem)
- [Part IX — Scheduler Core: Two-Tier Architecture](#part-ix--scheduler-core-two-tier-architecture)
- [Part X — Learning Subsystem (ProfileStore v2)](#part-x--learning-subsystem-profilestore-v2)
- [Part XI — Preemption & Defragmentation](#part-xi--preemption--defragmentation)
- [Part XII — Pod Specs (Concrete YAML/Python)](#part-xii--pod-specs-concrete-yamlpython)
- [Part XIII — Persistence Layer](#part-xiii--persistence-layer)
- [Part XIV — Observability & Logging](#part-xiv--observability--logging)
- [Part XV — Test Harness](#part-xv--test-harness)
- [Part XVI — Migration Plan (Phased Rollout)](#part-xvi--migration-plan-phased-rollout)
- [Part XVII — File-by-File Change Map](#part-xvii--file-by-file-change-map)
- [Part XVIII — Open Implementation Questions](#part-xviii--open-implementation-questions)

---

## Part I — Topology (Bird's-Eye View)

```
                      ┌──────────────────────────────────────┐
                      │     CLIENT (submit_workflows.py)     │
                      │   POSTs WorkflowInstance to the API  │
                      └───────────────┬──────────────────────┘
                                      │ HTTP / k8s API
                                      ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        TS-SCHEDULER POD (1 replica)                   │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                       SchedulerEngine                           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐    │  │
│  │  │ QueueManager │  │ Resolver     │  │  Policy (pluggable) │    │  │
│  │  │ (workflows + │  │ (DAG → READY)│  │  - LegacyEightFactor│    │  │
│  │  │  tasks)      │  │              │  │  - HeftPolicy       │    │  │
│  │  │              │  │              │  │  - AdaptivePolicy ★ │    │  │
│  │  └──────────────┘  └──────────────┘  └─────────────────────┘    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐    │  │
│  │  │ ProfileStore │  │ DataPlacement│  │  PreemptionPlanner ★│    │  │
│  │  │ (v2 buckets) │  │ Registry  ★  │  │  + DefragPlanner  ★ │    │  │
│  │  └──────────────┘  └──────────────┘  └─────────────────────┘    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐    │  │
│  │  │ K8sBinder    │  │ Observer     │  │  ThermalCollector ★ │    │  │
│  │  │ (Pod→Node)   │  │ (completion) │  │  BandwidthCollector★│    │  │
│  │  └──────────────┘  └──────────────┘  └─────────────────────┘    │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │ k8s client                             │
└──────────────────────────────┼────────────────────────────────────────┘
                               ▼
        ┌──────────────────────────────────────────────────────┐
        │                  KUBERNETES API                      │
        └──────────┬───────────────────────────────┬───────────┘
                   │                               │
                   ▼                               ▼
   ┌──────────────────────────────┐   ┌──────────────────────────────┐
   │ DaemonSet: ts-fileserver  ★  │   │ DaemonSet: ts-bw-probe    ★  │
   │ (per-node HTTP server for    │   │ (per-node bandwidth tester)  │
   │  /var/lib/ts-data/<wfid>/)   │   │                              │
   └──────────────────────────────┘   └──────────────────────────────┘
                   │
   ┌───────────────┴────────────────────────────────────────────┐
   │ DaemonSet: ts-thermal-collector  ★                         │
   │ (per-node temperature scraper, exposes /metrics)           │
   └────────────────────────────────────────────────────────────┘

           ↓                                    ↓                 ↓
   ┌──────────────┐                  ┌──────────────┐    ┌──────────────┐
   │  Worker N1   │  ←── HTTP fetch  │  Worker N2   │    │  Worker N3   │
   │  Task Pods   │  (initContainer) │  Task Pods   │    │  Task Pods   │
   │  + fileserv  │                  │  + fileserv  │    │  + fileserv  │
   │  + thermal   │                  │  + thermal   │    │  + thermal   │
   │  + bw-probe  │                  │  + bw-probe  │    │  + bw-probe  │
   └──────────────┘                  └──────────────┘    └──────────────┘

   ★ = new components introduced by this architecture (not in current code)
```

### 1.1 Two execution modes

The codebase supports two parallel runtimes that share the **same** `models/` and `services/` core:

| Mode | Driver | Cluster | Purpose |
|---|---|---|---|
| **Simulation** | [run_simulation.py](run_simulation.py), [test_simulation.py](test_simulation.py), [main.py](main.py) | In-process `ClusterScenario` with synthetic nodes; no real pods. Tasks are simulated by `time.sleep` + scripted observations. | Fast iteration, batch ablation studies, deterministic seeded experiments. |
| **Real K8s** | [k8s_scheduler.py](k8s_scheduler.py), [submit_workflows.py](submit_workflows.py), [k8s_main.py](k8s_main.py) | A `kind` cluster (6 workers, see [kind-cluster.yaml](kind-cluster.yaml)) or a real heterogeneous cluster. Pods are real; metrics, image cache, and runtime are observed. | Validate that decisions translate to real wall-clock; defense demo. |

Both modes share `Policy`, `ProfileStore`, `DataPlacement`, `QueueManager`, `ReadinessResolver`, and the new planners. **The only difference is the I/O layer**: in-process function calls vs. k8s-API binds.

---

## Part II — Implementation Status Audit

Snapshot of the repo as of this writing, mapped against the entity model in [ProblemSpecification.md §4](ProblemSpecification.md). **This is the baseline; everything marked "✗" is what this architecture document tells you to add.**

### 2.1 Models ([models/](models/))

| Spec entity | File | Status | Gap |
|---|---|---|---|
| `Node` | [models/cluster.py](models/cluster.py) | ✓ Partial | Missing `cooling_class`, `thermal_throttle_temp_c`, `cpu_temperature`, `thermal_headroom`. |
| `RunningTask` | [models/cluster.py](models/cluster.py) | ✓ | Complete. |
| `ClusterScenario` | [models/cluster.py](models/cluster.py) | ✓ Partial | Missing `bandwidth_matrix: Dict[Tuple[str,str], float]`. |
| `TaskTemplate` | [models/workload.py](models/workload.py) | ✓ Partial | Missing `checkpointable`, `checkpoint_interval_s`, `gang_group_id`, `expected_output_bytes`. |
| `TaskInstance` | [models/workload.py](models/workload.py) | ✓ Partial | Missing `last_checkpoint_at`, `preemption_count`, `upward_rank`. |
| `WorkflowInstance` | [models/workload.py](models/workload.py) | ✓ Partial | Missing `vruntime`, `upward_rank_max`. |
| `DependencyEdge` | [models/workload.py](models/workload.py) | ✓ | Already distinguishes `EXECUTION` vs `DATA`, with `data_field_names`. |
| `DataPlacement` | — | ✗ | **Missing entirely.** New file: `services/data_placement.py`. |
| `Observation` | [models/profile.py](models/profile.py) | ✓ Partial | Missing `io_bytes_read/written`, `output_bytes_by_field`, `temperature_at_start/end`. |
| `NodeMetrics` | [models/profile.py](models/profile.py) | ✓ Partial | Missing variance, EMA, contextual buckets. |
| `TaskProfile` | [models/profile.py](models/profile.py) | ✓ Partial | Buggy `exploration_level` (degenerate); monotonic `failures_by_node` (no decay). |

### 2.2 Services ([services/](services/))

| Spec component | File | Status | Gap |
|---|---|---|---|
| Queue & admission | [services/queue_manager.py](services/queue_manager.py) | ✓ | Already has aging, sorted retrieval. Will need vruntime hook. |
| DAG readiness | [services/workflow_manager.py](services/workflow_manager.py) | ✓ | Already propagates failures. Needs gang readiness check. |
| Placement scoring | [services/scheduler.py](services/scheduler.py) | ✓ Legacy | The current 8-factor scorer becomes `LegacyEightFactorPolicy`; new policies sit beside it. |
| Profile updates | [services/profile_store.py](models/profile_store.py) | ✓ Partial | Needs bucket-aware `record_observation`, EMA, decay, variance. |
| Completion observer | [services/observer.py](services/observer.py) | ✓ Partial | Needs to record contextual fields, output sizes, temperatures, transfer time. |
| Cluster polling | [services/k8s_cluster.py](services/k8s_cluster.py) | ✓ Partial | Needs `cooling_class` from node labels and temperature read from node-exporter / DaemonSet. |
| Data manager | [services/data_manager.py](services/data_manager.py) | ✗ Wrong model | Current implementation is the **shared-NAS** model that the spec rejected. Replace per Part VI. |
| `Policy` interface | — | ✗ | Missing — placement is hardwired in `WorkflowSchedulerRunner`. Introduce in `services/policy.py`. |
| `DataPlacement` registry | — | ✗ | New file: `services/data_placement.py`. |
| `PreemptionPlanner` | — | ✗ | New file: `services/preemption.py`. |
| `DefragPlanner` | — | ✗ | New file: `services/defrag.py`. |
| `ThermalCollector` | — | ✗ | New file: `services/thermal.py` (scheduler side) + DaemonSet (cluster side). |
| `BandwidthCollector` | — | ✗ | New file: `services/bandwidth.py` (scheduler side) + DaemonSet (cluster side). |
| Critical-path / upward rank | — | ✗ | New file: `services/dag_metrics.py`. |
| ECT calculator | — | ✗ | New file: `services/ect.py`. |
| Trace driver (open arrival) | — | ✗ | New file: `tools/trace_driver.py`. |
| DAX importer (Pegasus) | — | ✗ | New file: `tools/dax_importer.py`. |

### 2.3 Engine & entry points

| Spec | File | Status | Gap |
|---|---|---|---|
| `run_tick(state)` entry point | [engine.py](engine.py) | ✓ Per-task greedy | Replace inner loop with batched plan-search calling `Policy.decide(state)`. |
| Real-cluster scheduler | [k8s_scheduler.py](k8s_scheduler.py) | ✓ | Needs to wire in new collectors, planners, and Policy. |
| Simulation driver | [run_simulation.py](run_simulation.py), [main.py](main.py) | ✓ | Needs trace-driven mode (Poisson arrivals). |

### 2.4 Cluster artifacts

| Artifact | File | Status | Gap |
|---|---|---|---|
| Kind cluster | [kind-cluster.yaml](kind-cluster.yaml) | ✓ | Currently mounts a **single shared host dir** to every kind-node — must change to per-node host dirs (Part VI.4). Add `cooling-class` label per node. |
| Scheduler deployment | [scheduler-deployment.yaml](scheduler-deployment.yaml) | ✓ Partial | Needs RBAC for fileserver/thermal/bw-probe DaemonSets and for reading metrics. |
| Scheduler image | [Dockerfile.scheduler](Dockerfile.scheduler) | ✓ | OK. |
| Task images | [tasks/](tasks/) | ✓ | The three sample tasks must drop the shared-volume helpers and start writing to a per-pod local hostPath (Part VI.5). |
| Fileserver DaemonSet | — | ✗ | New: `k8s/fileserver-daemonset.yaml` + minimal Go/Python image. |
| BW-probe DaemonSet | — | ✗ | New: `k8s/bw-probe-daemonset.yaml`. |
| Thermal DaemonSet | — | ✗ | New: `k8s/thermal-collector-daemonset.yaml` (Linux uses `node-exporter`; macOS workers need a custom one or fall back to `None`). |

---

## Part III — Data Channels A / B / C

This is the question that triggered this document. **Are the three channels implemented?** Short answer: **A is fully implemented, B is *not* implemented (the current code uses a different — incompatible — shared-volume model), C is implicit and only opportunistic.**

### 3.1 Channel A — Inline metadata via `__TS_OUTPUT__` logs

**Status: ✅ FULLY IMPLEMENTED.**

**Mechanism (already in repo):**
1. Each task ends with `print(f"__TS_OUTPUT__={json.dumps(output)}")` — see [tasks/task_io/task_io.py:49](tasks/task_io/task_io.py), [tasks/task_mem/task_mem.py:53](tasks/task_mem/task_mem.py), [tasks/task_cpu/task_cpu.py:59](tasks/task_cpu/task_cpu.py).
2. After a child pod's parents have completed, the orchestrator reads pod logs with `read_namespaced_pod_log` and grabs the `__TS_OUTPUT__=...` line — see `extract_task_output` in [k8s_main.py:105-116](k8s_main.py) and `extract_output` in [submit_workflows.py:121-128](submit_workflows.py).
3. The parsed dict is injected as **environment variables** into the child pod (`env_vars[f] = saved_outputs[parent_id][f]`).

**Cost model (used by scheduler):** zero. Logs are at most a few KB, fetched via the k8s API which sits on the control-plane LAN; the scheduler does **not** add a transfer term to ECT for Channel A payloads.

**Limitations:** payload must be JSON-serialisable and small (k8s log buffers are typically 10–256 KB per container). If a task tries to pass a 10 MB blob this way, k8s truncates the log.

**No changes needed for the new architecture.** Channel A is the right tool for control-plane metadata (file paths, counts, status flags, hashes) and we keep it.

### 3.2 Channel B — Producer-local hostPath + initContainer fetch via fileserver DaemonSet

**Status: ❌ NOT IMPLEMENTED. The current code does the *opposite*.**

**What the current code does (the *wrong* model per the spec):**

Look at [kind-cluster.yaml:18-22](kind-cluster.yaml):
```yaml
extraMounts:
  - hostPath: /tmp/ts-shared-data
    containerPath: /ts-data
```
**Every kind-worker mounts the *same* host directory** `/tmp/ts-shared-data` (i.e., the developer's macOS host-machine path) into `/ts-data` inside each kind-node. Then [k8s_main.py:14-15](k8s_main.py) plus the pod spec mounts `/ts-data` (host on the kind-node) → `/data/shared` (inside every pod) via `hostPath`.

Net effect: **all pods on all nodes see the same files**. This is a pseudo-NAS sitting on the developer machine. It works for development but it is *exactly* the shared-storage model that [ProblemSpecification.md §2.4](ProblemSpecification.md) rejected. The current `services/data_manager.py` (`SharedVolumeDataManager`) is built on this assumption.

**What the new architecture requires:**

1. Each kind-node mounts its **own** host directory (e.g., `/tmp/ts-shared-data-N1`, `…-N2`, …) — *not* a shared one. On a real heterogeneous cluster this is automatic: each physical machine has its own disk.
2. A `ts-fileserver` DaemonSet runs one pod per node, exposing the local directory via HTTP on a known port (e.g., `8081`).
3. When the scheduler decides to place child task `τ_c` on node `n_c` and `τ_c` has a DATA edge from parent `τ_p` (which produced `field_X`), and `τ_p` ran on node `n_p ≠ n_c`, then the scheduler **adds an `initContainer`** to `τ_c`'s pod spec that does `wget http://ts-fileserver.<n_p>.svc:8081/<wfid>/<task_p_id>/field_X.bin -O /data/local/field_X.bin`.
4. The main container of `τ_c` reads `/data/local/field_X.bin` from its own pod-local volume.
5. The `initContainer` records its wall-clock duration → fed back to `BandwidthCollector` (Part VII) to refine the bandwidth matrix.

**Implementation is detailed in [Part VI](#part-vi--data-plane-producer-local-storage).**

### 3.3 Channel C — Same-node co-location (zero transfer)

**Status: ⚠️ IMPLICIT, NOT FIRST-CLASS.**

Today, when a child happens to be placed on the same node as its parent, the shared `/data/shared` is still read (Channel B's broken cousin), so it's "free" — but only by accident: the scheduler does not actively *try* to co-locate parent and child.

**What the new architecture requires:** Channel C is the emergent property of Channel B done correctly:

- If `n_p == n_c`, the new pod spec emits **no initContainer** for that field (the file is already on the local disk under `/var/lib/ts-data/...`).
- The main container instead mounts the local hostPath **read-only** at `/data/inputs/field_X.bin`.
- The placement decision actively prefers `n_p` for the child because the transfer term in ECT is zero only when `n_p == n_c` (see [ProblemSpecification.md §8.5](ProblemSpecification.md)).

**No new code beyond Channel B.** Channel C is "Channel B without the initContainer wget step" — and that branch already needs to exist anyway because even with B implemented, the optimal case is to *avoid* a transfer.

### 3.4 Summary table

| Channel | Payload | Transport | Cost | Status |
|---|---|---|---|---|
| **A** | KB-scale JSON metadata | Pod stdout → `kubectl logs` → env-var injection | Zero (treated as control-plane) | ✅ done |
| **B** | MB-to-GB output files | Producer-local `hostPath` → `ts-fileserver` HTTP → consumer's `initContainer` | `bytes / BandwidthMatrix[(n_p, n_c)]` | ❌ to build |
| **C** | Any size | Same-node `hostPath` mount; no network | Zero | ⚠️ becomes implicit once B is built correctly |

---

## Part IV — Component Inventory

This part lists *existing* components and what role each plays in the new architecture. New components are in [Part V](#part-v--new-components-to-build).

### 4.1 `models/` — pure data classes (no behaviour)

These have **no I/O**, **no side effects**. They are immutable-ish containers used by every layer above.

- **[models/enums.py](models/enums.py)** — Enums for `NodeType`, `TaskClass`, `WorkflowClass`, `PriorityClass`, `TaskState`, `WorkflowState`, `DependencyType`. **Add:** `CoolingClass {PASSIVE, STANDARD, HIGH, EXTREME}`. **Verify:** `DependencyType` already has `EXECUTION` and `DATA` (it does — line 50–52).
- **[models/cluster.py](models/cluster.py)** — `Node`, `RunningTask`, `ClusterScenario`. **Add per Part II.1:** thermal fields on `Node`, `bandwidth_matrix` on `ClusterScenario`.
- **[models/workload.py](models/workload.py)** — `TaskTemplate`, `TaskInstance`, `WorkflowTemplate`, `WorkflowInstance`, `DependencyEdge`. **Add per Part II.1:** the listed missing fields.
- **[models/profile.py](models/profile.py)** — `Observation`, `NodeMetrics`, `TaskProfile`. **Restructure per Part X.**
- **[models/profile_store.py](models/profile_store.py)** — `ProfileStore` (in-memory + JSON serialisation). **Restructure per Part X.**

### 4.2 `services/` — stateful, single-responsibility units

- **[services/queue_manager.py](services/queue_manager.py)** — `QueueManager`. Maintains the workflow admission heap and the flat task entry list. Handles aging. Will be extended with vruntime accounting (Part IX.4).
- **[services/workflow_manager.py](services/workflow_manager.py)** — `ReadinessResolver`. DAG → READY tasks; failure propagation. Will be extended with **gang readiness** (all-or-nothing membership check, [ProblemSpec §5.9](ProblemSpecification.md)).
- **[services/scheduler.py](services/scheduler.py)** — currently houses `PlacementAlgorithm` (8-factor scorer) and `WorkflowSchedulerRunner`. **Refactor:** rename to `LegacyEightFactorPolicy`; move into a new `Policy` taxonomy (Part IX.2).
- **[services/observer.py](services/observer.py)** — `ExecutionObserver.record_task_completion / record_task_failure`. Stamps the task FINISHED/FAILED, calls `ProfileStore.record_observation`. **Extend:** record actual `output_bytes_by_field`, `temperature_at_start/end`, `transfer_seconds` (when an initContainer ran).
- **[services/data_manager.py](services/data_manager.py)** — `DataExchangeManager` interface + `FileStoreDataManager` (sim) + `SharedVolumeDataManager` (k8s). **Replace** the latter with `ProducerLocalDataManager` per [Part VI](#part-vi--data-plane-producer-local-storage). The sim variant stays as-is for in-process tests.
- **[services/k8s_cluster.py](services/k8s_cluster.py)** — `poll_cluster_state`. Reads node labels + allocatable + metrics-server. **Extend:** read `cooling-class`, `thermal-throttle-temp-c` labels; query `ThermalCollector` for current temperature; query `BandwidthCollector` for matrix.

### 4.3 Engine & entry points

- **[engine.py](engine.py)** — `SchedulerEngine.run_tick`. Currently does per-task greedy dispatch with virtual capacity. **Replace** the inner loop with `policy.decide(state) → ActionSet` (Part IX.3).
- **[k8s_scheduler.py](k8s_scheduler.py)** — Real-cluster scheduler with k8s watcher loop, completion observer, profile persistence. **Extend** to wire in new components (`DataPlacement`, `BandwidthCollector`, `ThermalCollector`, planners) and to mutate pod specs with initContainers when needed.
- **[k8s_main.py](k8s_main.py)** — Standalone driver that submits a single workflow without the deployed scheduler (uses `node_name` directly to pin pods). Useful for the simulation/manual mode; touches the data manager. Will use the simulation `DataExchangeManager` once the producer-local one ships.
- **[run_simulation.py](run_simulation.py)** — Closed-batch simulator for in-process experiments. **Extend** to accept trace files and Poisson arrival rates (Part XV).
- **[submit_workflows.py](submit_workflows.py)** — End-user CLI: submits N workflows to the deployed scheduler. **Extend** when adding new workflow shapes (DAX import, Pegasus benchmarks).
- **[server.py](server.py)** — Flask/FastAPI service that exposes a workflow-submission HTTP API; same Channel A logic. Optional path; not on the critical defense path.
- **[test_simulation.py](test_simulation.py)** — pytest. Will gain new tests per [Part XV](#part-xv--test-harness).

### 4.4 Tasks ([tasks/](tasks/))

Each task is a Python program in its own image (`ts-task-cpu:v1`, `ts-task-mem:v1`, `ts-task-io:v1`). They currently use the shared volume; **change them** to use Channel A for metadata and write bulk outputs to a single pod-local directory (Part VI.5).

### 4.5 Cluster artifacts

- **[kind-cluster.yaml](kind-cluster.yaml)** — kind cluster topology. **Modify** mounts (per-node host dirs); add cooling-class labels.
- **[scheduler-deployment.yaml](scheduler-deployment.yaml)** — RBAC + Deployment for the scheduler pod. **Extend** RBAC for new resources.
- **[setup_cluster.sh](setup_cluster.sh)** — bootstrap script. **Extend** to apply the three new DaemonSets.

---

## Part V — New Components To Build

This part is the spec for code that **does not yet exist**. Each subsection is sized so a single PR can implement it.

### 5.1 `services/policy.py` — `Policy` interface and registry

```python
# services/policy.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional
from models.cluster import ClusterScenario
from models.workload import TaskInstance, TaskTemplate
from services.queue_manager import TaskEntry


@dataclass
class PlaceAction:
    task_instance_id: str
    node_id: str
    expected_finish: float        # for J() bookkeeping
    expected_runtime: float       # for register_task on the chosen node
    transfer_seconds: float = 0.0 # initContainer cost (Channel B)


@dataclass
class PreemptAction:
    victim_task_instance_id: str
    mode: str                     # "kill_restart" or "checkpoint"


@dataclass
class HoldAction:
    task_instance_id: str
    reason: str                   # human-readable (e.g., "no_feasible_node")


ActionSet = Dict[str, list]       # {"place": [...], "preempt": [...], "hold": [...]}


class Policy(ABC):
    """Pluggable scheduling brain. One per scheduler instance."""

    @abstractmethod
    def decide(self, state: "SchedulerState") -> ActionSet:
        """Pure function: given full state, return action set for this tick."""

    def name(self) -> str:
        return self.__class__.__name__


# Concrete classes:
#   - LegacyEightFactorPolicy  (wraps existing services.scheduler logic)
#   - HeftPolicy               (literature baseline)
#   - FcfsFirstFitPolicy       (sanity)
#   - AdaptivePolicy           (★ thesis algorithm — Part IX)
```

`SchedulerState` is a typed bag of references (cluster, queue, profile_store, data_placement, dag_metrics, now). It is read-only as far as the policy is concerned — mutation is done by the engine when the policy returns its action set.

### 5.2 `services/data_placement.py` — `DataPlacement` registry

Implements [ProblemSpec §4.10](ProblemSpecification.md). One file, ~120 lines.

```python
# services/data_placement.py
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Optional
import time


@dataclass
class DataLocation:
    node_id: str
    size_bytes: int
    written_at: float
    replicas: Set[str] = field(default_factory=set)


class DataPlacement:
    """Tracks which node holds each (workflow_id, task_id, field_name)."""

    def __init__(self, ttl_seconds: float = 3600.0):
        self._records: Dict[Tuple[str, str, str], DataLocation] = {}
        self._ttl = ttl_seconds

    def record_output(self, wfid: str, task_id: str, field_name: str,
                      node_id: str, size_bytes: int):
        self._records[(wfid, task_id, field_name)] = DataLocation(
            node_id=node_id, size_bytes=size_bytes, written_at=time.time(),
        )

    def add_replica(self, wfid: str, task_id: str, field_name: str, node_id: str):
        rec = self._records.get((wfid, task_id, field_name))
        if rec:
            rec.replicas.add(node_id)

    def get_producer(self, wfid: str, task_id: str, field_name: str) -> Optional[DataLocation]:
        return self._records.get((wfid, task_id, field_name))

    def is_resident_on(self, wfid: str, task_id: str, field_name: str, node_id: str) -> bool:
        rec = self._records.get((wfid, task_id, field_name))
        if rec is None:
            return False
        return rec.node_id == node_id or node_id in rec.replicas

    def gc_workflow(self, wfid: str):
        """Remove all entries for a finished/failed workflow."""
        self._records = {k: v for k, v in self._records.items() if k[0] != wfid}

    def gc_expired(self, now: Optional[float] = None):
        now = now or time.time()
        self._records = {
            k: v for k, v in self._records.items()
            if (now - v.written_at) < self._ttl
        }
```

Wired into the engine: `Observer.record_task_completion(...)` calls `DataPlacement.record_output` for each declared output; `WorkflowManager` calls `gc_workflow` when a workflow becomes terminal.

### 5.3 `services/dag_metrics.py` — upward rank & critical path

Computed once on workflow admission, cached on the `WorkflowInstance`. Used by `AdaptivePolicy` for inner-loop task ordering.

```python
# services/dag_metrics.py
from typing import Dict
from models.workload import WorkflowInstance, WorkflowTemplate
from models.profile_store import ProfileStore


def compute_upward_ranks(wf: WorkflowInstance, tmpl: WorkflowTemplate,
                         store: ProfileStore) -> Dict[str, float]:
    """
    Bottom-up DFS. upward_rank(v) = w_v + max_{c ∈ children(v)} (c_data + upward_rank(c)).
    w_v = mean predicted runtime across compatible nodes (or template default if cold).
    c_data = predicted transfer cost from v to c, averaged across pairs.
    Returns dict: task_template_id -> upward_rank.
    """
    # implementation: build adjacency, topo-reverse, accumulate
    ...
```

Stored as `WorkflowInstance.upward_rank_max` and per-task `TaskInstance.upward_rank` after admission.

### 5.4 `services/ect.py` — Expected Completion Time calculator

This is the heart of [ProblemSpec §8.5](ProblemSpecification.md). Pure functions only — no state.

```python
# services/ect.py
from typing import Dict, List, Tuple
from models.cluster import Node, ClusterScenario
from models.workload import TaskInstance, TaskTemplate, WorkflowInstance, WorkflowTemplate
from services.data_placement import DataPlacement
from models.profile_store import ProfileStore


def task_finish_time(task: TaskInstance, tmpl: TaskTemplate, node: Node,
                     wf: WorkflowInstance, wf_tmpl: WorkflowTemplate,
                     store: ProfileStore, dp: DataPlacement,
                     bw_matrix: Dict[Tuple[str, str], float],
                     now: float, occupancy: Dict[str, float]) -> float:
    """
    Predicted wall-clock time at which task finishes if placed on node now.
    occupancy: per-node "earliest free at" times the planner has accumulated.
    """
    t_ready = max(now, occupancy.get(node.node_id, now))
    startup = store.get_expected_startup(task.task_template_id, node.node_id, default=2.0)
    transfer = _transfer_seconds(task, tmpl, node, wf, wf_tmpl, dp, bw_matrix, store)
    runtime = store.get_expected_runtime_bucketed(
        task.task_template_id, node.node_id,
        cpu_at_start=node.cpu_usage_ratio,
        memory_at_start=node.memory_usage_ratio,
        thermal_headroom=node.thermal_headroom,
    ) or _cold_start_default(tmpl, node)
    return t_ready + startup + transfer + runtime


def _transfer_seconds(...) -> float:
    """Sum over DATA-edge parents; zero if input already resident on node."""
    ...


def workflow_ect(wf: WorkflowInstance, plan: "Plan", ...) -> float:
    """Critical-path through DAG with per-task finish times under plan."""
    ...
```

### 5.5 `services/preemption.py` — `PreemptionPlanner`

Encapsulates [ProblemSpec §10](ProblemSpecification.md). Two responsibilities:

1. **Victim selection** (priority preemption, makespan preemption) — given a queued task and the current state, return either `None` or a list of `PreemptAction`s.
2. **Budget enforcement** — per-node K=3/min and per-task M=2 caps; min-runtime threshold; near-completion immunity.

```python
class PreemptionPlanner:
    def __init__(self, max_per_node_per_min=3, max_per_task=2,
                 near_completion_threshold_s=5.0, min_runtime_threshold_s=5.0):
        ...
        self._recent_preemptions: Dict[str, List[float]] = {}  # node_id -> [timestamps]

    def find_victims(self, queued: TaskEntry, state: SchedulerState) -> Optional[List[PreemptAction]]: ...
    def can_preempt_node(self, node_id: str, now: float) -> bool: ...
    def record_preemption(self, node_id: str, task_id: str, now: float): ...
```

### 5.6 `services/defrag.py` — `DefragPlanner`

Encapsulates [ProblemSpec §10.6](ProblemSpecification.md). Two operations:

1. `reactive_pass(state)` — called at the end of `Policy.decide` if any task in queue is still HOLD'd despite aggregate-sufficient capacity. Tries to find a migration plan that frees a slot.
2. `proactive_pass(state)` — called from the heavy/async path every `T_defrag` seconds. Looks for packing improvements with `ΔJ > η`.

Plus EASY backfill helpers (reservation tokens carried in state).

### 5.7 `services/thermal.py` — scheduler-side thermal collector

Pulls per-node temperature from a metrics endpoint exposed by the thermal-collector DaemonSet (or directly from `node-exporter` if present). Updates `Node.cpu_temperature` on every `poll_cluster_state` call.

```python
# services/thermal.py
class ThermalCollector:
    def __init__(self, prometheus_url: Optional[str] = None,
                 daemonset_endpoint: Optional[str] = None):
        ...

    def get_temperatures(self, node_ids: List[str]) -> Dict[str, Optional[float]]:
        """Returns {node_id: temp_celsius or None}."""
```

If neither source is reachable, returns `None` for every node and the scheduler proceeds in "cooling-class only" mode (per [ProblemSpec §2.6](ProblemSpecification.md)).

### 5.8 `services/bandwidth.py` — scheduler-side bandwidth collector

Polls the bandwidth-probe DaemonSet's exported metrics; aggregates into `ClusterScenario.bandwidth_matrix`. Falls back to a uniform default (e.g., 100 MB/s) on first boot before any probe completes.

### 5.9 `tools/dax_importer.py` — Pegasus DAX → `WorkflowTemplate`

Small XML reader that maps Pegasus DAX abstract task names to our `TaskClass` via a heuristic table:

```python
DAX_TASK_CLASS = {
    "mProject":    TaskClass.IO_BOUND,
    "mDiffFit":    TaskClass.CPU_BOUND,
    "mBackground": TaskClass.MEMORY_BOUND,
    # ...
}
```

Outputs a `WorkflowTemplate` that the system can run unchanged.

### 5.10 `tools/trace_driver.py` — open-arrival driver

Reads a trace file (CSV or JSON: `{timestamp, workflow_template_id, priority}`) and submits workflows at the right wall-clock instant. Used for [ProblemSpec §5.1](ProblemSpecification.md) open-arrival mode evaluation.

### 5.11 K8s YAML manifests (new)

- `k8s/fileserver-daemonset.yaml`
- `k8s/bw-probe-daemonset.yaml`
- `k8s/thermal-collector-daemonset.yaml`

Each shipped with its own minimal Dockerfile under `images/`.

---

## Part VI — Data Plane: Producer-Local Storage

This part is the concrete plan for replacing the current shared-volume model with the producer-local + fileserver model from [ProblemSpec §2.4](ProblemSpecification.md).

### 6.1 Filesystem layout (per node)

Each worker node owns a single host directory `/var/lib/ts-data/` (or `/tmp/ts-data-<nodename>` on kind for dev). Inside:

```
/var/lib/ts-data/
└── <workflow_instance_id>/
    └── <producer_task_instance_id>/
        ├── field_X.bin
        ├── field_Y.bin
        └── _meta.json        # {"size_bytes": {...}, "written_at": ...}
```

Only the **producer** node ever writes here. Consumer nodes either:
- mount this directory read-only (Channel C, same node), or
- fetch over HTTP from `ts-fileserver` (Channel B, different node).

### 6.2 Fileserver DaemonSet

One pod per node, runs a tiny static-file HTTP server (e.g., `nginx` with a minimal config, or a 50-line Python `http.server` derivative) bound to a known **NodePort** or via a per-node Service.

**Sketch (`k8s/fileserver-daemonset.yaml`):**
```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ts-fileserver
spec:
  selector: { matchLabels: { app: ts-fileserver } }
  template:
    metadata: { labels: { app: ts-fileserver } }
    spec:
      hostNetwork: true                  # easy reach via <node-ip>:8081
      containers:
        - name: srv
          image: ts-fileserver:v1
          ports: [{ containerPort: 8081, hostPort: 8081 }]
          volumeMounts:
            - { name: data, mountPath: /srv/data, readOnly: true }
      volumes:
        - name: data
          hostPath: { path: /var/lib/ts-data, type: DirectoryOrCreate }
```

Discovery: the scheduler resolves a producer node's HTTP base URL as `http://<node-internal-ip>:8081/` (no DNS dance). Node IPs come from the `Node.status.addresses` list (already accessible via `services/k8s_cluster.py`).

### 6.3 Pod spec for a child task with one or more remote inputs

When `Policy.decide` returns a `PlaceAction(task=τ_c, node=n_c)`, the **K8sBinder** (Part IX.5) inspects DATA-edge parents and produces a pod spec like:

```yaml
spec:
  schedulerName: ts-scheduler
  nodeName: n_c                         # hard-pin (we already decided)
  initContainers:
    - name: fetch-from-n-p
      image: curlimages/curl:8.5.0
      command: ["sh", "-c"]
      args:
        - |
          set -e
          mkdir -p /data/local
          curl -sf http://<n_p_ip>:8081/<wfid>/<task_p_id>/field_X.bin \
            -o /data/local/field_X.bin
          curl -sf http://<n_p_ip>:8081/<wfid>/<task_p_id>/field_Y.bin \
            -o /data/local/field_Y.bin
      volumeMounts:
        - { name: local, mountPath: /data/local }
  containers:
    - name: worker
      image: ts-task-cpu:v1
      env:
        - { name: TS_INPUTS_DIR, value: /data/local }
        - { name: TS_OUTPUTS_DIR, value: /var/lib/ts-data/<wfid>/<task_c_id> }
        - { name: TS_WORKFLOW_ID, value: <wfid> }
        - { name: TS_TASK_INSTANCE_ID, value: <task_c_id> }
      volumeMounts:
        - { name: local, mountPath: /data/local }
        - { name: producer-out, mountPath: /var/lib/ts-data }    # for writing this task's outputs
  volumes:
    - { name: local, emptyDir: {} }
    - { name: producer-out, hostPath: { path: /var/lib/ts-data, type: DirectoryOrCreate } }
```

If a parent's output is **same-node** (Channel C), the binder skips the `curl` line for that field and instead adds a read-only mount of `/var/lib/ts-data/<wfid>/<task_p_id>/...` straight into `/data/local/`.

### 6.4 kind-cluster.yaml change

Replace the single shared `extraMounts` entry with per-node host dirs:

```yaml
# was
extraMounts:
  - hostPath: /tmp/ts-shared-data
    containerPath: /ts-data

# becomes (per node, with N1, N2, … unique)
extraMounts:
  - hostPath: /tmp/ts-data-cpu-1     # different per kind-node
    containerPath: /var/lib/ts-data
```

This decouples the kind-nodes' storage so transfer between them can be modelled. (On a real heterogeneous cluster, `/var/lib/ts-data` is just the local disk of each machine and there's no kind-shenanigans to undo.)

### 6.5 Task-side change

`tasks/task_*/task_*.py` drop the `SHARED_DIR = "/data/shared"` helpers and use:

```python
INPUTS_DIR = os.environ["TS_INPUTS_DIR"]      # /data/local
OUTPUTS_DIR = os.environ["TS_OUTPUTS_DIR"]    # /var/lib/ts-data/<wfid>/<my_id>
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# read inputs:
with open(os.path.join(INPUTS_DIR, "field_X.bin"), "rb") as f: ...

# write outputs (bulk):
with open(os.path.join(OUTPUTS_DIR, "result.bin"), "wb") as f: ...

# emit metadata via Channel A (still useful for control-plane state):
print(f"__TS_OUTPUT__={json.dumps({'result_bytes': size_written})}")
```

This is a small, mechanical change to the three task scripts.

### 6.6 GC

`Observer.on_workflow_terminal(wf)` calls:
- `DataPlacement.gc_workflow(wf.id)` (in-memory registry),
- and emits a job (Kubernetes `Job` or `kubectl exec`) on each node that ran a task in `wf` to `rm -rf /var/lib/ts-data/<wfid>/`.

Failed workflows GC the same way (we don't keep partial outputs). A periodic janitor sweeps anything older than the TTL (default 1 h) regardless.

---

## Part VII — Bandwidth Probe Subsystem

Implements [ProblemSpec §2.5](ProblemSpecification.md).

### 7.1 BW-probe DaemonSet

One pod per node. Every `T_bw = 600 s`, it iterates over the *other* nodes and `curl`s a fixed-size 100 MB blob from each one's fileserver, recording bytes/sec. Results are written to a pod log (or pushed to a Prometheus pushgateway / a small in-cluster Redis — see Part XVIII Q1).

**Probe blob**: a constant 100 MB file shipped baked into the fileserver image at `/srv/data/_probe.bin`. (Not under any `<wfid>/` namespace; the fileserver special-cases the path.)

**Rate limiting**: each probe-pod sleeps a randomized 30–90 s between targets so the cluster never sees a synchronised burst.

### 7.2 BandwidthCollector (scheduler side)

`services/bandwidth.py`. Reads the probe results and builds a `Dict[Tuple[str, str], float]` keyed by `(producer_node_id, consumer_node_id)`. Update is atomic: build a new dict, then assign to `cluster.bandwidth_matrix` under a lock.

On scheduler boot, the matrix is initialised to a uniform default (`100 MB/s` for now; configurable). The first probe replaces it.

### 7.3 Online refinement from real transfers

When an `initContainer` finishes, it logs its wall-clock duration and the bytes fetched. The scheduler observes this in the same "completion" path and pushes a refinement sample into the matrix entry (EMA with a small weight, e.g., 0.1):

```python
new_estimate = bytes / duration_s
matrix[(p, c)] = 0.9 * matrix[(p, c)] + 0.1 * new_estimate
```

This way the matrix tracks both the steady-state probe and the *actual* observed transfers. Probe-only would be staler; observation-only would be biased toward whatever transfers happened to occur. Both together is the right blend.

---

## Part VIII — Thermal Subsystem

Implements [ProblemSpec §2.6](ProblemSpecification.md).

### 8.1 Per-node temperature collection

Two implementations, depending on the node OS:

| OS | Source | Pod |
|---|---|---|
| Linux | `node-exporter`'s `node_hwmon_temp_celsius` metric (auto-derived from `/sys/class/thermal`). | Reuse the existing `node-exporter` DaemonSet if present; otherwise ship `prometheus/node-exporter:latest` ourselves. |
| macOS (dev only) | Custom DaemonSet calling `osx-cpu-temp` (or `powermetrics`). | `images/thermal-mac/`. |
| Unknown | None — return `cpu_temperature = None`. | n/a |

The scheduler doesn't care about the source; it only calls `ThermalCollector.get_temperatures()` and slots whatever it gets onto `Node.cpu_temperature`.

### 8.2 Cooling-class declaration (static)

Each node carries two new labels:

```yaml
metadata:
  labels:
    node-type: CPU_OPT
    ts.cooling-class: STANDARD
    ts.thermal-throttle-temp-c: "100"
```

`services/k8s_cluster.py` reads them and sets `Node.cooling_class` and `Node.thermal_throttle_temp_c`. Default if absent: `STANDARD` and the vendor table from [ProblemSpec §4.1](ProblemSpecification.md).

### 8.3 `thermal_headroom` is derived

It's just `thermal_throttle_temp_c - cpu_temperature`. Computed lazily on `Node` (a `@property`).

### 8.4 Wiring into ProfileStore

`Observer.record_task_completion` now also stamps `temperature_at_start` and `temperature_at_end` (read off the `Node` object at submit time and again at completion). The new bucketed `record_observation` reads `temperature_at_start` and stores it in the contextual bucket key (Part X).

---

## Part IX — Scheduler Core: Two-Tier Architecture

This part replaces the per-task greedy loop in [engine.py](engine.py) with the two-tier architecture from [ProblemSpec §5.8 & §6](ProblemSpecification.md).

### 9.1 SchedulerState — read-only aggregate

```python
@dataclass(frozen=True)
class SchedulerState:
    cluster: ClusterScenario
    queue: QueueManager
    profile_store: ProfileStore
    data_placement: DataPlacement
    bw_matrix: Dict[Tuple[str, str], float]
    workflow_templates: Dict[str, WorkflowTemplate]
    now: float
```

Passed by reference into `Policy.decide`. The policy does not mutate it.

### 9.2 Policy implementations

**Required, all in `services/policy/`** (or `services/policy.py` as one file if small):

1. `LegacyEightFactorPolicy` — wraps the existing `PlacementAlgorithm` so the legacy behaviour is reproducible as a baseline.
2. `FcfsFirstFitPolicy` — simplest baseline.
3. `RandomPolicy` — sanity floor.
4. `HeftPolicy` — published HEFT baseline (closed batch only).
5. `AdaptivePolicy` — the thesis algorithm. Uses upward rank for inner-loop ordering, ECT scoring with the full transfer/thermal/bucket model, and the preemption/defrag planners.

The choice is made via a CLI flag (`--policy adaptive`) or env var (`TS_POLICY=adaptive`), defaulting to `LegacyEightFactorPolicy` until validated.

### 9.3 The new `run_tick`

```python
def run_tick(self, cluster: ClusterScenario) -> ActionSet:
    # 1. Admission
    while self.queue.workflow_queue:
        wf = self.queue.admit_next_workflow()
        compute_upward_ranks(wf, self.templates[wf.workflow_template_id], self.profile_store)

    # 2. DAG resolution (existing logic; extended for gangs)
    for wf in list(self.queue.admitted_workflows.values()):
        ready = self.resolver.get_ready_tasks(wf, self.templates[wf.workflow_template_id])
        ready = self.gang_resolver.filter_atomic(ready, wf)   # new: H8
        if ready:
            self.queue.enqueue_ready_tasks(ready, wf)

    # 3. Build state
    state = SchedulerState(
        cluster=cluster, queue=self.queue,
        profile_store=self.profile_store,
        data_placement=self.data_placement,
        bw_matrix=cluster.bandwidth_matrix,
        workflow_templates=self.templates,
        now=time.time(),
    )

    # 4. Delegate to policy
    actions = self.policy.decide(state)

    # 5. Apply (mutates cluster + queue; emits k8s bindings)
    self._apply(actions, cluster)
    return actions
```

### 9.4 Fairness accounting

After every tick, increment each running workflow's `vruntime` by `(elapsed_seconds × Σ cpu_request_of_running_tasks_normalised_by_node_speed)`. Used by `AdaptivePolicy` in the outer loop to pick the next workflow to advance ([ProblemSpec Q8](ProblemSpecification.md)).

### 9.5 K8sBinder

Replaces the existing `bind_pod` step. Given `PlaceAction(task, node)` it:

1. Inspects DATA-edge parents.
2. Looks each up in `DataPlacement` to find the producer node.
3. Decides which fields require an `initContainer` (parent on different node) vs. a read-only mount (same node).
4. Patches the pod spec accordingly (Part VI.3).
5. POSTs the binding (or creates the pod with `nodeName` pre-set in the simulation/manual path).

This is the only place pod-spec mutation happens; everything else just produces structured `PlaceAction`s.

---

## Part X — Learning Subsystem (ProfileStore v2)

Refactors [models/profile.py](models/profile.py) and [models/profile_store.py](models/profile_store.py) to match [ProblemSpec §11](ProblemSpecification.md).

### 10.1 `Observation` v2

Add fields:
```python
@dataclass
class Observation:
    runtime: float
    startup: float
    cpu_at_start: float = 0.0
    memory_at_start: float = 0.0
    io_bytes_read: int = 0           # NEW
    io_bytes_written: int = 0        # NEW
    output_bytes_by_field: Dict[str, int] = field(default_factory=dict)  # NEW
    temperature_at_start: Optional[float] = None   # NEW
    temperature_at_end: Optional[float] = None     # NEW
    transfer_seconds: float = 0.0    # NEW (initContainer cost paid for this run)
    timestamp: float = 0.0
```

### 10.2 Bucket key

```python
@dataclass(frozen=True)
class BucketKey:
    cpu: str            # "LOW" | "MEDIUM" | "HIGH"
    memory: str         # "LOW" | "HIGH"
    thermal: Optional[str]  # "LOW" | "MEDIUM" | "HIGH" | None
```

`NodeMetrics` becomes `Dict[BucketKey, RollingWindow]` instead of one window. Falls back to less-specific buckets when count < 3 (drop thermal first, then memory, then cpu — exactly per [ProblemSpec §11.3](ProblemSpecification.md)).

### 10.3 Variance, EMA, drift

```python
class RollingWindow:
    def __init__(self, max_size=20):
        self._obs: List[Observation] = []
        self._ema_runtime: Optional[float] = None
        self._ema_alpha = 0.3

    def add(self, o: Observation):
        self._obs.append(o)
        if len(self._obs) > 20: self._obs.pop(0)
        self._ema_runtime = (
            o.runtime if self._ema_runtime is None
            else self._ema_alpha * o.runtime + (1 - self._ema_alpha) * self._ema_runtime
        )

    @property
    def median_runtime(self): ...
    @property
    def stddev_runtime(self): ...
    @property
    def is_drifting(self): ...   # |EMA − median|/median > τ_drift
```

### 10.4 UCB scoring

`store.ucb_score(task_id, node_id, beta=1.0)` returns `μ - β·σ/√n`. Used by `AdaptivePolicy` to choose between exploration and exploitation in one principled rule.

### 10.5 Decay for `failures_by_node`

Currently monotonic. Switch to:

```python
def decay_failures(self, half_life_hours=14):
    factor = 0.5 ** (elapsed_h / half_life_hours)
    for node_id in self.failures_by_node:
        self.failures_by_node[node_id] *= factor
```

Called once per tick on the heavy/async path.

### 10.6 Persistence

JSON schema bumps to v2. `load_json` reads either; `to_json` always writes v2. Existing on-disk profiles from v1 are migrated lazily (each missing field defaulted).

---

## Part XI — Preemption & Defragmentation

Wired in via the planners from [Part V](#part-v--new-components-to-build) (Sections 5.5 and 5.6).

### 11.1 When `AdaptivePolicy.decide` invokes preemption

```
for each high-priority queued task τ:
    if no node fits τ:
        victims = preemption_planner.find_victims(τ, state)
        if victims is None:
            hold(τ)
        elif J_after(victims + place(τ)) > J_before:
            emit preempt(victims) + place(τ)
        else:
            hold(τ)
```

### 11.2 Kill+restart vs. checkpoint

Both modes return `PreemptAction(mode=...)` from the planner. The engine, on apply, either issues `delete pod` (kill+restart) or signals the pod (`SIGUSR1` to invoke a user-supplied checkpoint hook) — kill+restart is the only mode that ships in Phase 1; checkpoint is an opt-in path enabled by `TaskTemplate.checkpointable = True` and is wired in Phase 4.

### 11.3 Defragmentation

`DefragPlanner.reactive_pass(state)` is called after the main `Policy.decide` loop if any task is still HOLD. It looks for a small set of preempt+replace actions that frees a single node's contiguous capacity. Scored by `J`; committed only if `ΔJ > 0`.

`DefragPlanner.proactive_pass(state)` runs every `T_defrag = 30 s` from the heavy/async thread, with stricter `ΔJ > η = 0.05`.

EASY-style reservations are a `dict[node_id, ReservationToken]` field on `SchedulerState`; the policy honors them when scoring placements.

---

## Part XII — Pod Specs (Concrete YAML/Python)

This part is reference material — paste-ready snippets.

### 12.1 Minimal worker pod (no remote inputs)

```python
def build_worker_pod(action: PlaceAction, tmpl: TaskTemplate, wfid: str) -> client.V1Pod:
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=action.task_instance_id,
            annotations={
                "ts.scheduler/task_template_id": tmpl.task_template_id,
                "ts.scheduler/workflow_instance_id": wfid,
                "ts.scheduler/expected_runtime_s": str(action.expected_runtime),
            },
        ),
        spec=client.V1PodSpec(
            scheduler_name="ts-scheduler",
            node_name=action.node_id,           # hard-pin (post-decision)
            restart_policy="Never",
            containers=[client.V1Container(
                name="worker",
                image=tmpl.image_name,
                image_pull_policy="Never",
                env=[
                    client.V1EnvVar(name="TS_INPUTS_DIR", value="/data/local"),
                    client.V1EnvVar(name="TS_OUTPUTS_DIR",
                                    value=f"/var/lib/ts-data/{wfid}/{action.task_instance_id}"),
                    client.V1EnvVar(name="TS_WORKFLOW_ID", value=wfid),
                    client.V1EnvVar(name="TS_TASK_INSTANCE_ID", value=action.task_instance_id),
                ],
                resources=client.V1ResourceRequirements(
                    requests={"cpu": str(tmpl.cpu_request),
                              "memory": f"{int(tmpl.memory_request)}Mi"},
                    limits={"cpu": str(tmpl.cpu_request),
                            "memory": f"{int(tmpl.memory_request)}Mi"},
                ),
                volume_mounts=[
                    client.V1VolumeMount(name="local", mount_path="/data/local"),
                    client.V1VolumeMount(name="producer-out",
                                         mount_path="/var/lib/ts-data"),
                ],
            )],
            volumes=[
                client.V1Volume(name="local",
                                empty_dir=client.V1EmptyDirVolumeSource()),
                client.V1Volume(name="producer-out",
                                host_path=client.V1HostPathVolumeSource(
                                    path="/var/lib/ts-data",
                                    type="DirectoryOrCreate")),
            ],
        ),
    )
```

### 12.2 Worker pod with remote inputs (Channel B initContainer)

Add to `spec.init_containers`:

```python
init = client.V1Container(
    name="fetch-inputs",
    image="curlimages/curl:8.5.0",
    command=["sh", "-c"],
    args=[fetch_script],          # generated below
    volume_mounts=[client.V1VolumeMount(name="local", mount_path="/data/local")],
)

# fetch_script is a single shell command:
#   set -e
#   curl -sf http://10.0.0.7:8081/<wfid>/<task_p>/field_X.bin -o /data/local/field_X.bin
#   curl -sf http://10.0.0.9:8081/<wfid>/<task_q>/field_Y.bin -o /data/local/field_Y.bin
```

### 12.3 Same-node co-location (Channel C)

For each parent that ran on the same node, **skip the curl** and instead add a read-only sub-path mount of the producer's output directory:

```python
client.V1VolumeMount(
    name="producer-out",
    mount_path=f"/data/local/field_X.bin",
    sub_path=f"{wfid}/{task_p_id}/field_X.bin",
    read_only=True,
)
```

(Same `producer-out` volume as the writeable one — k8s allows multiple mounts of the same volume at different paths with different `read_only` flags.)

### 12.4 Fileserver Dockerfile

```dockerfile
# images/fileserver/Dockerfile
FROM nginx:1.27-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/ts-fileserver.conf
# 100 MB constant probe blob baked in
RUN dd if=/dev/urandom of=/srv/data/_probe.bin bs=1M count=100
EXPOSE 8081
```

`nginx.conf` is a 10-line config that serves `/srv/data/` on port 8081 read-only.

---

## Part XIII — Persistence Layer

### 13.1 What persists

| Data | Lifetime | Backing store | Format |
|---|---|---|---|
| `ProfileStore` | Days–weeks | k8s `ConfigMap` (`ts-scheduler-profiles`) — already implemented | JSON |
| `DataPlacement` | Workflow lifetime | In-memory only; rebuilt from running workflows on restart | n/a |
| `BandwidthMatrix` | Hours | In-memory + periodic snapshot to ConfigMap (`ts-scheduler-bandwidth`) | JSON |
| Workflow & task state | Run lifetime | In-memory; reconstructed from k8s pod state on scheduler restart | n/a |
| Per-template config (cooling, image refs) | Static | k8s Node labels + workload definitions | YAML |

### 13.2 Restart semantics

When the scheduler pod restarts:
1. Load `ts-scheduler-profiles` (already done).
2. Load `ts-scheduler-bandwidth` (new).
3. Run `poll_cluster_state()` to rebuild `ClusterScenario`.
4. Walk all pods labelled `schedulerName=ts-scheduler`; reconstruct in-flight `WorkflowInstance`/`TaskInstance` objects from annotations.
5. For each completed task whose annotations are still readable, replay `DataPlacement.record_output` so future children can find their inputs.
6. Resume normal tick loop.

This is the only acceptable "warm restart" story; full state durability (etcd CRDs) is out of scope.

---

## Part XIV — Observability & Logging

### 14.1 Log lines (structured)

Every action a policy emits is logged with a stable prefix:

```
[POLICY=Adaptive][PLACE]   τ=task-cpu-7 → node=ts-node-cpu-1  J_delta=+12.4  ECT=83.2  transfer=2.1
[POLICY=Adaptive][PREEMPT] victim=task-mem-3 (priority=BATCH)  for=task-cpu-7 (priority=HIGH)  ΔJ=+8.1
[POLICY=Adaptive][HOLD]    τ=task-cpu-7  reason=NO_FEASIBLE_NODE  retry_at=tick+1
[POLICY=Adaptive][DEFRAG]  reactive  freed=ts-node-cpu-2  cost=4.2  benefit=15.0
[POLICY=Adaptive][LEARN]   τ=task-cpu  bucket=(LOW,LOW,HIGH)  median=3.4s  stddev=0.21  drift=False
```

### 14.2 Metrics (Prometheus-style)

If a Prometheus operator is running, the scheduler exposes:

- `ts_scheduler_decide_seconds` (histogram, per-tick wall-clock)
- `ts_scheduler_actions_total{type=place|preempt|hold}` (counter)
- `ts_scheduler_queue_depth` (gauge)
- `ts_scheduler_workflow_ect_seconds{priority=...}` (histogram)
- `ts_scheduler_transfer_seconds` (histogram)

Optional; a stretch goal for the defense.

### 14.3 Run summary file

After each simulation run, `run_simulation.py` writes `results/run-<timestamp>/summary.json` with:

```json
{
  "policy": "AdaptivePolicy",
  "scenario": "Hetero-asymmetric-10",
  "workload": "Montage-50",
  "mode": "closed_batch",
  "metrics": {
    "makespan_s": 412.7,
    "mean_completion_s": 71.3,
    "p99_completion_s": 187.2,
    "utilization": 0.74,
    "fairness_index": 0.91,
    "preemptions": 14,
    "evictions": 0,
    "decision_p99_ms": 38.0
  }
}
```

These feed the comparison tables in the thesis.

---

## Part XV — Test Harness

### 15.1 Unit tests ([test_simulation.py](test_simulation.py))

Add per-component tests:

| Component | Tests |
|---|---|
| `DataPlacement` | record / get / replicas / gc_workflow / gc_expired |
| `dag_metrics` | upward_rank on linear, fan-out, fan-in, diamond |
| `ect.task_finish_time` | with/without transfer, same-node, cold-start fallback |
| `PreemptionPlanner` | budget caps, near-completion immunity, victim ordering |
| `DefragPlanner` | reactive solves a wedged 4-CPU task; proactive ΔJ > η |
| `ProfileStore.ucb_score` | exploration → exploitation transition |
| Bucket fallback | drop thermal / memory / cpu order |
| Drift detection | EMA-vs-median trip |

### 15.2 Integration tests (simulation)

In-process `SchedulerEngine` driven by `run_simulation.py`:

| Test | Scenario | Expected outcome |
|---|---|---|
| Single linear DAG | Hetero-balanced-6 | All 3 tasks finish; learned profile populated. |
| Fan-out 1×8 | Hetero-balanced-6 | At least 4 of 8 placed concurrently in tick 1. |
| Fan-in 8×1 | Hetero-balanced-6 | Final task starts only after all 8 parents finish. |
| Mixed priority | Hetero-asymmetric-10 | Higher-priority workflow's median completion < lower's, AND lower's still finishes (no starvation). |
| Defrag wedge | Hetero-asymmetric-10 | A 4-CPU task placed despite scattered free capacity. |
| Gang | Synthetic 4-task gang | All 4 placed at the same tick or none. |
| Pegasus Montage-50 | Hetero-asymmetric-10 | Adaptive beats Legacy by ≥ 10% makespan (smoke). |

### 15.3 End-to-end on kind

`./setup_cluster.sh && python submit_workflows.py 5 --parallel` — must finish without fail and show varied node placements. Ground truth for "the scheduler is alive on real k8s".

---

## Part XVI — Migration Plan (Phased Rollout)

Each phase is a coherent, reviewable PR set. Earlier phases are prerequisites for later ones.

### Phase 0 — Foundations (no behaviour change)

1. Add new fields to models per Part II.1 (cooling fields, vruntime, upward_rank, gang_group_id, expected_output_bytes). Default values keep current callers compiling.
2. Introduce `services/policy.py` with `Policy` ABC and `LegacyEightFactorPolicy` wrapping current code; engine selects a policy via env var; default = legacy. **No measurable change.**
3. Fix `ProfileStore` bugs: `failures_by_node` decay; `exploration_level` redefined; variance and EMA tracked. **Slightly different exploration cadence; verify on simulation.**

### Phase 1 — Producer-local data plane (Channel B + C)

1. New `ProducerLocalDataManager` replaces `SharedVolumeDataManager`.
2. Modify `kind-cluster.yaml` to per-node host paths.
3. Add `ts-fileserver` DaemonSet + image.
4. Modify `K8sBinder` to build pod specs with initContainers / read-only mounts as appropriate.
5. Modify the three sample task scripts to read/write `/data/local` and `/var/lib/ts-data/.../`.
6. New `DataPlacement` registry; wire into Observer + GC.
7. Smoke test: existing 3-task workflow runs end-to-end on kind.

### Phase 2 — Bandwidth probe + ECT

1. Add `ts-bw-probe` DaemonSet + image.
2. New `BandwidthCollector`; populate `cluster.bandwidth_matrix`.
3. New `services/ect.py` with `task_finish_time` and `workflow_ect`.
4. Replace the legacy `W_DATA_LOCALITY` term with a transfer-cost-in-ECT term inside `LegacyEightFactorPolicy` for parity.

### Phase 3 — `AdaptivePolicy` (the thesis algorithm)

1. New `services/dag_metrics.py` for upward rank.
2. New `AdaptivePolicy` using ECT + UCB + bucketed predictions.
3. CLI flag `--policy adaptive`; A/B against legacy on simulation.

### Phase 4 — Preemption & Defrag

1. `PreemptionPlanner` (kill+restart only).
2. `DefragPlanner` reactive + proactive.
3. Gang readiness in `ReadinessResolver`; H8 enforced in policy.
4. EASY backfill reservation tokens.

### Phase 5 — Thermal + Drift

1. Thermal collector DaemonSet + `ThermalCollector`.
2. Bucketed predictions get the optional thermal dimension.
3. Drift detection + forced exploration on flagged (task, node) pairs.

### Phase 6 — Evaluation

1. DAX importer + Pegasus benchmark workflows.
2. Trace driver for Poisson arrivals.
3. Metrics export + run-summary plumbing.
4. Run the full evaluation matrix from [ProblemSpec §14](ProblemSpecification.md).

### Phase 7 — Stretch (only if time permits)

1. Speculative execution.
2. Checkpoint+resume preemption mode.
3. Closed-loop thermal forecast.

---

## Part XVII — File-by-File Change Map

A quick lookup of every file touched, indexed by phase. **`+`** = new file, **`*`** = modified, **`!`** = breaking change (e.g., remove the shared volume model).

### Phase 0
- `*` [models/cluster.py](models/cluster.py) — add `cooling_class`, `thermal_throttle_temp_c`, `cpu_temperature` (Optional), `thermal_headroom` (derived), `bandwidth_matrix` on `ClusterScenario`.
- `*` [models/workload.py](models/workload.py) — add `checkpointable`, `checkpoint_interval_s`, `gang_group_id`, `expected_output_bytes`, `last_checkpoint_at`, `preemption_count`, `upward_rank`, `vruntime`, `upward_rank_max`.
- `*` [models/enums.py](models/enums.py) — add `CoolingClass`.
- `*` [models/profile.py](models/profile.py) — add new `Observation` fields; restructure `NodeMetrics` for buckets; fix `exploration_level`.
- `*` [models/profile_store.py](models/profile_store.py) — bucketed `record_observation`, EMA, decay, variance, UCB score.
- `+` `services/policy.py` — `Policy` ABC + `LegacyEightFactorPolicy` + others scaffolded.
- `*` [services/scheduler.py](services/scheduler.py) — refactor `PlacementAlgorithm` to live behind `LegacyEightFactorPolicy`.
- `*` [engine.py](engine.py) — accept a `Policy`; preserve current behaviour by defaulting to legacy.

### Phase 1
- `!` [kind-cluster.yaml](kind-cluster.yaml) — per-node host paths.
- `!` [services/data_manager.py](services/data_manager.py) — replace `SharedVolumeDataManager` with `ProducerLocalDataManager`; keep the simulation `FileStoreDataManager` as-is.
- `+` `services/data_placement.py` — `DataPlacement` registry.
- `*` [services/observer.py](services/observer.py) — record output sizes, transfer seconds; call `DataPlacement.record_output`.
- `*` [k8s_scheduler.py](k8s_scheduler.py) — wire in new binder logic.
- `+` `services/binder.py` (extracted from `k8s_scheduler.bind_pod`) — handles initContainer / read-only mount construction.
- `+` `k8s/fileserver-daemonset.yaml`
- `+` `images/fileserver/{Dockerfile,nginx.conf}`
- `*` [tasks/task_io/task_io.py](tasks/task_io/task_io.py), [tasks/task_mem/task_mem.py](tasks/task_mem/task_mem.py), [tasks/task_cpu/task_cpu.py](tasks/task_cpu/task_cpu.py) — switch to `TS_INPUTS_DIR` / `TS_OUTPUTS_DIR`.
- `*` [submit_workflows.py](submit_workflows.py), [k8s_main.py](k8s_main.py) — drop the cluster-wide shared mount injection.
- `*` [setup_cluster.sh](setup_cluster.sh) — apply new DaemonSet.

### Phase 2
- `+` `services/bandwidth.py` — `BandwidthCollector`.
- `+` `services/ect.py` — ECT calculator.
- `+` `k8s/bw-probe-daemonset.yaml`
- `+` `images/bw-probe/{Dockerfile,probe.py}`
- `*` [services/k8s_cluster.py](services/k8s_cluster.py) — populate `bandwidth_matrix` after polling.

### Phase 3
- `+` `services/dag_metrics.py` — upward rank.
- `+` `services/policy/adaptive.py` (or `services/policy.py` continued) — `AdaptivePolicy`.

### Phase 4
- `+` `services/preemption.py` — `PreemptionPlanner`.
- `+` `services/defrag.py` — `DefragPlanner`.
- `+` `services/gang_resolver.py` — gang readiness; or extend [services/workflow_manager.py](services/workflow_manager.py).
- `*` `services/policy/adaptive.py` — call planners.

### Phase 5
- `+` `services/thermal.py` — `ThermalCollector`.
- `+` `k8s/thermal-collector-daemonset.yaml` (Linux uses `node-exporter`).
- `*` [services/k8s_cluster.py](services/k8s_cluster.py) — populate temperatures.
- `*` `models/profile.py`, `models/profile_store.py` — third (thermal) bucket dimension.

### Phase 6
- `+` `tools/dax_importer.py`
- `+` `tools/trace_driver.py`
- `+` `workflows/pegasus/*.json` (converted DAX outputs)
- `*` [run_simulation.py](run_simulation.py) — accept trace files.

### Phase 7 (stretch)
- `+` `services/speculation.py` — straggler doubling.
- `+` `services/checkpoint.py` — checkpoint/resume hook plumbing.

---

## Part XVIII — Open Implementation Questions

Tracked here so each gets resolved before its phase ships. Distinct from the *problem-level* open questions in [ProblemSpec §15](ProblemSpecification.md) — those concern the *what*; these concern the *how*.

| ID | Question | Phase | Resolution path |
|---|---|---|---|
| I1 | How does the BW-probe pod publish its results to the scheduler — pod logs, Prometheus pushgateway, an in-cluster Redis, or a `ConfigMap` it patches? | 2 | Default: pod logs scraped via the same k8s API the scheduler already uses. Cheap, no new dependency. Switch to Prometheus only if scrape latency hurts. |
| I2 | Should the fileserver speak HTTP or gRPC? | 1 | HTTP. Simpler, debuggable with `curl`, no proto generation. |
| I3 | Do we need authentication between fileserver and consumer pods? | 1 | No — single-tenant cluster (per [ProblemSpec §2.2](ProblemSpecification.md)). Can add a shared-secret bearer token later if scope expands. |
| I4 | When a child has *N* parents on *N* distinct nodes, do we issue *N* parallel `curl`s or *N* sequential ones inside the initContainer? | 1 | Parallel via `curl -Z` (libcurl's parallel mode) or a small `xargs -P` wrapper. Saves wall-clock. |
| I5 | Where does the per-node `cooling_class` label get baked in for the kind cluster (so dev experiments are reproducible)? | 5 | In [kind-cluster.yaml](kind-cluster.yaml) directly, alongside `node-type`. Real cluster: cluster-bootstrap script labels nodes from a YAML manifest. |
| I6 | What happens if the fileserver pod on a node is restarting at the moment a child needs to fetch from it? | 1 | InitContainer retries with exponential backoff (5 attempts, max 30 s). If still failing, the pod fails and the scheduler re-enqueues the child, possibly choosing a different node. |
| I7 | Do output files need a checksum so the consumer can detect a partial fetch? | 1 | Yes. The producer writes `<field>.bin.sha256` alongside; the initContainer verifies. Cheap, prevents subtle correctness bugs. |
| I8 | How is the gang-readiness check implemented atomically — at admission, at every tick, or once per gang? | 4 | Once per tick, in `ReadinessResolver`. A gang is "ready" only when every member's predecessors are FINISHED. Otherwise the whole gang stays WAITING. |
| I9 | Should the scheduler retry a failed transfer or fail the child task immediately? | 1 | Retry inside the initContainer (I6), then fail the child task. Don't conflate "node failed" with "transfer hiccup". |
| I10 | Do we need a way to opt out of producer-local for very small outputs (skip the fileserver dance)? | 1 | Threshold: if `expected_output_bytes < 16 KiB`, use Channel A only. Avoid building file-store records for trivially small outputs. |
| I11 | Where do `expected_output_bytes` defaults come from for Pegasus workflows? | 6 | DAX has `<uses link="output" size="..."/>` attributes; the importer reads them. For native `.json` workflows, declare in the file. |
| I12 | What's the on-wire format for trace files (open arrival)? | 6 | CSV: `timestamp_seconds_from_t0, workflow_template_id, priority`. Keep it boring. |
| I13 | What backoff applies between scheduler restarts when reconstructing in-flight state from k8s annotations? | 0 | None — reconstruction is a one-shot pass at boot, then watch resumes. |
| I14 | Is there any case where `K8sBinder` should refuse to bind (other than infeasibility)? | 0 | Yes: if the chosen node's `cordon` flag is set or the node has gone NotReady between policy decision and binding (rare; emit a HOLD and re-decide next tick). |

---

## Sign-off Checklist

This document is implementable as-is once the following are confirmed:

- [ ] Channel A stays as today (KB-scale metadata via `__TS_OUTPUT__` logs).
- [ ] Channel B is built per [Part VI](#part-vi--data-plane-producer-local-storage): per-node `hostPath`, `ts-fileserver` DaemonSet, initContainer fetch, checksum verification.
- [ ] Channel C is the same-node branch of B (read-only sub-path mount; no initContainer).
- [ ] The current shared-volume model in [services/data_manager.py](services/data_manager.py) and [kind-cluster.yaml](kind-cluster.yaml) is **replaced**, not preserved alongside.
- [ ] `Policy` interface is acceptable as the seam for swapping schedulers (legacy / FCFS / HEFT / Adaptive).
- [ ] The phased rollout (Part XVI) matches the time budget for the thesis.
- [ ] All new files & modified files in [Part XVII](#part-xvii--file-by-file-change-map) are agreed.
- [ ] Open questions in [Part XVIII](#part-xviii--open-implementation-questions) have owners.

Once signed off, **Phase 0 can begin**: model field additions + `Policy` interface + `ProfileStore` bug fixes — no behaviour change but unblocks every subsequent phase.
