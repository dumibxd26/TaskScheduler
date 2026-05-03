# Problem Specification — Adaptive Workflow Scheduler

> **Status:** Draft v1 · May 2026
> **Audience:** Thesis defense, future contributors, and the algorithm-design document that follows this one.
> **Goal of this document:** Define the problem completely — every entity, every constraint, every input, every output, every objective, every non-goal, every open question — *before* committing to an algorithm. Algorithm choice is deliberately deferred to a separate document.

---

## Table of Contents

- [Part I — Purpose](#part-i--purpose)
- [Part II — Domain & Deployment Context](#part-ii--domain--deployment-context)
- [Part III — Why Existing Schedulers Are Insufficient](#part-iii--why-existing-schedulers-are-insufficient)
- [Part IV — Entities & Data Model](#part-iv--entities--data-model)
- [Part V — Workload Regime](#part-v--workload-regime)
- [Part VI — The Scheduler Interface](#part-vi--the-scheduler-interface)
- [Part VII — Constraints](#part-vii--constraints)
- [Part VIII — Objectives](#part-viii--objectives)
- [Part IX — Failure Model](#part-ix--failure-model)
- [Part X — Preemption Model](#part-x--preemption-model)
- [Part XI — Learning Model](#part-xi--learning-model)
- [Part XII — Decision-Time Budgets](#part-xii--decision-time-budgets)
- [Part XIII — Out of Scope / Non-Goals](#part-xiii--out-of-scope--non-goals)
- [Part XIV — Evaluation Criteria](#part-xiv--evaluation-criteria)
- [Part XV — Open Questions & Blockers](#part-xv--open-questions--blockers)
- [Part XVI — Glossary & Notation](#part-xvi--glossary--notation)

---

## Part I — Purpose

### 1.1 What this system is

A **DAG-aware, learning-driven, preemption-capable workflow scheduler** for a fixed, dedicated, heterogeneous cluster. It accepts user-submitted workflows (DAGs of containerized tasks) and decides:

1. **When** to start each task.
2. **Where** to place it (which node).
3. **Whether** to preempt a currently-running task to make room.
4. **Whether** to defer a task because waiting is better than placing.

…such that the cluster runs as much useful work as possible, in the order users intended, learning from every execution to make better decisions next time.

### 1.2 What we want to obtain

Three things, in priority order:

1. **High effective throughput.** Given a stream of arriving workflows, the cluster should sustain a high arrival rate without queue blow-up. Equivalently, given a closed batch, it should finish in minimum wall-clock time.
2. **Low workflow makespan.** For each individual workflow, time-from-submit to time-of-last-task-finish should be small relative to a theoretical lower bound.
3. **Inter-workflow fairness.** When multiple workflows from the same user are in flight, none should starve disproportionately to the others (within the same priority class).

### 1.3 What "success" looks like

The scheduler is a thesis success if:

- It **statistically beats** baselines (random, FCFS, our current 8-factor scorer, and at least one named literature baseline) on the metrics in §1.2 across the workload matrix in [Part XIV](#part-xiv--evaluation-criteria).
- Its decisions are **explainable** — each placement / preemption can be traced to specific factors in the objective function.
- It **converges** on heterogeneous clusters: after a bounded number of executions per task template, placement quality is within a small factor of an oracle that knows true runtimes.
- It **degrades gracefully** under cold-start, failures, and resource pressure.
- It is **architecturally open** to later extensions (deadlines, cost, multi-tenant) without rewrites.

---

## Part II — Domain & Deployment Context

### 2.1 The "home cluster" framing

Our deployment target is a **dedicated cluster of physical machines on a private network**, plausibly assembled from heterogeneous hardware (a workstation, a server, a NAS, a small ARM box). Examples:

- Two PCs and a small server in a home lab.
- A research group's collection of donated machines on one VLAN.
- A small department's compute pool of mixed-generation hardware.

This framing matters because it sets the **realistic ranges** of every dimension below:

| Dimension | Realistic range | Implication |
|---|---|---|
| Node count $\lvert N \rvert$ | 2 – 30 | Small enough that per-tick optimization is cheap; large enough that smart placement matters |
| Heterogeneity ratio (fastest CPU / slowest CPU) | 2× – 30× | Real heterogeneity; learning has genuine signal |
| Memory range | 2 GB – 128 GB per node | Some nodes simply cannot run memory-heavy tasks |
| Network | Single LAN (~1 Gbps) | Network locality matters less than disk/CPU locality |
| Topology | Flat | No racks, zones, or cross-AZ concerns |
| Stability | Dedicated | Nodes are always up; nodes are not preempted by external systems |
| Cost model | Free / sunk | No cloud bills; no spot-vs-reserved trade-offs |

### 2.2 What the home framing **does not** mean

To prevent scope creep, the cluster is explicitly **not**:

- **Opportunistic.** Nodes are not laptops that close their lid. Nodes don't go offline because someone is gaming. We assume nodes are always available unless they fail.
- **Mobile.** Nodes don't move between zones / networks.
- **Volatile.** Membership is fixed at cluster boot. Node addition / removal is not a runtime event.
- **Owned by multiple users.** Single-user (one person submits all workflows). Multi-tenancy is out of scope.

If that ever changes, the scheduler will need significant rework — but this document scopes to dedicated, stable, single-user clusters.

### 2.3 Hardware diversity profile (assumed)

A typical target cluster has nodes that fall into recognizable **specializations**:

- **CPU-optimized**: high core count, modest RAM, modest disk (a workstation).
- **Memory-optimized**: average CPU, lots of RAM (a desktop with extra RAM).
- **I/O-optimized**: average CPU and RAM, fast NVMe (a NAS-like box).
- **General-purpose**: balanced, no specialization.

These are encoded as `NodeType ∈ {CPU_OPT, MEM_OPT, IO_OPT, GENERAL}` (see [models/enums.py](models/enums.py)). The set is closed; adding new types (e.g., `GPU_OPT`) is a future extension, not a runtime concern.

### 2.4 Storage model — producer-local with bandwidth matrix

We **do not** assume a shared filesystem. Each task writes its outputs to the **local disk of the node that executed it** (a `hostPath` volume in the k8s implementation). A child task that needs a parent's output reads it across the network from the producer's node.

This is the realistic model for the home-cluster target deployment (§2.1): commodity machines connected by a LAN, no NAS purchased, no shared object store stood up. It is also strictly more general than a shared-storage model — a shared-storage deployment is the special case where every node-pair has effectively infinite bandwidth.

Consequences:

| Aspect | Implication |
|---|---|
| **Inter-task data passing** | A child task placed on node $n_c$ that consumes parent output produced on node $n_p \neq n_c$ pays a **transfer cost** = `output_bytes / BandwidthMatrix[(n_p, n_c)]`. The cost is zero only when $n_p = n_c$. |
| **Data locality** | **Is** a placement factor. The transfer cost above appears inside $\widehat{\text{ECT}}$ in §8.5, so the scheduler naturally prefers placing children on (or near) their parents' nodes, all else equal. |
| **Checkpoint blobs** | Written to the producer's local disk. Resume on the same node is cheap (local read). Resume on a different node pays a transfer cost. The scheduler's preemption model (§10) accounts for this. |
| **Pegasus-style file dependencies** | DAX workflows reference input/output files by name. The scheduler resolves each name to a `DataPlacement` record (§4.10) when computing transfer cost; pre-staged inputs (workflow-level inputs at $t=0$) are treated as resident on every node (zero transfer). |
| **Container images** | Per-node local image cache, as before. Image locality (warm-image bonus) remains a placement factor independent of data locality. |
| **Bandwidth contention** | The bandwidth matrix is **static** for now — entries are sampled values, not real-time available bandwidth. Concurrent transfers contending for the same link are not modeled. This is an explicit simplification; §13 lists it as future work. |
| **Functional task contract** | Outputs are **values**, not slots. No two tasks ever write to the same output name. Tasks do not share mutable state across instances. See §5.5.1. This is what makes producer-local safe without distributed-consistency machinery. |

**K8s implementation (informative).** The scheduler runs a small **fileserver DaemonSet** on every node, listening on a per-node HTTP port and serving files out of the per-node `hostPath` directory. When a child task is placed on $n_c$, an `initContainer` in its pod spec fetches each required parent output from the producer's fileserver before the main container starts. Transfer time is observable (initContainer duration) and feeds back into bandwidth-matrix updates.

### 2.5 Bandwidth probe DaemonSet

The `BandwidthMatrix` referenced in §2.4 is populated and refreshed by a small probe DaemonSet:

- A pod runs on every node.
- Periodically (every $T_\text{bw}$ seconds, default **600s = 10 min**) each node copies a fixed-size blob (default 100 MB) from every other node's fileserver and records bytes/sec.
- Results are published to the scheduler, which updates `ClusterScenario.bandwidth_matrix` (§4.8).
- On scheduler boot, before the first probe completes, the matrix is initialized to a uniform conservative default (e.g., 100 MB/s = 1 Gbps LAN).
- Probe traffic is rate-limited to a small fraction of nominal LAN capacity so it doesn't perturb measurements.

Real-time fluctuation is **not** tracked — the matrix represents a steady-state estimate. Modeling per-link contention from concurrent transfers is out of scope (§13).

### 2.6 Thermal model — temperature with cooling capability

The target deployment (§2.1) is a home cluster: workstations in a closet, a desktop in a study, an ARM box on a shelf. None of these machines has datacenter-grade climate control. **Thermal throttling is a real performance hazard** on this hardware — a CPU that hits its junction-temperature limit downclocks itself, sometimes by 30–50%, until it cools.

**Naive temperature is the wrong signal.** A node at 70°C with strong cooling that holds 70°C indefinitely under sustained load is **better** than a node at 50°C with weak cooling that climbs to 90°C and throttles within a minute. The placement-relevant quantity is therefore **thermal headroom under expected load**, which depends on:

1. Current temperature.
2. The chip's throttle threshold (hardware-determined).
3. The node's cooling capability (how much heat it can dissipate at steady state).
4. The current load (other running tasks already heating the chip).

This spec models items 1–3 as Node attributes (§4.1) and uses item 4 implicitly via the `cpu_at_start` context bucket (§11.3). Ambient room temperature is *not* tracked separately — its effect is already absorbed into the live `cpu_temperature` reading (a hot room produces a hotter idle CPU, which directly lowers `thermal_headroom`).

**Cooling classes** (declared per node, enum `CoolingClass`):

| Class | Meaning | Typical sustained-load behaviour |
|---|---|---|
| `PASSIVE` | No fan or weak fan (fanless mini-PC, ARM SBC). | Throttles within seconds under sustained CPU_BOUND load. |
| `STANDARD` | Stock OEM fan + heatsink (typical workstation, off-the-shelf desktop). | Reaches equilibrium below throttle threshold in a temperate room; throttles in a hot room or under prolonged extreme load. |
| `HIGH` | Large aftermarket heatsink, good case airflow, rack server with engineered cooling. | Equilibrium well below throttle at any realistic sustained load. |
| `EXTREME` | Water cooling or equivalent. | Effectively no thermal limit at expected workloads; treat as `+∞` headroom. |

The class is declared in the cluster scenario (the user knows the hardware); it is not learned at runtime. A future extension may *refine* the class via observation — see §13.

**How thermal signals enter scheduling:**

- **Soft only.** Thermal signals never produce a hard placement constraint. A `CRITICAL` task may be placed on a node about to throttle if no better option exists — throttling is degradation, not failure (§8.4).
- **Through learning.** §11.3 adds an optional `thermal_headroom_at_start` bucket dimension. A node that has historically thrown 1.4×-slow runtimes when placed-while-hot will be modelled as such for the same bucket on the next decision.
- **Through ECT.** Predicted runtime $\mu_{\tau,n}$ comes from the contextually-correct bucket; if the bucket says "this node is slow when hot" then $\widehat{\text{ECT}}$ inherits that prediction, and the placement is naturally avoided when alternatives exist.

**Sensor availability.** Linux nodes expose CPU temperature via `/sys/class/thermal/thermal_zone*/temp` or `node_exporter`'s `node_hwmon_temp_celsius` metric. macOS workers (development only) require a custom DaemonSet or accept `cpu_temperature = None` and use a fallback. When `cpu_temperature` is `None`, the scheduler assumes ample headroom (HIGH bucket) and the cooling-class declaration alone steers placement — i.e. the cooling class is enough on its own to bias against `PASSIVE` nodes for long CPU_BOUND tasks.

**Out of scope:** a closed-loop thermal physics model that predicts the *steady-state* temperature of node $n$ if task $\tau$ is added to its current load (§13). Such a model would replace the bucket-based discounting with a continuous prediction $\widehat{T}^\text{ss}(n + \tau)$; we leave it as future work.

---

## Part III — Why Existing Schedulers Are Insufficient

The Kubernetes default scheduler `kube-scheduler` is the realistic reference point. It is:

- **Stateless.** Decides per-pod, with no memory of past decisions.
- **DAG-blind.** Has no model of workflow dependencies. Each pod is an island.
- **Heterogeneity-blind.** Doesn't learn that task X runs 5× faster on node Y; it only checks resource fits.
- **Preemption-shallow.** Supports priority preemption but not as a planning primitive.
- **Optimization-target-fixed.** Tunable only through plugin chains, not through a single objective.

Other named systems (HEFT, Quincy, Sparrow, Decima) each target a *different* problem (one DAG / cluster scheduling at scale / sub-second tasks / RL-driven). None natively addresses the combination of **(a) small heterogeneous cluster, (b) DAG workflows, (c) learning, (d) preemption-as-planning, (e) multi-workflow fairness** that this thesis targets. They become **baselines**, not the answer.

---

## Part IV — Entities & Data Model

This section defines the universe the scheduler reasons about. Every term used later is defined here.

### 4.1 Node

A **Node** is a single compute resource (a physical or virtual machine; a kind worker in dev, a real machine in production).

| Field | Type | Description |
|---|---|---|
| `node_id` | string | Unique identifier (cluster-stable). |
| `node_type` | `NodeType` enum | One of CPU_OPT, MEM_OPT, IO_OPT, GENERAL. |
| `total_cpu` | float | Total CPU cores available. |
| `total_memory` | float | Total RAM (MiB). |
| `free_cpu` | float | Currently uncommitted CPU. |
| `free_memory` | float | Currently uncommitted RAM. |
| `warm_images` | set\<string\> | Container images already pulled / cached on **this** node's local container runtime. **Per-node**; not shared across the cluster. (Image locality remains a placement factor independent of data locality, §2.4.) |
| `active_tasks` | dict\<task_id, RunningTask\> | Tasks currently executing on this node. |
| **`cooling_class`** | `CoolingClass` enum | *(new)* One of PASSIVE, STANDARD, HIGH, EXTREME. Declared per node in the cluster scenario; describes the steady-state heat-dissipation capability of this machine (§2.6). Not learned. |
| **`thermal_throttle_temp_c`** | float | *(new)* The CPU junction temperature at which the hardware begins thermal throttling. Declared per node; defaulted from a vendor table by `node_type` if not specified (Intel ~100°C, AMD ~95°C, ARM ~85°C). |
| *(future)* `cost_per_second` | Optional[float] | Reserved for cost-aware extension; ignored for now. |
| *(derived)* `cpu_usage_ratio` | float | `1 − free_cpu / total_cpu`. |
| *(derived)* `memory_usage_ratio` | float | `1 − free_memory / total_memory`. |
| *(derived)* `estimated_free_in` | Optional[float] | Predicted seconds until **soonest** running task finishes. |
| *(derived)* `estimated_all_free_in` | Optional[float] | Predicted seconds until **all** running tasks finish. |
| *(derived, sampled)* **`cpu_temperature`** | Optional[float] | *(new)* Most recently sampled CPU temperature (°C). Sampled by a metrics collector (Linux: `node_exporter` / `/sys/class/thermal`; macOS: optional custom DaemonSet). `None` when no sensor is available; the scheduler then treats headroom as HIGH and relies on `cooling_class` alone (§2.6). |
| *(derived)* **`thermal_headroom`** | Optional[float] | *(new)* `thermal_throttle_temp_c − cpu_temperature` when `cpu_temperature` is known; `None` otherwise. The basic placement signal: how many degrees can this node absorb before the chip throttles. A more accurate "headroom under expected load" prediction is left as future work (§2.6, §13). |

**Invariants:**
- $0 \leq \text{free\_cpu} \leq \text{total\_cpu}$
- $0 \leq \text{free\_memory} \leq \text{total\_memory}$
- $\sum_{\tau \in \text{active\_tasks}} \tau.\text{cpu\_request} = \text{total\_cpu} - \text{free\_cpu}$
- $\sum_{\tau \in \text{active\_tasks}} \tau.\text{memory\_request} = \text{total\_memory} - \text{free\_memory}$

### 4.2 RunningTask

A snapshot of a task that is currently executing on a Node.

| Field | Type | Description |
|---|---|---|
| `task_template_id` | string | Which template this is an instance of. |
| `task_instance_id` | string | Unique id of this execution. |
| `start_time` | float | Wall-clock when execution began. |
| `expected_runtime` | Optional[float] | Predicted total runtime (from `ProfileStore`). |
| `cpu_request` | float | Reserved CPU. |
| `memory_request` | float | Reserved memory. |
| *(derived)* `elapsed` | float | `now − start_time`. |
| *(derived)* `estimated_remaining` | Optional[float] | `max(expected_runtime − elapsed, 0)`. |

### 4.3 TaskTemplate (static blueprint)

Describes *a kind of task* — like a function signature. Many `TaskInstance`s can share one template.

| Field | Type | Description |
|---|---|---|
| `task_template_id` | string | Unique id within a workflow template. |
| `name` | string | Human-readable. |
| `task_class` | `TaskClass` enum | CPU_BOUND, MEMORY_BOUND, IO_BOUND. Used for anti-affinity & contention modeling. |
| `cpu_request` | float | Cores required. |
| `memory_request` | float | RAM required (MiB). |
| `image_name` | string | Container image. |
| `compatible_node_types` | List[NodeType] | Hard constraint — task can only run on these. |
| `min_cores` | int | Below this, the task can't run at all. |
| `max_cores` | Optional[int] | Above this, no benefit (None = "more is always better"). |
| `command` / `args` | List[str] | Container entrypoint. |
| **`checkpointable`** | bool | *(new)* Whether this task supports checkpoint / resume. Default: False. |
| **`checkpoint_interval_s`** | Optional[float] | *(new)* If checkpointable, how often it writes checkpoints. Hint for the scheduler; default None. |
| **`gang_group_id`** | Optional[str] | *(new)* If set, this task belongs to a **gang** within its workflow instance. All ready tasks sharing the same `(workflow_instance_id, gang_group_id)` must be placed atomically at a single tick or all held. Default None (no gang). See §5.9 and H8. |
| **`expected_output_bytes`** | Optional[Dict[str, int]] | *(new)* Per output field name, the declared expected size in bytes. Used to estimate transfer cost in §8.5 ECT before any observation exists. As executions accumulate, the actual size is recorded in `Observation` (§4.9) and the prediction is updated; the declared value is the cold-start prior. |
| *(future)* `deadline` | Optional[float] | Reserved for deadline scheduling extension. |
| *(future)* `cost_weight` | Optional[float] | Reserved for cost-aware extension. |

### 4.4 TaskInstance (live execution)

| Field | Type | Description |
|---|---|---|
| `task_instance_id` | string | Unique id for this run. |
| `workflow_instance_id` | string | Parent workflow. |
| `task_template_id` | string | Template this instance is from. |
| `state` | `TaskState` enum | WAITING, READY, SCHEDULED, RUNNING, FINISHED, FAILED. |
| `assigned_node_id` | Optional[str] | Node it's running / ran on. |
| `start_time` | Optional[float] | When execution began. |
| `finish_time` | Optional[float] | When execution ended. |
| **`last_checkpoint_at`** | Optional[float] | *(new)* Timestamp of last successful checkpoint, if any. Used by checkpoint-resume preemption. |
| **`preemption_count`** | int | *(new)* How many times this instance has been preempted. Used to cap preemption thrash per task. |
| **`upward_rank`** | Optional[float] | *(new)* DAG-position-based urgency, computed at workflow admission. Defined in algorithm spec; stored here. |

### 4.5 DependencyEdge

A single directed edge in a workflow DAG.

| Field | Type | Description |
|---|---|---|
| `parent_task_id` | string | Predecessor task template id. |
| `child_task_id` | string | Successor task template id. |
| `dependency_type` | `DependencyType` enum | EXECUTION (control only) or DATA (parent's output is child's input). |
| `data_field_names` | List[str] | If DATA, which fields are passed. |

### 4.6 WorkflowTemplate (static blueprint)

| Field | Type | Description |
|---|---|---|
| `workflow_template_id` | string | Unique id. |
| `name` | string | Human-readable. |
| `workflow_class` | `WorkflowClass` enum | REAL_TIME or BATCH. |
| `default_priority` | `PriorityClass` enum | Used if instance doesn't override. |
| `default_preemptable` | bool | Used if instance doesn't override. |
| `tasks` | Dict[str, TaskTemplate] | Map of task templates by id. |
| `edges` | List[DependencyEdge] | DAG edges. |

**DAG well-formedness invariants:**
- The graph is acyclic.
- All edge endpoints reference declared tasks.
- At least one task has zero incoming edges (root).
- All tasks are reachable from at least one root.

### 4.7 WorkflowInstance (live execution)

| Field | Type | Description |
|---|---|---|
| `workflow_instance_id` | string | Unique id. |
| `workflow_template_id` | string | Template this is an instance of. |
| `priority` | `PriorityClass` | Effective priority (may differ from template default). |
| `preemptable` | bool | Effective preemption flag. |
| `task_instances` | Dict[str, TaskInstance] | All task instances in this workflow. |
| `state` | `WorkflowState` enum | QUEUED, ADMITTED, RUNNING, FINISHED, FAILED. |
| `arrival_time` | Optional[float] | When the workflow was submitted. |
| `finish_time` | Optional[float] | When the workflow reached terminal state. |
| **`vruntime`** | float | *(new)* Accumulated cluster-time consumed; used for fairness. Initialized to 0. |
| **`upward_rank_max`** | Optional[float] | *(new)* Max upward rank of any task in the DAG; the workflow's inherent "depth-cost". |
| *(future)* `deadline` | Optional[float] | Reserved for deadline-aware extension. |

### 4.8 ClusterScenario

A snapshot of the cluster at scheduler boot.

| Field | Type | Description |
|---|---|---|
| `scenario_id` | string | Identifier. |
| `name` / `description` | string | Human-readable. |
| `nodes` | List[Node] | All cluster nodes. |
| **`bandwidth_matrix`** | Dict[Tuple[str, str], float] | *(new)* For every ordered pair of node ids $(n_p, n_c)$, the steady-state bytes/sec achievable when transferring from $n_p$'s fileserver to $n_c$. Entry $(n, n)$ is conventionally $+\infty$ (or omitted; treated as zero transfer cost). Populated and refreshed by the bandwidth-probe DaemonSet (§2.5). On boot before first probe, defaults to a uniform conservative value. |

### 4.9 Profile / Observation (learning data)

Defined fully in [Part XI](#part-xi--learning-model). Briefly:

- `Observation` = `(runtime, startup, cpu_at_start, memory_at_start, io_bytes_read, io_bytes_written, output_bytes_by_field, temperature_at_start, temperature_at_end, timestamp)` per executed task. The `output_bytes_by_field` map captures *actual* output sizes per declared output name, used to refine `TaskTemplate.expected_output_bytes` (§4.3) over time. The temperature fields are `Optional[float]`; recorded when a sensor is available, otherwise `None` and ignored by learning.
- `NodeMetrics` = rolling window of observations + derived statistics (median, mean, **stddev**) per `(task_template_id, node_id)`.
- `TaskProfile` = aggregate per `task_template_id` across all nodes, ranked.

### 4.10 DataPlacement (runtime data-locality registry)

A scheduler-internal map maintained alongside the active workflow set. It records *where* each piece of produced data currently lives, so the scheduler can compute transfer cost when evaluating placements.

| Field | Type | Description |
|---|---|---|
| `key` | Tuple[workflow_instance_id, task_instance_id, field_name] | Identifies a specific output of a specific task instance. |
| `node_id` | string | Node whose local disk currently holds the data (the producer node). |
| `size_bytes` | int | Actual size on disk. Recorded when the producer task finishes. |
| `written_at` | float | Wall-clock when the data became readable. |
| *(optional)* `replicas` | Set[str] | Additional node ids that have a cached copy after a transfer. The scheduler may opportunistically treat replicas as zero-cost sources for further children. |

**Lifecycle:**
- An entry is created when a task completes successfully and emits a declared output.
- An entry is read whenever the scheduler scores a candidate placement for a downstream task: transfer cost = $\text{size\_bytes} / \text{BandwidthMatrix}[(\text{node\_id}, n_c)]$.
- An entry is **garbage-collected** when its parent workflow reaches a terminal state (`FINISHED` or `FAILED`), or after a per-entry TTL (default 1 hour) — whichever comes first.
- Entries persist across scheduler ticks but are not durable across scheduler restarts (rebuilt by querying live workflows on resume).

If a task instance in `DataPlacement` is preempted and re-run elsewhere, the registry is updated when the new instance completes — there is no record of the killed instance's outputs (they were never made visible to children, by H4).

---

## Part V — Workload Regime

### 5.1 Workload mode (both supported)

The same engine must handle both:

- **Closed-batch mode.** A fixed set of $N$ workflows is enqueued at $t=0$. Measure makespan and resource utilization until the last finishes. (Replicates published HEFT-style benchmarks.)
- **Open-arrival mode.** Workflows arrive at rate $\lambda$ (Poisson, deterministic, or trace-driven) over a finite horizon. Measure throughput, p50/p95/p99 completion time, queue length stationarity. Standard practice: load sweep at $\lambda \in \{0.1, 0.3, 0.5, 0.7, 0.9\} \times \text{capacity}$.

The submission API is identical (`submit_workflow`); only the **driver** differs.

### 5.2 Recurrence

Task templates recur **frequently**. Workflows recur **less frequently**. Concretely:

- A given `task_template_id` will execute hundreds of times across an evaluation run (so per-task-template learning has time to converge).
- A given `workflow_template_id` may execute dozens of times.
- Cold-start (first execution of a task template) is a transient regime, not the steady state.

### 5.3 DAG shapes (must support all)

The scheduler must handle any well-formed DAG. For evaluation we explicitly test:

| Shape | Example | Why it matters |
|---|---|---|
| **Linear** | `A → B → C` | Simplest case; current code already handles. |
| **Fan-out** | `A → {B, C, D}` | Tests parallel placement decisions. |
| **Fan-in** | `{A, B, C} → D` | Tests synchronization / readiness. |
| **Diamond** | `A → {B, C} → D` | Tests both fan-out and fan-in. |
| **Deep linear** | `A → B → ... → Z` (≥10 deep) | Tests critical-path-length awareness. |
| **Wide parallel** | 50 independent leaves under one root | Tests batch-mode placement. |
| **Real benchmarks** | Pegasus Montage, CyberShake | Tests against published numerical comparisons. |

DAG sizes range from 3 tasks to a few hundred. **Workflows with > 1000 tasks are out of scope** for this thesis; if encountered, the scheduler should still be correct but evaluation isn't expected at that scale.

### 5.4 Task duration regime

- Tasks run on the order of **seconds to minutes** (1s – 600s typical).
- Sub-second tasks are out of scope (Sparrow's regime).
- Multi-hour tasks are out of scope for evaluation but should still be correct.

### 5.5 Inter-task data volume & locality

- Inter-task data passing happens through two channels:
  1. **Small metadata** — `__TS_OUTPUT__` log lines (KB-scale JSON) inlined in pod logs. Treated as zero-cost; no transfer modeled.
  2. **Bulk outputs** — files written to the producer node's local disk under the per-task `hostPath` directory, served by the fileserver DaemonSet (§2.4). Transfer cost is modeled.
- **Data locality IS a placement factor.** A child placed on the same node as its parent pays zero transfer; a child placed elsewhere pays `output_bytes / BandwidthMatrix[(n_p, n_c)]`. This factor is folded into $\widehat{\text{ECT}}$ (§8.5), not added as a separate score term.
- **Image locality** (warm container images, §4.1's `warm_images`) is a separate placement factor that operates the same way it always has.
- Per-link bandwidth from the matrix (§4.8) is the operative quantity. Real-time bandwidth contention from concurrent transfers is not modeled; the matrix represents a steady-state estimate (§13).
- Output size predictions come from `TaskTemplate.expected_output_bytes` (declared default, §4.3) refined by `Observation.output_bytes_by_field` (learned, §4.9). Cold-start uses the declared value; once $\geq 3$ observations exist, the empirical median is used.

#### 5.5.1 Functional task contract (invariant)

The data-locality model in §2.4 and §5.5 is only sound because the scheduler enforces a strict **functional contract** on tasks:

1. **Pure with respect to declared inputs and outputs.** A task's behaviour is fully determined by the values referenced via its DAG predecessors' declared output fields plus any workflow-level inputs. No hidden inputs are permitted.
2. **No shared mutable state across tasks.** Tasks may not coordinate through a shared database, shared message queue, shared in-memory cache, or any other mutable channel that lives outside the DAG. The DAG edges *are* the only sanctioned channel.
3. **Outputs are values, not slots.** A task produces named output values. No two tasks (in the same workflow or across workflows) ever write to the same output name. Output names are scoped to `(workflow_instance_id, task_instance_id, field_name)` (§4.10).
4. **Inputs are immutable.** A task may not mutate the bytes of any input it receives. (Operationally enforced by mounting inputs read-only in the container.)
5. **Determinism is not required.** A task may be non-deterministic (random sampling, timestamps, etc.); the scheduler does not assume same-input-same-output. Speculative execution (§11.7) deliberately races two instances of the same task with this in mind.

**Why this matters for scheduling.** Under this contract, a task's outputs are owned by exactly one node (the producer). There is no consistency question — no two writers, no replication-coherence concern, no read-your-writes ordering across nodes. Caching, replication, and re-execution are all safe transformations. This is what makes producer-local storage tractable without a distributed filesystem; the model would be unsound if any of clauses 1–4 could be violated.

**Out of scope.** Workflows that need shared mutable state — a database that two tasks both update, a long-running model server that several tasks query and modify — are outside the scope of this scheduler. Such workloads belong to a different scheduling regime (services + jobs, not pure batch DAGs).

### 5.6 Priority semantics

`PriorityClass ∈ {BATCH=1, REAL_TIME_MEDIUM=2, REAL_TIME_HIGH=3, CRITICAL=4}` (see [models/enums.py](models/enums.py)).

| Class | Meaning | Aging behavior | Preemption rights |
|---|---|---|---|
| BATCH | Background, best-effort. | None. | Cannot preempt anyone. |
| REAL_TIME_MEDIUM | User-interactive. | Ages to HIGH after `AGING_TTL` (60s default). | Can preempt BATCH. |
| REAL_TIME_HIGH | Latency-sensitive. | None (already top non-critical). | Can preempt BATCH and MEDIUM. |
| CRITICAL | Override. May force-fit on incompatible nodes. | None. | Can preempt anyone. |

Priority is a **user contract** — the scheduler may not violate strict ordering across classes. Within a class, the scheduler is free to optimize (this is where DAG-awareness, learning, and search live).

### 5.7 Workload examples

**Example 1 — single linear workflow (current default):**
```
WF-001: io → mem → cpu       priority=BATCH       arrival=t=0
```

**Example 2 — concurrent workflows of mixed priority (open-arrival):**
```
WF-001: <Montage-50>          priority=BATCH        arrival=t=0
WF-002: io → cpu              priority=REAL_TIME_HIGH arrival=t=10
WF-003: <CyberShake-30>       priority=REAL_TIME_MEDIUM arrival=t=15
WF-004: cpu → mem → cpu       priority=CRITICAL     arrival=t=22
```

**Example 3 — DAG with fan-out / fan-in:**
```
       ┌─→ B ─┐
WF-005: A ─┼─→ C ─┼─→ E         priority=BATCH        arrival=t=0
       └─→ D ─┘
```

### 5.8 Concurrent multi-workflow execution

**Priority is a preference signal, not an exclusivity claim.** Multiple workflows — at the same or different priority classes — execute **concurrently** by default. The scheduler never reserves the cluster for a single workflow.

Concrete rules:

1. **No exclusive ownership.** A higher-priority workflow does *not* halt lower-priority work. As long as both have ready tasks and feasible nodes exist, both are placed.
2. **Idle-node-must-fill rule.** If a node is idle and *any* compatible ready task exists in the queue, that node should be filled — even by a BATCH task while a CRITICAL workflow is running. Wasting capacity to "reserve" for a higher class is forbidden unless an explicit reservation is active (EASY-style backfill, see algorithm spec).
3. **Within-DAG concurrency.** If workflow A has 5 ready tasks (e.g., a fan-out), all 5 may run on different nodes simultaneously, even if A is BATCH and a single REAL_TIME_HIGH task is queued — provided the HIGH task can also be placed (it gets first pick of nodes; A fills the rest).
4. **Contention only matters when nodes are scarce.** Priority *only* changes the outcome when (a) two tasks of different priority compete for the same scarce node, or (b) preempting a lower-priority running task would unblock a higher-priority queued task subject to §10.
5. **Cross-workflow dependencies do not exist.** Workflows are independent DAGs; tasks in workflow A never depend on tasks in workflow B. The scheduler may interleave their tasks freely.

This is the standard concurrent-multi-job semantics from production schedulers (YARN, Borg, kube-scheduler with PriorityClass). It is called out explicitly here because the current code's per-tick top-down sort can *appear* to imply exclusivity; it does not, and the new algorithm must preserve concurrency by construction.

### 5.9 Gang scheduling

Some workflows contain **gangs** — sets of tasks that must start at the same instant because they coordinate at runtime (e.g., a parallel reducer stage where workers exchange data through a barrier).

Gang semantics:

- A `TaskTemplate` may declare an optional `gang_group_id: str` (§4.3).
- All tasks in workflow $w$ sharing the same `gang_group_id` form a **gang**.
- **Strict atomicity** (Ousterhout 1982 coscheduling): the scheduler must place either *all* members of the gang at the same tick, or *none* of them.
- Gang-member tasks all become eligible (`READY`) only when **every** member's DAG predecessors are FINISHED. Until that holds for all members, the gang is not placeable; partially-ready gang members `hold`.
- A gang inherits the workflow's priority class and counts as a single unit for fairness accounting.
- If a gang is **structurally infeasible** (the cluster cannot fit all members simultaneously even with full preemption of lower-priority tasks), the gang is failed with reason `INFEASIBLE-GANG` per §7.3.
- If a gang is **transiently infeasible** (would fit if some other tasks finished), it holds with a starvation timer (§7.2 S3); on timeout, the gang is escalated by aging.

**Out of scope:** *multi-node tasks* — a single task whose container spans multiple nodes (MPI rank-0 across hosts). Our task model is one task = one container = one node. Gang scheduling here orchestrates *N independent single-node tasks that must start together*, not one task that needs N nodes.

---

## Part VI — The Scheduler Interface

### 6.1 Inputs (read at every decision)

At each decision point the scheduler observes:

| Input | Source | Update frequency |
|---|---|---|
| Set of nodes $N$ with current `free_cpu`, `free_memory`, `warm_images`, `active_tasks` | k8s Metrics API / simulator | Every tick |
| Set of running tasks $R$ with `start_time`, predicted `expected_runtime` | Internal state | Every tick |
| Queue of ready tasks $Q$ (DAG-eligible, awaiting placement) | Internal state | Every tick |
| Set of admitted workflows $W$, each with DAG + priority + vruntime | Internal state | On admission |
| `ProfileStore` $P$ — predictions $(\mu_{\tau,n}, \sigma_{\tau,n}^2, \text{failure\_rate}_{\tau,n})$ | Persistent | Updated post-execution |
| Wall-clock time `now` | OS | Every read |

### 6.2 Outputs (per tick)

The scheduler emits an **action set** $A$, where each action is one of:

| Action | Semantics | Cost |
|---|---|---|
| `place(task, node)` | Start a queued task on a node. | Image pull (cold) or container start (warm). |
| `preempt_kill_restart(task)` | SIGTERM the task; re-enqueue at the head of its priority class. | Lost work since `start_time`. |
| `preempt_checkpoint(task)` | Trigger checkpoint hook; re-enqueue with `last_checkpoint_at` metadata. | Checkpoint write overhead; only valid if `task.checkpointable`. |
| `hold(task)` | Explicit no-op for this task this tick. Default for all queued tasks not placed. | None. |

A tick's output is a **set** of compatible actions: all `place` and `preempt` operations after applying must respect capacity feasibility and DAG ordering.

### 6.3 Scheduler-internal state (persistent across ticks)

- `ProfileStore` (durable, persisted to disk on a timer).
- Per-node `active_tasks`.
- Per-workflow `vruntime`.
- Per-task `preemption_count`.
- Per-(task, node) failure / eviction counters with **decay**.

### 6.4 Decision triggers

A decision is triggered by:
1. **Tick clock** — the engine wakes every $T_\text{tick}$ seconds (default 1s).
2. **Workflow submission** — wake immediately.
3. **Task completion / failure** — wake immediately.
4. **Node state change** — wake immediately (rare; e.g. node lost).

All triggers funnel into the same `run_tick(state)` entry point ([engine.py](engine.py)). Scheduler logic is **pure**: same inputs ⇒ same outputs (modulo deliberate randomization).

---

## Part VII — Constraints

### 7.1 Hard constraints (must always hold)

A scheduling decision $A$ is **infeasible** if any of these are violated. The scheduler must never emit an infeasible $A$.

| H# | Constraint | Formal statement |
|---|---|---|
| H1 | **Capacity feasibility** | After applying $A$, every node $n$ has $\sum_{\tau \text{ on } n} \tau.\text{cpu\_request} \leq n.\text{total\_cpu}$ and analogously for memory. |
| H2 | **Type compatibility** | For every `place(τ, n)` in $A$: `n.node_type ∈ τ.compatible_node_types` *unless* the task's priority is `CRITICAL` (which may force an incompatible fit; see §5.6). |
| H3 | **DAG ordering** | A task may only be placed once **all** its DAG predecessors are in `FINISHED` state. |
| H4 | **Single execution** | A task instance may only be `RUNNING` on at most one node at any instant. (Speculative execution is a *different* mechanism that uses *separate* instances; see §11.7.) |
| H5 | **No undefined preemption** | `preempt_checkpoint(τ)` may only be issued when `τ.checkpointable == True`. |
| H6 | **Min-cores compliance** | $\tau.\text{cpu\_request}$ must satisfy $\tau.\text{cpu\_request} \geq \tau.\text{min\_cores}$ before placement. |
| H7 | **Workflow integrity** | A failed task whose failure cannot be retried propagates `FAILED` to all downstream descendants ([services/workflow_manager.py](services/workflow_manager.py)). |
| H8 | **Gang atomicity** | For every gang $G$ (set of READY tasks sharing `(workflow_instance_id, gang_group_id)`): either every $\tau \in G$ has a `place(τ, n)` action in $A$, or no $\tau \in G$ does. Partial gang placement is forbidden. (See §5.9.) |
| H9 | **No exclusive priority** | An idle, compatible node may not be left idle when a ready task of *any* priority class is feasible on it. (See §5.8 rule 2; this forbids "reserve and starve" behaviour.) Exception: an EASY-style explicit reservation is permitted by §10.6. |

### 7.2 Soft constraints (should hold)

Violations are not bugs but degrade quality. The scheduler trades these against the objective.

| S# | Constraint | Quality penalty if violated |
|---|---|---|
| S1 | **Priority class respect** | Higher classes should run before lower classes within each priority comparison. Hard for CRITICAL. |
| S2 | **Min-core satisfaction** | Tasks should get $\geq \tau.\text{min\_cores}$ when feasible. |
| S3 | **No starvation** | No workflow should wait indefinitely; aging or vruntime fairness should kick in. |
| S4 | **Decision time budget** | Per-tick decision should complete within $T_\text{decision}$ ([Part XII](#part-xii--decision-time-budgets)). |
| S5 | **Preemption budget** | At most $K$ preemptions per node per minute (default $K = 3$). |
| S6 | **Per-task preemption cap** | A single task instance shouldn't be preempted more than $M$ times (default $M = 2$). |
| S7 | **Image warmth respect** | Prefer nodes where the image is warm, all else equal. |

### 7.3 Infeasibility handling

If the queue contains a task that is **structurally infeasible** (no node in the cluster could ever satisfy H1+H2 even with full preemption), the scheduler must:

1. Mark the task `FAILED` with a structured reason (`INFEASIBLE`, e.g. "memory_request exceeds total_memory of every compatible node").
2. Propagate the failure per H7.
3. Log the decision for diagnostics.

If a task is **transiently infeasible** (would fit if some other task finished), the scheduler holds it.

---

## Part VIII — Objectives

### 8.1 Primary objectives

In priority order:

1. **Throughput** (open-arrival mode) / **Makespan** (closed-batch mode). These are duals — high throughput in open mode and low makespan in closed mode are produced by the same decisions.
2. **Per-workflow completion time** weighted by priority class. A high-priority workflow's completion time matters more than a low-priority one's.

### 8.2 Secondary objective

3. **Inter-workflow fairness within priority class**. Concurrent workflows of the same priority should make proportional progress; none should starve. Quantified by a fairness index (e.g., Jain's) over per-workflow effective rate.

### 8.3 Tertiary objectives

4. **Resource utilization** (steady-state, averaged across nodes).
5. **Decision explainability** — every action in $A$ should map to specific score contributions in the objective.
6. **Robustness to noise** — tail-latency stability under runtime variance.

### 8.4 Trade-offs explicitly accepted

The scheduler **may degrade** these in pursuit of objectives 1–3:

- **Average task wait time across all priorities.** It is acceptable for BATCH tasks to wait long under contention if interactive tasks need the cluster — but *only when nodes are scarce* (§5.8 rule 4).
- **Worst-case CPU/memory utilization on individual nodes.** Nodes may sit idle if no compatible work is queued. They may **not** sit idle when compatible work *is* queued (H9).
- **Image-warmth preference** when no recent placement matches.
- **Decision determinism.** Tie-breaking may be randomized.

### 8.5 Composite objective shape (informal)

The algorithm spec will formalize this. In rough form, every decision optimizes:

$$
J(A) = \underbrace{- \sum_{w \in W} \alpha_{p(w)} \cdot \widehat{\text{ECT}}(w \mid A)}_{\text{progress + priority}} \;-\; \underbrace{\lambda_\text{fair} \cdot \text{fairness\_debt}(W)}_{\text{fairness}} \;-\; \underbrace{\lambda_\text{churn} \cdot \text{preemption\_cost}(A)}_{\text{churn cost}} \;-\; \underbrace{\lambda_\text{risk} \cdot \text{plan\_variance}(A)}_{\text{risk}}
$$

where $\widehat{\text{ECT}}(w \mid A)$ is the predicted expected completion time of workflow $w$ if action set $A$ is applied. Hyperparameters $\alpha, \lambda$ are tunable; defaults defined in algorithm spec.

**ECT structure (informal).** For a single task $\tau$ placed on node $n$ under plan $A$, the predicted finish time has three components:

$$
\widehat{\text{finish}}(\tau, n \mid A) \;=\; t_\text{ready}(\tau, n \mid A) \;+\; \text{startup}(\tau, n) \;+\; \text{transfer}(\tau, n) \;+\; \mu_{\tau, n}
$$

where:
- $t_\text{ready}(\tau, n \mid A)$ — earliest moment $n$ has free capacity for $\tau$ given all prior placements in $A$ and its currently-running tasks.
- $\text{startup}(\tau, n)$ — image pull (cold) or container init (warm), from `ProfileStore`.
- $\text{transfer}(\tau, n) = \sum_{\text{output } o \text{ of parents of } \tau} \frac{\text{size}(o)}{\text{BandwidthMatrix}[(\text{producer}(o), n)]}$, summed over outputs not already resident on $n$ (per `DataPlacement`, §4.10). Zero when all parents ran on $n$. The scheduler reads the producer node from `DataPlacement` and the size from `Observation` (or from `TaskTemplate.expected_output_bytes` if no observation exists).
- $\mu_{\tau,n}$ — predicted runtime from `ProfileStore`, contextualized by `cpu_at_start`/`memory_at_start` buckets (§11.3).

$\widehat{\text{ECT}}(w \mid A)$ then aggregates per-task finishes along $w$'s critical path. Concretely it is the longest path in $w$'s DAG with edge weights = $\widehat{\text{finish}}$ of the destination task. Exact formulation (deterministic forward projection vs. stochastic simulation) is fixed in the algorithm spec (Q1).

**Where data locality comes from in $J$.** The transfer term inside $\widehat{\text{ECT}}$ is the entire data-locality mechanism. There is no separate locality bonus; the scheduler prefers parent-co-location *because* doing so reduces transfer time, which reduces ECT, which raises $J$. This makes the trade-off honest: a small slow co-located node is preferred over a large fast remote node only when transfer time would actually exceed runtime savings.

### 8.6 Concurrent execution policy (operational)

This subsection specifies how the objective in §8.5 is evaluated *across* concurrent workflows so that §5.8's concurrency rules are satisfied by construction:

- $\widehat{\text{ECT}}$ is computed **per workflow** under the assumption that all currently-admitted workflows continue to receive resource.
- Priority weights $\alpha_p$ create **preference**, not **exclusion**: a CRITICAL workflow's $\alpha$ may be 100× a BATCH workflow's, but the BATCH workflow's term still appears in $J$, so leaving a node idle when the BATCH workflow could use it strictly reduces $J$.
- Therefore the scheduler will *never* hold an idle node simply because a higher-priority workflow exists — doing so would *worsen* $J$ via the BATCH term, with no compensating gain on the higher-priority term unless preemption is also at play (§10).
- The hard constraint H9 is redundant with this property under a correctly-specified $J$, but is kept as a guard against badly-tuned weights.

---

## Part IX — Failure Model

### 9.1 Failure taxonomy

| Type | Frequency in target deployment | Distinguishable signal | Treatment |
|---|---|---|---|
| **Node down** (machine off, kubelet unreachable) | Rare | k8s node `Ready=False`, watcher loss | Mark all running tasks on that node as `FAILED-NODE`; re-enqueue at front of queue; no penalty to learning. |
| **Pod evicted** (k8s evicts due to resource pressure / preemption) | Common in oversubscribed clusters | k8s eviction event with reason | Treat as **scheduler's fault**, not node's. Bump per-node `eviction_count`, but **do not** treat as task failure for `failures_by_node`. Re-enqueue. |
| **OOMKilled** (container exceeded memory) | Moderate | exit code 137 + OOMKilled annotation | Bump `failures_by_node` (this *is* a task-on-node failure). On retry, request more memory if possible. |
| **Task crash** (non-zero exit, segfault, exception) | Rare for stable code | non-zero exit code, no OOM marker | Bump `failures_by_node`. Retry up to a per-template cap (default 2). |
| **Task hang** (no progress, exceeds 3× predicted runtime) | Rare | wall-clock heuristic | Mark as straggler candidate (see §11.7); after 5× predicted, kill and re-enqueue. |
| **Task non-determinism** (correct exit but wrong output) | Out of scope | n/a | Not a scheduler concern. |
| **Network partition** (k8s API unreachable) | Rare on LAN | API errors / watch disconnect | Pause new dispatches; preserve state; resume on reconnect. |

### 9.2 Counters and decay

The current code keeps a monotonic `failures_by_node` counter. **This is a defect** documented in this spec:

- All failure / eviction counters **must decay**. Either via rolling window (consistent with `Observation` storage) or via exponential decay (default $\beta = 0.95$ per hour).
- A node that flapped once at boot must not be permanently penalized.
- Failures and evictions are **separate counters**; only failures penalize the (task, node) learning.

### 9.3 Retry policy

- Default `max_retries_per_template = 2`.
- Retries always go through the full scheduler (not blindly retry on the same node).
- A task failing on $\geq 3$ distinct nodes is marked permanently `FAILED-PERSISTENT` (likely a code bug, not a node issue) and propagated per H7.

---

## Part X — Preemption Model

### 10.1 Why preemption is first-class here

Preemption is **not** a side mechanism reserved for priority emergencies. It is a *planning primitive*: the scheduler may preempt as part of normal placement reasoning if doing so improves the objective enough to outweigh the churn cost.

### 10.2 Preemption modes

| Mode | When applicable | Cost model | Implementation status |
|---|---|---|---|
| **Kill + restart** | Always (no flag required). | Lost progress = elapsed wall-clock + restart overhead (image pull if cold, container init). | Default; available now. |
| **Checkpoint + resume** | Only if `task.checkpointable == True`. | Cost = checkpoint write overhead + resume overhead. Resume continues from `last_checkpoint_at`. | **Architecture wired now**, full implementation deferred. Tasks must opt in by setting flag and writing periodic checkpoints. |
| **CRIU live migration** | Out of scope for this thesis. | Listed for completeness; not implemented. | Future work. |

### 10.3 When preemption may be triggered

The scheduler considers preemption in three situations, all part of the same objective evaluation:

1. **Priority preemption.** A higher-priority task can't fit; preempting a lower-priority running task on a feasible node would let it run.
2. **Makespan preemption.** A queued task on the critical path of a workflow would benefit dramatically from a node currently held by a less-urgent task. Permitted only if $\Delta J > 0$ after accounting for churn.
3. **Defragmentation preemption.** Cluster is fragmented; a large queued task (or gang, §5.9) can't fit anywhere; preempting + relocating small tasks would consolidate space. See §10.6 for the full defragmentation model.

All three use the same $J$. Mode 3 is enabled by algorithm-spec phase 5; modes 1 and 2 by phase 2.

### 10.4 Preemption budget & guards

- Max $K = 3$ preemptions per node per rolling 60s window (S5).
- Max $M = 2$ preemptions per task instance lifetime (S6) — after that, the task is allowed to run to completion regardless.
- Tasks within $\theta = 5s$ of predicted completion are immune to preemption (would waste more work than save).
- A preemption that re-places the victim on a node *worse than* its current one is forbidden unless the higher-priority winner is `CRITICAL`.

### 10.5 Victim selection (when multiple candidates)

In order of preference, prefer victims that:

1. Are lower priority class.
2. Have lower remaining elapsed wall-clock invested (less to throw away).
3. Are **not** on the critical path of their workflow.
4. Are checkpointable (cheaper to resume than restart).
5. Have higher `preemption_count` ties (if any) broken by oldest start time.
6. Are **not** members of a gang in mid-execution (preempting one gang member kills the whole gang's progress; treat as $|G| \times$ churn cost).

### 10.6 Defragmentation (reactive and proactive)

Fragmentation arises when free capacity is *aggregate-sufficient* but *spatially scattered* — e.g., 10 nodes each with 1 free CPU cannot run a 4-CPU task. The scheduler addresses this with two complementary mechanisms:

#### 10.6.1 Reactive defragmentation (per-tick)

Triggered when at least one ready task or gang in $Q$ cannot be placed on any single node, despite $\sum_n \text{free\_cpu}_n \geq \tau.\text{cpu\_request}$ (and analogously for memory).

Procedure:

1. Identify the largest unplaceable task $\tau^\star$.
2. Search for a **migration plan**: a set of preempt+replace actions that, when applied, free a contiguous-enough block on some node $n^\star$ to fit $\tau^\star$.
3. Score the plan via $J$. If $\Delta J > 0$ (defragmentation gain on $\tau^\star$ exceeds churn cost on victims), commit.
4. Otherwise, hold $\tau^\star$ and try again next tick — fragmentation may resolve naturally.

#### 10.6.2 Proactive defragmentation (background)

A periodic pass (every $T_\text{defrag}$ seconds, default 30s) that runs even when nothing is blocked. It looks for **packing improvements**:

- Two half-loaded nodes that could be consolidated to one fully-loaded + one idle node (idle node is then a hot reserve for incoming large tasks).
- A small task on a fast/scarce node that would run equally well on a slow/abundant node, freeing the scarce node for tasks that *need* it.

Proactive defrag is bounded by:

- Same preemption budget caps as reactive (§10.4 K, M).
- Only runs in the **heavy/async** decision path (Part XII), never in hot or tick paths.
- Only commits if $\Delta J > \eta$ (default $\eta = $ 5% of average per-tick $J$ change), to avoid useless churn.

#### 10.6.3 Reservation alternative (EASY-style)

Defragmentation preemption is the *active* approach. The *passive* alternative — **EASY backfilling** — reserves the next-finishing slot for a head-of-line big task and only allows a smaller task to backfill if it provably finishes before the reservation comes due. Both mechanisms coexist:

- EASY reservations are cheap (no preemption); used as the **first-line** tool against fragmentation.
- Reactive/proactive defrag preemption is used when EASY can't help (e.g., the head-of-line task wouldn't fit even after the next finish).

---

## Part XI — Learning Model

### 11.1 What is learned

Per `(task_template_id, node_id)` pair, the scheduler maintains:

- $\mu_{\tau,n}$ — median runtime.
- $\sigma_{\tau,n}^2$ — runtime variance (**newly required**, currently not tracked).
- $\hat{\mu}^\text{EMA}_{\tau,n}$ — exponentially-moving mean (**newly required**, for drift detection).
- $\mu^\text{startup}_{\tau,n}$ — median startup (image pull + container init).
- $\mu^\text{io}_{\tau,n}$ — median I/O throughput (**newly tracked**, learning-only for now).
- `failure_rate` — failures / (failures + successes), with decay.
- `eviction_rate` — evictions / total placements, with decay (separate from failure).

Per `task_template_id`, aggregated across nodes:

- `preferred_node_order` — ranked list of `NodeType`.
- `preferred_node_ids` — ranked list of specific node ids.

### 11.2 Storage

- Rolling window of $W = 20$ observations per `(task, node)` pair.
- Persisted to disk every 30s and on shutdown.
- JSON format (existing `to_json` / `load_json` in [models/profile_store.py](models/profile_store.py)).

### 11.3 Contextual buckets (newly required)

Observations are bucketed by **context at start time**:
- `cpu_at_start` ∈ {LOW (<30%), MEDIUM (30–70%), HIGH (>70%)}
- `memory_at_start` ∈ {LOW (<50%), HIGH (≥50%)}
- *(optional, sensor-dependent)* `thermal_headroom_at_start` ∈ {LOW (<10°C), MEDIUM (10–25°C), HIGH (>25°C)} — active only when `Node.cpu_temperature` is known. This is the bucket that captures the §2.6 "hot-but-stable beats cool-but-poorly-cooled" intuition: it combines current temperature *and* the throttle threshold (which already encodes hardware) into one number, and observations naturally separate by `cooling_class` because each node's headroom distribution is fixed by its own cooling.

This produces up to 6 sub-medians per `(task, node)` pair (or up to 18 when the thermal dimension is active). The scheduler queries the bucket matching current node state; falls back to a less-specific bucket if the exact bucket has < 3 observations (drop the thermal dimension first, then memory, then cpu — in that order, since thermal is the noisiest signal). This is the central mechanism that converts the currently-unused `node_cpu_at_start` / `node_memory_at_start` fields into a real signal.

### 11.4 Exploration (revised from current)

Replace the three-phase coverage logic with **UCB-style exploration**:

$$
\text{score}(\tau, n) = \hat{\mu}_{\tau,n} - \beta \cdot \frac{\hat{\sigma}_{\tau,n}}{\sqrt{\text{count}_{\tau,n}}}
$$

A low-confidence node automatically gets a "free probe" via the variance term. As observations accumulate, $\hat{\sigma}/\sqrt{n}$ shrinks and the algorithm exploits.

The current `EXPLORATION_RATE = 0.10` and `MIN_OBSERVATIONS_PER_NODE = 3` constants become a compatibility fallback for the *legacy* policy only.

### 11.5 Cold-start

- A brand-new `task_template_id` has no observations. Default behavior: distribute the first $|N|$ executions across distinct compatible nodes (current "Phase A" logic preserved).
- *(Future, optional)* Inherit predictions from the most-similar known `task_template_id` (similarity by `task_class`, `compatible_node_types`, `image_name` base layer, resource request bucket).

### 11.6 Drift detection

When $\lvert \hat{\mu}^\text{EMA}_{\tau,n} - \hat{\mu}_{\tau,n} \rvert / \hat{\mu}_{\tau,n} > \tau_\text{drift}$ (default $\tau_\text{drift} = 0.30$), mark the `(task, node)` pair as **drifting** and force exploration on the next placement. Detects regressions from kernel updates, thermal throttling, or noisy neighbors.

### 11.7 Speculative execution (straggler mitigation)

When a running task exceeds $\rho \cdot \hat{\mu}_{\tau,n}$ wall-clock (default $\rho = 1.5$):

1. Launch a duplicate `TaskInstance` (separate id) on the best-available alternative node.
2. The first to finish "wins"; the loser is killed.
3. Both completion times feed back into observations, but the *killed* one is marked appropriately (not counted as failure).

This is a different mechanism from preemption — preemption removes a task to make room; speculation duplicates a task to win a race.

---

## Part XII — Decision-Time Budgets

The scheduler has three execution paths with separate time budgets:

| Path | When it runs | Budget | What runs |
|---|---|---|---|
| **Hot** | On task completion or single-task placement | < 100ms (p99) | Lookup-and-place a single newly-ready task. |
| **Tick** | Every $T_\text{tick}$ (default 1s) | < 1s (p99) | Full per-tick planning over all queued tasks. Allowed to use up to 50% of $T_\text{tick}$ for search. |
| **Heavy / async** | On workflow admission, profile update | seconds OK | Compute upward ranks for new workflows; recompute preferred orders; defrag pass. |
| **Offline** | Between runs | minutes OK | Profile compaction; weight tuning; replay analysis. |

**Implication:** at the cluster sizes in §2.1 (≤30 nodes × ~50 ready tasks), **algorithm choice is not constrained by wall-clock cost**. A full per-tick optimization pass costs single-digit milliseconds at this scale. The 1s budget allows generous search procedures.

---

## Part XIII — Out of Scope / Non-Goals

These are explicitly **not** addressed by this scheduler. They appear here so reviewers know they were considered and excluded with reason.

| Area | Why excluded |
|---|---|
| **Multi-tenancy** | Single-user assumption (§2.2). DRF, capacity scheduling, hierarchical queues are unnecessary. |
| **Cost-aware scheduling** | No cloud bills in target deployment. Architecture leaves a cost-vector seat (§4.1) for future extension. |
| **Energy-aware scheduling** | Out of thesis scope. Could reuse the same objective form. |
| **GPU scheduling** | No GPU support in current cluster. Adding `GPU_OPT` `NodeType` is a future extension. |
| **Cross-cluster federation** | Single cluster only. |
| **Sub-second tasks** | Out of duration regime (§5.4); Sparrow's territory. |
| **Heavy ML training jobs** | Pollux / Tiresias / Gandiva regime. Out of scope. |
| **Opportunistic compute** | Nodes are dedicated (§2.2), not laptops with lids. |
| **CRIU live migration** | Listed for completeness; future work. |
| **Reactive autoscaling** | Cluster size is fixed at boot. |
| **DAG-from-natural-language** | Listed in [Milestones.md](Milestones.md); future work. |
| **Public release / CRD definition** | Internal thesis tool; no Operator pattern. |
| **Multi-node tasks** | A single task spanning multiple nodes (MPI rank-0 across hosts). Our model is one task = one container = one node. Multi-node coordination is expressed via *gangs of single-node tasks* (§5.9). |
| **Real-time bandwidth contention modeling** | The bandwidth matrix (§2.5, §4.8) holds steady-state per-link estimates. Concurrent transfers competing for the same link are not modeled — a single transfer's predicted time uses the static matrix entry. If observed evaluation shows this materially distorts decisions (e.g., many parallel children of a fan-out all fetching from the same parent node), revisit as future work. |
| **Sidecar data plane (Option 3)** | The k8s implementation uses Option 2 (initContainer + per-node fileserver DaemonSet, §2.4). A more elaborate sidecar that streams partial outputs while the producer is still running, or replicates outputs proactively to predicted-future-consumers, is future work. |
| **Multi-replica data placement** | `DataPlacement` (§4.10) optionally records replicas after a transfer, but the scheduler does not actively *choose* to replicate outputs ahead of demand. Proactive replication for fault tolerance or load spreading is future work. |
| **Workflows requiring shared mutable state** | Excluded by the functional contract in §5.5.1. Tasks that share databases, message queues, or in-memory caches across the DAG are outside this scheduler's regime. |
| **Closed-loop thermal physics model** | §2.6 captures thermal effects via a static `cooling_class` declaration plus a `thermal_headroom` bucket dimension. A predictive model that forecasts the *steady-state* temperature of node $n$ if task $\tau$ is added to its current load — and uses that to *forecast* whether the placement will trigger throttling — would replace the bucket-based discounting with a continuous estimate. Out of scope here; future work. |
| **CPU frequency scaling control** | The scheduler observes throttling effects via runtime variance and the thermal bucket but does not actively control CPU frequency, governor mode, or power limits on the nodes. Out of scope. |
| **Cool-down preemption rule** | An explicit "this node is too hot, hold new placements until headroom recovers" mechanism. Not implemented; the bucket-discount approach lets the existing scoring naturally avoid hot nodes when alternatives exist. |

---

## Part XIV — Evaluation Criteria

### 14.1 Workload corpus

| Source | Workflows |
|---|---|
| **Synthetic — shapes** | Linear-3, Linear-10, Diamond-4, FanOut-1×8, FanIn-8×1, Wide-50, Deep-20 |
| **Synthetic — sizes** | Same shapes scaled to small / medium / large task counts |
| **Pegasus (real benchmarks)** | Montage-50, Montage-1000, CyberShake-30, LIGO-50, Epigenomics-50, SIPHT-30 (DAX format → converter in `tools/dax_importer.py`) |
| **Native** | Existing `workflows/3taskworkflow.json` |

### 14.2 Cluster scenarios

| Scenario | Composition |
|---|---|
| **Homogeneous-6** | 6 nodes, all GENERAL, identical specs. Sanity baseline. |
| **Hetero-balanced-6** | 2 CPU_OPT, 2 MEM_OPT, 2 IO_OPT (current default). |
| **Hetero-asymmetric-10** | 1 large CPU_OPT (8 vCPU), 1 medium MEM_OPT (4 vCPU/8 GB), 4 small mixed (1 vCPU each), 2 IO_OPT, 2 GENERAL. Models the home cluster realistically. |
| **Hetero-extreme-12** | 30× speed ratio between fastest and slowest CPU_OPT. Stresses learning. |

### 14.3 Workload modes

For each `(workload, cluster)` pair, run both:

- **Closed batch.** All workflows arrive at $t = 0$. Measure makespan.
- **Open arrival.** Poisson arrivals at $\lambda \in \{0.1, 0.3, 0.5, 0.7, 0.9\} \times \text{capacity}$. Measure throughput, p50/p95/p99 completion time, queue length over time.

### 14.4 Metrics

Per run:

| Metric | Definition |
|---|---|
| **Makespan** | $\max_w(\text{finish}_w) - \min_w(\text{arrival}_w)$ across all workflows in the run. |
| **Mean completion time** | $\text{mean}_w(\text{finish}_w - \text{arrival}_w)$. |
| **p95 / p99 completion time** | Tail. Per priority class. |
| **Throughput** | Workflows finished per second, steady-state portion of run. |
| **Utilization** | Mean of $1 - \text{idle\_fraction}$ across all nodes. |
| **Fairness index** | Jain's index over per-workflow effective rates within priority class. |
| **Eviction rate** | Evictions per minute. |
| **Preemption rate** | Preemptions per minute. |
| **Decision overhead** | p99 wall-clock per `run_tick`. |
| **Exploration count** | Number of "exploratory" placements per task template. |
| **Cold-start convergence** | Number of executions until placement quality is within 10% of oracle. |

### 14.5 Baselines

A run is meaningful only against named baselines. For every metric we report:

1. **Random + first-fit** — sanity floor.
2. **FCFS + first-fit** — industry default.
3. **Current 8-factor scorer** ([services/scheduler.py](services/scheduler.py)) — our "before".
4. **Online EFT + priority** — minimal sensible alternative.
5. **HEFT** — canonical DAG scheduler (literature comparison).
6. *(Optional)* **Min-Min / Sufferage** — heterogeneous batch baseline.
7. *(Optional)* **Quincy/Firmament-style MCMF** — sophisticated alternative.

### 14.6 Statistical methodology

- Each `(workload, cluster, mode, scheduler)` cell is run with $\geq 10$ random seeds.
- Report **mean ± stddev**, never single-run anecdotes.
- Significance tested via paired bootstrap (10000 resamples) at $p < 0.05$.
- Wall-clock and simulation-clock results reported separately.

---

## Part XV — Open Questions & Blockers

These are unresolved at the time of this spec. Each must be answered (or explicitly deferred) before algorithm implementation finalizes.

| ID | Question | Owner | Resolution path |
|---|---|---|---|
| Q1 | What is the exact functional form of $\widehat{\text{ECT}}$ — naive serial sum, full forward projection, or stochastic simulation? | Algorithm spec | Pick in algorithm doc; ablation study in evaluation. |
| Q2 | Default values for hyperparameters $\alpha_p, \lambda_\text{fair}, \lambda_\text{churn}, \lambda_\text{risk}, \gamma$? | Algorithm spec | Initial values from intuition; tuned offline against replay traces. |
| Q3 | How are Pegasus DAX abstract task names mapped to `TaskClass`? | Implementation | Heuristic table in `tools/dax_importer.py`; verify against published Montage profiles. |
| Q4 | What is the exact contention model — $\mu_{\tau,n}(A) = \mu^\text{base}(1 + \gamma \cdot c)$ where $c$ counts same-class running tasks? Or something more nuanced (CPU vs memory contention separate)? | Algorithm spec | Start simple; instrument observation collection so we can refit later. |
| Q5 | Should `failures_by_node` decay be **rolling-window** (consistent with observations) or **exponential** (smoother)? | Algorithm spec | Start with exponential ($\beta = 0.95/\text{hour}$); revisit if it under-reacts. |
| Q6 | What constitutes "convergence" in §1.3 — within 10% of oracle for 5 consecutive runs? Within 5%? | Evaluation methodology | Set in evaluation chapter. |
| Q7 | Is replay-based weight tuning (CMA-ES over historical traces) in-scope for the thesis, or noted as future work? | Scope decision | Defer to "stretch" phase; mention in conclusion. |
| Q8 | How is workflow-level `vruntime` accumulated — wall-clock CPU-seconds consumed, or normalized by node speed? | Algorithm spec | Normalized: a second on a fast node "counts more" than a second on a slow node. |
| Q9 | When checkpoint+resume preemption is implemented (later phase), what's the storage backend for checkpoint blobs? | Future implementation | Out of this spec. |
| Q10 | Do we need a "minimum runtime threshold" before a task is preemption-eligible (to avoid preempting a task that just started)? | Algorithm spec | Yes, default 5s; tuned later. |
| Q11 | How is per-tick *search* time-budgeted — fixed wall-clock cap, fixed iteration count, or anytime with deadline? | Algorithm spec | Anytime with 50% of $T_\text{tick}$ deadline. |
| Q12 | Is the seed for randomized tie-breaking exposed for reproducibility? | Implementation | Yes, configurable per run. |
| Q13 | Gang scheduling — strict atomic coscheduling (§5.9), or weaker "as-many-as-fit" semantics? | Algorithm spec | Strict atomicity (H8); revisit only if it produces excessive holds in evaluation. |
| Q14 | Proactive defragmentation cadence $T_\text{defrag}$ and gain threshold $\eta$ — what defaults? | Algorithm spec | Start at $T_\text{defrag} = 30s$, $\eta = 0.05$; tune offline. |
| Q15 | What happens when a gang is structurally infeasible (cluster cannot fit all members even with full preemption)? Fail fast, or hold indefinitely with timeout? | Algorithm spec | Fail fast with `INFEASIBLE-GANG`; per §7.3. Hold-with-timeout would create unbounded queue growth. |
| Q16 | Should H9 ("no exclusive priority") be treated as a property *derived* from a correctly-tuned $J$ (§8.6) or as a hard guard always enforced separately? | Algorithm spec | Both. The objective should make idle-with-work strictly suboptimal; H9 is a defensive check. |
| Q17 | If observed evaluations show parallel children fetching from the same producer materially distort runtime predictions (real-time bandwidth contention), do we add per-link concurrent-transfer modeling to the cost function? | Future / scope | Out of scope for this thesis; flag in evaluation if observed. The static `BandwidthMatrix` is the contract. |
| Q18 | Are the default thermal-headroom bucket thresholds (LOW <10°C, MEDIUM 10–25°C, HIGH >25°C) right for typical home-cluster hardware, or should they be tuned per node? | Algorithm spec / evaluation | Start with the defaults; if buckets prove too coarse / fine on home-cluster runs, allow per-node override in the scenario file. |
| Q19 | When a node lacks a temperature sensor (`cpu_temperature = None`), should the scheduler refuse to place long CPU_BOUND tasks on a `PASSIVE`-cooling node, or just rely on learned variance to do that implicitly? | Algorithm spec | Implicit by default — a `PASSIVE` node will accumulate slow observations under sustained load and the bucket-less median will reflect it. Add an explicit hard guard only if evaluation shows the implicit signal is too slow to react. |

---

## Part XVI — Glossary & Notation

### 16.1 Notation

| Symbol | Meaning |
|---|---|
| $N$ | Set of nodes; $\lvert N \rvert$ is node count. |
| $n \in N$ | A node. |
| $W$ | Set of currently-admitted workflows. |
| $w \in W$ | A workflow instance. |
| $G_w$ | DAG of workflow $w$. |
| $\tau$ | A task instance. |
| $Q$ | Set of ready (DAG-eligible, queued) tasks. |
| $R$ | Set of currently-running tasks. |
| $\mathbf{c}_n = (cpu_n, mem_n)$ | Node $n$'s capacity vector. |
| $\mathbf{c}_\tau$ | Task $\tau$'s resource request vector. |
| $\mu_{\tau,n}$ | Median predicted runtime of $\tau$ on $n$. |
| $\sigma^2_{\tau,n}$ | Predicted runtime variance. |
| $A$ | Action set output by scheduler at a tick. |
| $\widehat{\text{ECT}}(w \mid A)$ | Expected completion time of $w$ if $A$ is applied. |
| $T_\text{tick}$ | Scheduler tick period (default 1s). |
| $p(w)$ | Priority class of workflow $w$. |
| $\alpha_p$ | Weight on priority-$p$ workflows in $J$. |
| $\lambda_\text{fair}, \lambda_\text{churn}, \lambda_\text{risk}$ | Composite-objective weights. |
| $\gamma$ | Same-class contention penalty. |
| $\beta$ | UCB exploration constant. |
| $\rho$ | Speculative-execution trigger ratio. |
| $K, M$ | Preemption budget caps (per-node, per-task). |

### 16.2 Glossary

| Term | Meaning |
|---|---|
| **Workflow** | A user-submitted DAG of tasks with shared priority and (optionally) deadline. |
| **Task** | A single containerized unit of work; a node in a workflow's DAG. |
| **Task template** | The static definition (image, requests, class). Many instances share one template. |
| **Task instance** | A specific execution of a template. |
| **Node** | A worker machine in the cluster. |
| **Tick** | The periodic decision cycle of the scheduler. |
| **Placement** | The act of starting a queued task on a node. |
| **Preemption** | The act of stopping a running task before completion. |
| **Eviction** | A k8s-initiated termination due to resource pressure; **not** the scheduler's fault. |
| **Failure** | A task ending in a non-zero state due to internal error (OOM, crash, timeout). |
| **Critical path** | Longest expected-time path from a task to a workflow's exit. |
| **Upward rank** | A task's distance-to-exit cost in the DAG; higher = earlier scheduling. |
| **vruntime** | Per-workflow accumulated cluster-time; used for fairness. |
| **Cold start** | First execution of a task template on a node; no profile data. |
| **Steady state** | Regime after enough executions per template that profiles are stable. |
| **ECT** | Expected Completion Time of a workflow under a given placement plan. |
| **Drift** | Detected divergence between recent and historical runtime medians for a (task, node) pair. |
| **Backfilling** | Allowing a lower-priority task to use slack between now and a higher-priority task's reservation. |
| **Anti-affinity** | A constraint or penalty preventing similar tasks (same `TaskClass`) from co-locating on a node. |
| **Gang** | A set of READY tasks within a single workflow that share a `gang_group_id` and must be placed atomically — all at the same tick or none. |
| **Defragmentation** | Active rearrangement of running tasks (via preemption + re-placement) to consolidate scattered free capacity into placeable contiguous blocks. |
| **EASY backfill** | Reservation-based scheduling: when the head-of-line task can't fit, reserve its next-available slot and allow only smaller, shorter tasks to fill the gap. |
| **Image locality** | The condition where a task's container image is already cached on a candidate node. One of two locality factors in this scheduler (the other is data locality). |
| **Producer-local storage** | The storage model adopted in §2.4: each task writes its outputs to the local disk of the executing node, not to a shared filesystem. Consumers fetch across the network when not co-located. |
| **Bandwidth matrix** | A scenario-level table (§4.8) mapping each ordered pair of node ids to the estimated bytes/sec achievable when transferring data between them. Populated by the probe DaemonSet (§2.5). |
| **Transfer cost** | The predicted time to move a parent task's output from its producer node to a candidate consumer node: `output_bytes / BandwidthMatrix[(producer, consumer)]`. Folded directly into ECT (§8.5). |
| **Data locality** | The condition where a task's required parent outputs are already resident on a candidate node (transfer cost = 0 for those outputs). Expressed via the transfer term in ECT, not as a separate score. |
| **Fileserver DaemonSet** | The per-node HTTP server (one pod per node) that exposes the local `hostPath` storage so other nodes' initContainers can fetch parent outputs. The k8s mechanism behind §2.4. |
| **Functional task contract** | The set of invariants in §5.5.1 (pure inputs/outputs, no shared mutable state, immutable inputs, value-not-slot semantics) that make producer-local storage sound without a distributed filesystem. |
| **DataPlacement** | The runtime registry (§4.10) tracking which node holds each produced output. Read by the scheduler when scoring placements; GC'd on workflow completion. |
| **Thermal throttling** | A CPU's hardware-enforced clock-down when junction temperature reaches `thermal_throttle_temp_c`. Manifests as runtime inflation (typical 30–50%) until the chip cools. Not modeled as a hard failure; absorbed by the learning model via the thermal bucket (§2.6, §11.3). |
| **Cooling class** | A static per-node attribute (`PASSIVE`/`STANDARD`/`HIGH`/`EXTREME`) describing the heat-dissipation capability of the machine. Declared in the cluster scenario; encodes information that current temperature alone cannot — e.g., a 70°C node with `HIGH` cooling is more stable than a 50°C node with `PASSIVE` cooling. |
| **Thermal headroom** | The basic per-node thermal placement signal: `thermal_throttle_temp_c − cpu_temperature`. How many degrees the node can absorb before throttling. Bucketed (LOW/MEDIUM/HIGH) and used as an optional 3rd dimension of the contextual learning model (§11.3). |

---

## Sign-off Checklist

Before moving to the algorithm specification, the following must be confirmed:

- [ ] §1.2 captures the right primary/secondary/tertiary objectives.
- [ ] §2.1 home-cluster ranges (2–30 nodes, 2–30× heterogeneity) are accurate.
- [ ] §4 entity model — all current fields preserved, all *(new)* additions accepted (incl. `expected_output_bytes`, `bandwidth_matrix`, §4.10 `DataPlacement`).
- [ ] §2.4 producer-local storage model + fileserver DaemonSet + initContainer transfer mechanism is the intended deployment shape.
- [ ] §2.5 bandwidth-probe cadence ($T_\text{bw} = 600s$, 100 MB blob) is acceptable.
- [ ] §2.6 thermal model — cooling-class declaration + headroom bucket; closed-loop thermal physics deferred — matches user's intent.
- [ ] §5.1 dual-mode evaluation (closed batch + open arrival) is in scope.
- [ ] §5.5 data locality as a transfer-cost term inside ECT (not a separate score) is the intended treatment.
- [ ] §5.5.1 functional task contract — pure inputs/outputs, no shared mutable state — is acceptable as a hard pre-condition for any submitted workflow.
- [ ] §5.6 priority semantics — strict-across-class, free-within-class — is the intended contract.
- [ ] §5.8 multi-workflow concurrency rules — "priority is preference, not exclusivity" — match user's intent.
- [ ] §5.9 gang scheduling is in scope; multi-node tasks are not.
- [ ] §7 hard constraints H1–H9 are complete and correct.
- [ ] §8.4 trade-offs — explicitly accepted degradations — are acceptable to user.
- [ ] §9 failure taxonomy is complete; no missing failure modes.
- [ ] §10 preemption modes — kill+restart now, checkpoint+resume later — match user's answer.
- [ ] §11 learning model — contextual buckets, UCB, drift detection, separate eviction counter — is the intended direction.
- [ ] §13 out-of-scope list is complete; no missing exclusions.
- [ ] §14 evaluation matrix is feasible given thesis time budget.
- [ ] §15 open questions are tracked; each has an owner.

Once signed off, this document **freezes the problem** and the algorithm specification document derives the scheduler from it.
