# TaskScheduler Engineering Journal

This journal explains, step by step, how the TaskScheduler project reached
its current state: what was built, what was measured, what went wrong, how
each mistake was found and fixed, and why one design was chosen over another.
It is written for a reader who wants to *understand* the project, not only
use it, so most sections contain a worked example with real numbers taken
from the retained artifacts.

The companion documents are:

- `thesis/thesis.pdf` - the formal write-up (introduction, state of the art,
  architecture, evaluation, conclusions);
- `README.md` - how to run everything;
- `ARCHITECTURE.md` - the component map;
- `results/final-evidence-synthesis.md` and `results/kind-validation/summary.md`
  - generated evidence reports from which every number in this journal is
  taken.

Evidence snapshot: 2026-10-03 (correction round). Everything produced before
the correction round is preserved in
`results/archive/pre-correction-2026-10-03.zip` and in git history.

---

## Contents

1. How to read this journal
2. The project in one page
3. Inherited history (phases A-I) and what survived
4. Phase J - the audit
5. Phase K - correcting the simulator
6. Phase L - HEFT-RA, a contention-aware list scheduler
7. Phase M - re-running the simulation evidence
8. Phase N - rebuilding the live emulation
9. Phase O - live Kubernetes validation and the simulator twin
10. Recommendations: which algorithm for which workflow and cluster
11. Claims withdrawn or corrected
12. Design choices that were considered and rejected
13. Evidence map
14. Open work
15. Glossary

---

## 1. How to read this journal

### 1.1 Three levels of evidence

Every claim in this project belongs to exactly one evidence level, and a
claim is only as strong as the weakest level that supports it.

| Level | What it is | What it can prove |
|---|---|---|
| 1. Controlled simulation | Deterministic discrete-event runs of declared cluster and workflow models (`simulation/`). | Comparative policy behaviour under the stated model assumptions. |
| 2. Same-host Kubernetes | Real CRDs, controller, custom scheduler, Pods, kubelet, cgroups and `tc netem` links in Kind containers on one PC. | That the control plane works end to end, and whether the simulator's predictions hold for a real (emulated) system. |
| 3. Physical cluster | k3s on separate machines (scripts exist in `scripts/`). | Not executed. No result in this project is a physical-cluster result. |

### 1.2 The vocabulary used everywhere

- **Makespan** - time from a workflow's submission until its last task
  finishes.
- **Regret** - how much worse a policy is than the best *deployable* policy
  on the same case: `makespan / best_makespan - 1`. A regret of 0% means
  the policy was the best (or tied for best) on that case. The exhaustive
  static oracle is reported separately and never enters this denominator.
- **Case** - one (cluster, workflow, seed) combination for closed DAGs, or
  one (cluster, workload, seed) combination for overlapping workloads.
- **P95 regret** - the 95th percentile of regret over cases. It measures
  the bad tail: "how badly does this policy fail in its worst 5% of cases?"
- **Win/tie rate** - share of cases where the policy had zero regret.
- **Exploratory / confirmatory / holdout** - three disjoint seed ranges.
  Exploratory seeds (0-19) were used for development; confirmatory seeds
  (200-249) check that conclusions repeat; holdout seeds (400-449) were
  never looked at before every design choice was frozen.

### 1.3 Where things live

| Path | Content |
|---|---|
| `simulation/` | Discrete-event simulator, policies, estimators, experiment runners, Kind twin. |
| `scenarios/` | Cluster, workflow, workload, and policy-profile definitions (YAML). |
| `controlplane/`, `services/`, `models/`, `engine.py` | Live Kubernetes controller and scheduler. |
| `tasks/`, `images/` | Task images and node DaemonSets (fileserver, bandwidth probe, thermal). |
| `crds/`, `k8s/`, `kustomization.yaml` | Kubernetes manifests. |
| `clusters/kind_profiles.yaml`, `kind_lab.py` | Emulated Kind cluster profiles and the tool that builds them. |
| `experiments/k8s/` | Live task templates and workflow batches. |
| `run_experiment_lab.py`, `analyze_final_evidence.py` | Simulation matrices and their synthesis. |
| `run_k8s_experiment.py`, `kind_twin_predict.py`, `analyze_kind_validation.py` | Live harness, twin predictions, validation report. |
| `results/` | Retained evidence (see section 13). |
| `tests/` | Unit, property, and regression tests. |

---

## 2. The project in one page

**The question.** A workflow is a directed acyclic graph (DAG) of tasks:
each task needs CPU, memory bandwidth, storage, and the outputs of its
parents. A heterogeneous cluster has nodes that are good at different
things (a fast CPU here, a wide memory bus there, a slow edge device behind
a weak Wi-Fi link) and that change over time (they heat up and throttle,
they fail). The scheduler must decide, repeatedly, *which ready task runs
next and on which node*. The project asks which decision rule works best,
for which kind of workflow and which kind of cluster, and whether the
answer survives contact with a real Kubernetes control plane.

**Two execution surfaces.**

```
                    scenarios/*.yaml (clusters, workflows, workloads)
                                   |
             +---------------------+---------------------+
             |                                           |
   Simulator (level 1)                        Kubernetes (level 2)
   simulation/engine.py                       Workflow + TaskTemplate CRDs
   - fluid CPU/memory/storage sharing         controller: DAG readiness, Pods,
   - routed, contended network flows            status, retries
   - RC thermal model, throttling             scheduler: ready-Pod ordering,
   - failures, repairs, checkpoints             placement, binding, preemption
   - gangs, priorities, preemption            node DaemonSets: fileserver,
   - 11 policies + exhaustive oracle            bandwidth probe, thermal
             |                                           |
             +--------- simulation/kind_twin.py ---------+
                 (calibrated model of the Kind cluster,
                  used to check that the simulator's
                  predictions hold on the live system)
```

**The policies.**

| Name | What it does | Why it is in the study |
|---|---|---|
| `fcfs` | First ready task to the first node that fits. | Simplest control. |
| `random` | Seeded random feasible node. | Lower control. |
| `k8s-default` | Imitates kube-scheduler: PrioritySort queue, LeastAllocated + BalancedAllocation + ImageLocality scores. | What a stock cluster would do. |
| `eft` | Earliest estimated finish time, tasks in arrival order. | Classic greedy list scheduler. |
| `heft` | EFT placement, tasks ordered by HEFT upward rank (Topcuoglu et al., 2002). | The standard heterogeneous DAG baseline. |
| `heft-ra` | HEFT with contention-, thermal- and failure-aware estimates and one-level lookahead. **New in the correction round.** | Fixes HEFT's measured failure modes (section 6). |
| `heft-ra-cap` | `heft-ra` whose lookahead only counts on capacity the children will actually find. **Added after the first live run.** | Fixes a lookahead weakness that only slot-limited (live) nodes exposed (section 9.4). |
| `legacy` | The project's historical simulator heuristic. | Continuity with old results. |
| `legacy-v1` | The exact eight-factor live scorer with its original weights. | What the original live scheduler did. |
| `legacy-tuned` | Same scorer with weights found by a seeded search. | Was the original scorer just badly tuned? |
| `adaptive` | HEFT order plus priority classes, energy/transfer/thermal/failure penalties, and preemption. | The project's earlier "smart" policy. |
| `adaptive-no-preempt` | `adaptive` with preemption disabled. | Isolates the effect of preemption. |
| `oracle-static` | Exhaustive search over static assignments (small DAGs only). | A reference point, never a competitor. |

**The headline result (details in sections 7-10).** On a fresh, untouched
holdout, HEFT-RA has 0.37% mean and 0.00% P95 makespan regret on closed
DAGs, against 12.42% and 49.02% for HEFT and 160.69% for the stock
Kubernetes scoring. With the capacity-aware lookahead added after the
first live trials (`heft-ra-cap`), it also leads the overlapping
workloads on median and P95 response regret and deadline hit rate. On
live Kind clusters, a HEFT-RA variant was the fastest of nine policies for
four concurrent DAGs in 8 of 9 runs (makespan 0.917 of HEFT's), for the
same reason as in simulation. Adaptive (priority ordering plus
preemption) is the policy for bounding the worst case of urgent work: its
P95 urgent response is 21 s against 127 s for HEFT-RA, while HEFT-RA is
faster for the urgent workflow in the typical case.

---

## 3. Inherited history (phases A-I) and what survived

The project was built over several earlier sessions before the correction
round. This section summarises each phase and then states plainly what the
audit later found about it. The original, longer narrative is in the
archived journal.

| Phase | What was done | What held up | What did not |
|---|---|---|---|
| A. Direct Pod runs | A custom scheduler bound three synthetic task containers; `results/run1.txt`, `run2.txt`, `run34.txt`. | Binding through the API works. | No baseline, no DAG model, no protocol; `run34` failures came from an overloaded worker, not from the algorithm. |
| B. Simulator | Validated YAML schemas; discrete-event engine with CPU, cache, storage, network, thermal, failures, checkpoints, gangs, priorities; FCFS/Random/EFT/HEFT/Legacy/Adaptive. | The architecture and most models. | Several modelling defects (section 4): half-duplex links everywhere, no TCP loss ceiling, SMT ignored at saturation, failure randomness coupled to policy decisions. |
| C. First matrices | 5 clusters x 15 workflows x 20 seeds; 4 overlapping workloads. | The scenario corpus. | Most seeds were identical repeats (section 7.2); the lower bound used for sanity checks was not a bound. |
| D. Kubernetes control plane | Workflow/TaskTemplate CRDs, controller, scheduler adapter, producer-local data plane, DaemonSets. | Overall split of responsibilities. | Double dispatch under preemption, refresh overcommit, bind failures recorded as successes, Pod name aliasing, missing DAG information in the scheduler (section 4). |
| E. Legacy audit | Exact adapter of the live eight-factor scorer for the simulator. | Correct adapter after a wall-clock fix. | Branch-and-bound gang placement assumed non-negative scores, which the scorer violates. |
| F. Weight search | 256-candidate seeded search with train/validation/holdout splits. | The protocol; the selected vector is re-selected after the fixes. | Profiles were built from the unperturbed cluster instead of the sampled one. |
| G-H. Confirmation | Fresh seeds, factor ablation, Kind topology runs. | Factor study design. | Live heterogeneity was not emulated (every Pod ran at the same speed), so the Kind runs could not test placement quality. |
| I. Contextual search | 160 generated workflows, 64 Adaptive candidates, per-context policy portfolio. | Protocol and guardrails. | A `KeyError` for default-only profiles; conclusions depended on HEFT, which turned out to be contention-blind. |

The most important lesson of this table: **most of the code was sound in
shape but wrong in details that changed results.** None of the defects was
visible in aggregate tables; each was found by reading a specific code path
against a specific trace.

---

## 4. Phase J - the audit

### 4.1 How the audit was run

The user's first request was simply: *analyse this entire workspace and spot
any implementation or logic mistakes.* The audit was split into five
independent reviews (live harness and Kind tooling; legacy root scripts;
`services/`; `controlplane/`; simulator and analysis), plus a direct
reading of the simulator and the live emulation. Each reported finding was
then **verified against the code or by a reproduction** before being
accepted. 53 findings were recorded; the ones below changed behaviour or
results. Every fix in sections 4 and 5 has a regression test
(`tests/test_simulation_regressions.py`, `tests/test_live_policy_regressions.py`,
`tests/test_k8s_controller.py`, `tests/test_scheduler_engine_preemption.py`,
`tests/test_k8s_cluster.py`, `tests/test_legacy_entry_points.py`, and others).

### 4.2 Findings that changed behaviour

| ID | Severity | Where | Defect | Consequence |
|---|---|---|---|---|
| J1 | High | `simulation/experiments.py` | The "lower bound" was not a lower bound. | 350 of 26,250 old confirmatory records had makespan below their "bound". |
| J2 | High | `engine.py`, live scheduler | A preempted task could be dispatched again while its old Pod was still being deleted. | Two Pods for one task; capacity counted twice. |
| J3 | High | `services/k8s_cluster.py` | A periodic refresh restored a node's full free capacity while keeping its active tasks. | The scheduler overcommitted nodes after every refresh. |
| J4 | High | `controlplane/scheduler/main.py`, `k8s_scheduler.py` | A failed bind was logged and then treated as success. | The Pod was forgotten and the workflow hung. |
| J5 | High | `controlplane/controller/main.py` | Pod names were truncated to 63 characters. | Two different long task names could map to one Pod name. |
| J6 | High | `services/policy.py`, `services/preemption.py` | Gangs were scheduled before higher-priority tasks; the per-node preemption cap was checked per victim, not per plan. | Priority inversion; more victims than the budget allowed. |
| J7 | High | live emulation | Every task template ran at the same per-Pod speed on every node. | Live "heterogeneity" was only slot counts and links; learned profiles could not discover specialisation. |
| J8 | High | live scheduler | No DAG information reached the live scheduler (all upward ranks were 0), and HEFT had never been run live. | The simulator's winner was never validated. |
| J9 | Medium | `simulation/engine.py` | One random stream served failures, jitter, and transfers. | A policy's placements changed the failure times it later suffered, so policies were compared under different failures. |
| J10 | Medium | `simulation/policies.py`, `legacy_weights.py` | Branch-and-bound gang placement pruned on partial scores, but the Legacy scorer can be negative. | Optimal gang placements could be pruned away. |
| J11 | Medium | `simulation/engine.py` | When a node failed, only the interrupted gang member restarted. | A gang continued with a missing member. |
| J12 | Medium | `simulation/resources.py` | Route lookup used `functools.lru_cache` on a method. | Every topology instance was kept alive forever (memory leak), and estimators rebuilt topologies on every call. |
| J13 | Medium | simulator schema and workloads | `bool("false")` is `True`; priority `BEST_EFFORT` has value 0 and was treated as "missing". | String booleans and best-effort priorities silently flipped. |
| J14 | Medium | `services/ect.py`, `services/policy.py` | Workflow ECT summed roots instead of sinks; Adaptive treated multi-slot nodes as single-server queues and omitted startup and transfer from occupancy. | Live estimates were biased. |
| J15 | Medium | `models/profile_store.py` | Loading replayed raw observations over the persisted EMA. | Learned profiles drifted after every restart. |
| J16 | Medium | live measurements | Runtimes came from kubelet timestamps (1 s resolution) for 2-5 s tasks; workflow response used `creationTimestamp` and a 2 s reconcile tick. | Live timings had up to 50% quantisation error. |
| J17 | Medium | `controlplane/scheduler/main.py` | Tick, Pod watch, completion watch, and refresh mutated shared state without a common lock. | Races between placement and completion. |
| J18 | Medium | `analyze_kind_topologies.py` | The `tc qdisc` integrity check accepted any observed qdisc; trial validation checked only totals. | Wrongly shaped links or duplicate binds could pass. |
| J19 | Low | tooling | `verify-cluster.sh` read `.status.phase` (CRD uses `.status.state`) and `bw-to-*` (probe writes `bw-from-*`); k3s scripts lost environment variables through `sudo`; Python 3.9 image with PEP 604 annotations; `GET /profiles` returned HTTP 500; `submit_workflows.py` treated a timed-out parent as success. | Tools that silently did nothing or crashed. |

### 4.3 Worked examples

**J1 - a lower bound that was not a bound.** A lower bound must never
exceed an achievable makespan, otherwise "makespan / bound" cannot be used
as a sanity check. The old bound took the maximum of the critical path and
four aggregate terms (CPU, memory, disk read, disk write). Three of its
inputs were too pessimistic:

1. it charged *all* memory traffic to DRAM, but the engine serves the
   cache-resident part of a working set from L3 (a 192 MiB working set with
   high reuse on a node with a 32 MiB L3 still hits the cache for a large
   fraction of its accesses);
2. its CPU capacity ignored simultaneous multithreading, which the engine
   models as `smt_efficiency` extra throughput per core;
3. it evaluated execution at the node's initial temperature, which can be
   above the throttle point after uncertainty sampling.

Each makes the "bound" larger than what the engine can actually achieve.
The fixed `workflow_lower_bound_s` uses the most favourable value of every
term (unthrottled CPUs, private L3 for the DRAM estimate, SMT-aware
capacity). Regression tests check the bound against EFT, HEFT and FCFS
schedules on representative cluster/workflow pairs, and section 7 checks
every retained record.

**J2 - double dispatch under preemption.** In the live engine, a preempted
task went straight back to `READY`. On the next tick it could be placed
again while the victim Pod was still terminating, so for a few seconds two
Pods existed for one task and the node's capacity was counted twice. The
fix adds an explicit `TaskState.PREEMPTED`: the task is parked until the
controller, having observed the deletion, creates a fresh Pod, and only
that new Pod re-enters the scheduling queue.

**J3 - refresh overcommit.** The scheduler keeps *logical* reservations
(what it has decided) while the cluster refresh reads *physical* Pods from
the API. The old refresh replaced node objects, restoring their full free
capacity, but kept the active-task dictionary. After every refresh the
scheduler believed every node was empty and placed more work on it. The
fix (`poll_cluster_inventory` + `merge_cluster_state`) merges observed Pods
into the logical state and never forgets a reservation that has not yet
appeared in the API.

**J4 - a failed bind recorded as success.** A bind can fail with HTTP 409
(already bound) or 422 (node gone). The old code logged the error, marked
the task running, and removed the Pod from the pending set. The task never
ran and its workflow never finished. The fix makes binding a callback with
three outcomes (`BIND_OK`, `BIND_RETRY`, `BIND_DROP`) and rolls back the
reservation on anything but success.

**J9 - failures that depended on the policy.** With one shared random
generator, the number of random draws made for transfers and jitter
depended on where a policy placed tasks; the next failure time was then
drawn from a different position of the stream. Two policies on the same
seed therefore experienced different failures, which is not a paired
comparison. The fix derives an independent stream per purpose and node
(`derived_rng`, SHA-256 of the seed and a label), so failures depend only
on the seed and the node.

**J7 and J8 - the old live experiments could not answer the question.**
Every validation Pod requested 500m CPU and ran the same amount of work, so
a "fast CPU node" and a "slow edge node" executed a task in the same time.
Only the number of slots and the links differed. In addition, the live
scheduler never received the workflow graph, so HEFT's upward ranks were
all zero and HEFT itself had never been run live. The old Kind results were
therefore integration evidence, not evidence about placement quality. They
are withdrawn as comparative evidence (section 11) and the live emulation
was rebuilt (section 8).

---

## 5. Phase K - correcting the simulator

The second request was to "redo everything the previous AI did wrong" and to
make the test environment "as accurate as possible" while staying cheap
enough to simulate many cluster types. That raised a design question first.

### 5.1 Why a fluid discrete-event simulator, not VMs or containers

Three ways of emulating many heterogeneous computers on one laptop were
considered:

| Option | Fidelity | Cost on an i5-8300H / 12 GB | Verdict |
|---|---|---|---|
| Virtual machines per node | Real kernels, but all VMs share the same four cores, so "a fast node" cannot be faster than the host. | 1-2 GB RAM per VM; a handful of nodes at most. | Rejected (the user also asked for no VMs). |
| Containers with cgroup limits (Kind) | Real Kubernetes behaviour; heterogeneity must be *emulated* (CPU quotas, `tc netem`, work scaling). | About 0.5 GB per node; 4-6 nodes. | Used for validation (level 2). |
| Fluid discrete-event simulation | Models resource sharing by equations; any hardware can be described. | Milliseconds per run; thousands of runs per minute. | Used for the broad comparison (level 1). |

The simulator is the same family of model used by SimGrid (Casanova et
al., 2014): tasks and transfers are *fluid* activities whose rates are
recomputed whenever something starts or finishes, and the engine jumps
from event to event. Velho et al. (2013) showed that such flow-level TCP
models are accurate for bulk transfers when contention and RTT effects are
modelled, which is exactly what the corrections below add.

### 5.2 What a simulated node is made of

Each node in `scenarios/clusters/*.yaml` declares:

- **CPU**: architecture, physical cores, threads per core, frequencies,
  calibrated GFLOP/s per core, L3 size, idle and maximum power, and SMT
  efficiency (extra throughput from a second hardware thread);
- **memory**: capacity, calibrated bandwidth, latency, channels;
- **storage**: sequential and random read/write rates;
- **thermal**: ambient, initial, throttle and shutdown temperatures,
  thermal resistance and capacitance, and the minimum throttle factor;
- **reliability**: optional Poisson failure rate, scripted failure times,
  and mean repair time;
- **background load**: CPU fraction and memory used by the OS and daemons.

Links declare bandwidth, latency, jitter, loss, medium (`ethernet`,
`wifi`, `wan`) and, after the correction round, an optional reverse
bandwidth/latency and duplex mode. The local profile
`scenarios/clusters/local-i5-8300h.yaml` describes the development laptop
itself and is the only profile with a large declared calibration
uncertainty (25%). It was derived from the Windows hardware inventory
(`Get-CimInstance Win32_Processor`, `Win32_PhysicalMemory`,
`Win32_DiskDrive`):

| Component | Inventory | Profile value and reasoning |
|---|---|---|
| CPU | Intel Core i5-8300H, 4 cores / 8 threads, 2.3 GHz base (4.0 GHz turbo), 1 MiB L2, 8 MiB L3 | 4 physical cores, 2 threads per core, 8 MiB L3, SMT efficiency 0.28 (a second hardware thread adds roughly a quarter of a core on throughput code) |
| Memory | 12 GiB as 4 GiB + 8 GiB DDR4 modules running at 2133 MT/s | 18,000 MiB/s: two channels at 2133 MT/s give about 34 GB/s in theory, but unequal modules run partly single-channel (flex mode) and sustained streaming reaches only part of the peak |
| Storage | Samsung 970 EVO Plus NVMe SSD (500 GB) and a 1 TB laptop HDD | NVMe values below the datasheet (2,500 / 1,800 MiB/s sequential) to account for a full drive and thermal limits |
| Power and heat | 45 W TDP laptop part with a passive-class cooling model | 8 W idle, 45 W maximum, throttling at 95 C |
| OS | Windows 11 Pro, Docker Desktop on WSL2 | 8% background CPU, 1.5 GiB background memory |

The task model (`TaskSpec`) declares compute work (GFLOP), parallel
fraction (Amdahl), memory traffic and working set with cache reuse, disk
reads and writes with a random-I/O fraction, image name and size, startup
time, supported architectures, CPU and memory requests, priority,
deadline, checkpoint interval, and gang membership.

### 5.3 The corrections, one by one

**K1. Links are directional; Wi-Fi is half-duplex.** Before, every link was
one shared channel, so traffic in opposite directions competed even on
full-duplex Ethernet. Now each hop maps to a *channel*: wired and WAN links
have one channel per direction, while 802.11 links share one channel
between both directions because only one station transmits at a time.
Example: two 1 GiB transfers in opposite directions over a 119 MiB/s
Ethernet link now take roughly 9 s each instead of roughly 18 s.

**K2. TCP cannot fill a lossy, long link.** Before, packet loss only scaled
capacity by `1 - p`, so 1% loss cost 1% of throughput. Real TCP reacts to
every loss event by halving its window; Mathis et al. (1997) derived the
steady-state bound `rate <= MSS * sqrt(3/2) / (RTT * sqrt(p))`. Each flow is
now limited by max-min fair sharing on its channels, by its receive window
divided by the RTT, and by the Mathis bound. Example: the balanced edge
node (3.5 ms + 0.2 ms one way, so RTT 7.4 ms, loss 0.05%) now gets about
10.2 MiB/s per TCP flow on its 60 MiB/s Wi-Fi link, so a single transfer
cannot saturate it but six parallel ones can.

The scenario loss values had been chosen under the old `1 - p` model, where
they were almost harmless (edge-3 had 1%, the WAN uplink 0.4%). Under the
Mathis bound, those values would make the asymmetric-edge path deliver
about 0.36 MiB/s per flow, an unrealistic collapse for links that are
described as working Wi-Fi. They were therefore re-interpreted as TCP
*loss-event* rates after link-layer retransmission (which 802.11 performs)
and recalibrated: edge-1 0.003 to 0.0005, edge-2 0.006 to 0.001, edge-3
0.01 to 0.002, WAN uplink 0.004 to 0.0005, balanced edge 0.002 to 0.0005,
unreliable flaky link 0.004 to 0.001. The edge-3 to core path still
reaches only about 0.84 MiB/s per flow (RTT 40.3 ms), which is the point of
that cluster. This is a Reno-style bound; CUBIC would be somewhat faster,
which is recorded as a limitation.

**K3. SMT counts at saturation.** The per-task rate already used SMT
efficiency, but the node-wide ceiling was `physical cores x per-core
GFLOP/s`, so when all hardware threads were busy the SMT gain disappeared.
The ceiling now includes it (`node_cpu_capacity_gflops`).

**K4. Optional per-task DRAM and storage caps.** One core cannot use all
of a node's memory bandwidth, and one I/O stream rarely saturates an NVMe
drive. `memory.per_task_bandwidth_mib_s` and
`storage.per_task_read_mib_s` / `per_task_write_mib_s` express this. They
are optional and unset in the main corpus (so the corpus results do not
depend on guessed values), but they are available for calibrated profiles.

**K5. Control-plane delay.** A real cluster needs time between "task ready"
and "Pod can be placed" (controller reconcile, Pod creation, watch
latency). `cluster.control_plane_delay_s` models it; it is 0 for the
abstract scenarios and calibrated for the Kind twin (section 9).

**K6. Independent random streams** (fix for J9): `derived_rng(seed, label)`
gives each node its own failure stream and each transfer its own jitter
stream.

**K7. Gangs fail together** (fix for J11): a failure that interrupts one
gang member restarts the whole gang, because a gang is defined as "all
members run simultaneously or none".

**K8. Guaranteed progress.** For large virtual times, `now + 1e-9` can be
equal to `now` in floating point, which once produced a loop that never
advanced. `_future(dt)` now returns the next representable time strictly
after `now`.

**K9. Small but result-changing parsing fixes** (J13): `parse_bool` for
YAML strings, `priority is not None` instead of truthiness.

### 5.4 How the corrections were validated

- Unit tests for every model (`tests/test_simulation_resources.py`,
  `test_simulation_engine.py`, `test_simulation_failures.py`,
  `test_simulation_properties.py` with Hypothesis property tests, and
  `test_simulation_regressions.py`).
- Property tests (Hypothesis): network allocations never exceed the
  capacity of any shared channel; bounded allocations respect both the
  capacity and every consumer's own limit; RC temperatures stay between
  the initial and steady-state values; a seed replays jitter, failures,
  placements and metrics exactly.
- Re-running the weight search after the fixes selected the *same* tuned
  Legacy vector, which shows that the corrections did not change what the
  search considers good, only the magnitudes.

---

## 6. Phase L - HEFT-RA, a contention-aware list scheduler

### 6.1 The observation that started it

While re-running the corrected matrices, one case stood out. On the
balanced heterogeneous cluster, the `fanout-1to8` workflow (one I/O-bound
source reading 4 GiB, then eight CPU workers that each need a 1 GiB copy of
the source's output) gave:

| Policy | Makespan | Data moved | Where the tasks ran |
|---|---:|---:|---|
| FCFS | 6.63 s | 168 MiB | everything on `cpu-fast` |
| EFT / HEFT | 40.42 s | 8,360 MiB | source on `io-fast`, 8 workers on `cpu-fast` |

The simplest possible scheduler was six times faster than the textbook
heterogeneous scheduler. The trace explains why:

1. HEFT places the source on `io-fast`, whose 7 GB/s drive finishes the
   4 GiB read at 1.10 s instead of 2.79 s on `cpu-fast`. Locally correct.
2. For each worker, HEFT estimates its finish time on `cpu-fast` as "source
   finish + 1 GiB / 238 MiB/s + compute", about 8.7 s, because the
   estimate assumes *this transfer has the link to itself*.
3. All eight workers make the same choice. Their eight 1 GiB transfers then
   share one 238 MiB/s link: 8 GiB / 238 MiB/s = 34.4 s. The workers start
   at 37.15 s and finish at 40.42 s.

This failure mode was not new: the archived pre-correction data shows the
same 40.4 s case. Aggregate tables had hidden it because the mean regret
of HEFT was dominated by many easy cases. It is a known limitation of
HEFT, which assumes a contention-free communication model (Topcuoglu et
al., 2002); contention-aware list scheduling was discussed by Sinnen and
Sousa (2005).

A second family of failures appeared on the unreliable/thermal cluster,
where Adaptive's fixed penalties (100 s per expected failure, 10 s for
thermal pressure) sometimes pushed work onto a slow node to avoid a risk
that never materialised, or failed to avoid throttling that did.

### 6.2 Design goals

1. Keep HEFT's task ordering (upward rank), which was not the problem.
2. Fix the *estimate*, not add weights: no tuned penalties, no priority
   classes, no preemption, so that any difference from HEFT can be
   attributed to the estimator alone.
3. Use only information a real scheduler has: the cluster description,
   current temperatures, transfers in flight, and decisions already made in
   the same scheduling round.
4. Stay cheap enough to call for every (task, node) pair.

### 6.3 What HEFT-RA estimates differently

For a ready task `t` and candidate node `n`, HEFT-RA's finish estimate is
built from four corrections (`simulation/estimation.py`,
`resource_aware_estimate`):

1. **Contended transfers.** Each input transfer receives a max-min fair
   share of every channel it crosses, counting transfers already in flight
   and transfers planned earlier in the same round, and is capped by the
   TCP bounds of section 5.3. In the fan-out case the second worker sees
   one competing flow, the third sees two, and so on.
2. **Shared image pulls.** If an image is already being pulled to `n` (or
   was planned in this round), a second task does not pay for it again.
3. **Thermal prediction.** Instead of using the current speed, CPU work is
   integrated over the RC thermal model: a node at 58 C with a 72 C
   throttle point is predicted to heat up under load and slow down partway
   through a long task.
4. **Expected failure loss.** For nodes with a declared Poisson failure
   rate `lambda`, the probability of a failure during a task of duration
   `d` is `1 - exp(-lambda d)`; the expected delay added is that
   probability times (half the work that would be lost, at most half a
   checkpoint interval for checkpointable tasks, plus the restart cost).
   This is risk-neutral: it uses the expected value, not a penalty weight.

Then a **one-level lookahead** (Bittencourt, Sakellariou and Madeira,
2010) scores a producer by when its children could finish: for each child,
the best (transfer + execution) over all nodes, assuming the siblings pull
from the producer concurrently. The producer's score is the latest of
those finishes.

### 6.4 What each part contributes (fan-out example)

| Variant | Makespan | Data moved | Placement |
|---|---:|---:|---|
| HEFT | 40.42 s | 8,360 MiB | source `io-fast`; 8 workers `cpu-fast` |
| HEFT-RA without lookahead | 12.94 s | 1,288 MiB | source `io-fast`; 7 workers stay on `io-fast`, 1 moves to `cpu-fast` |
| HEFT-RA | 6.63 s | 168 MiB | everything on `cpu-fast` |

Contention-aware estimates alone stop the stampede onto one link: the
estimator sees that the third, fourth, ... transfer would be slower than
computing locally on `io-fast`'s weaker CPU. Lookahead then fixes the
producer's placement: putting the source on `cpu-fast` costs 1.7 s of
slower reading but saves every transfer.

### 6.5 Where HEFT-RA is *not* better

HEFT-RA is not uniformly better, and the holdout data shows exactly where:

- On 17 of 3,750 holdout closed-DAG cases, HEFT-RA is more than 1% slower
  than HEFT. All 17 are `unreliable-thermal-3 / mixed-etl`. That cluster's
  fast node `hot-fast` has *scripted* failures at t = 20 s and 75 s (fault
  injection that no policy can know about). HEFT-RA correctly predicts that
  the ETL chain finishes earlier on `hot-fast` (the aggregate step ends at
  17.5 s instead of 24.5 s on `reliable`), but in some seeds the failure at
  20 s hits the final export step, which must then wait for the repair.
  Over the 50 holdout seeds of that pair, HEFT-RA still wins 39 and loses
  11, with a mean makespan ratio of 0.878 (median 33.9 s against 46.1 s).
  This is the cost of a risk-neutral estimator. A risk-averse variant (for
  example, minimising a high quantile instead of the mean) is listed as
  future work.
- On `homogeneous-4 / fanin-8to1`, FCFS packing beats every list scheduler:
  all list schedulers spread the eight producers and then pay to gather
  their outputs. Fixing that would require group-level consolidation (as in
  packing schedulers such as Tetris; Grandl et al., 2014), not a better
  per-task estimate. It was documented and deliberately *not* tuned for,
  because tuning for one case on development seeds is how overfitting
  starts.

### 6.6 Complexity

HEFT costs O(|V| x |N|) estimates per round plus the rank computation.
HEFT-RA adds, per (task, node) estimate, a max-min share over the flows on
the route (bounded by the flows in flight) and, per producer, the lookahead
over (children x nodes). On the largest scenario workflow (`wide-12`, 12
tasks, up to 5 nodes) a whole run takes milliseconds; the lookahead
dominates only for very wide fan-outs and could be capped if needed.

### 6.7 Development protocol

HEFT-RA was designed using only the exploratory seeds (0-19). Its
parameters are not tuned: there are no weights. The confirmatory and
holdout matrices were run after the code was frozen, and no change was
made to HEFT-RA after looking at them. The capacity-aware lookahead
(`heft-ra-cap`) was added later, prompted by the live trials, when the
first holdout had already been seen; a second, fresh holdout (seeds
600-649) was therefore run once for the final policy set.

---

## 7. Phase M - re-running the simulation evidence

### 7.1 The corpus

- **5 cluster families**: `homogeneous-4` (four equal nodes, 1 Gb/s
  switch), `balanced-heterogeneous-4` (CPU-, memory-, I/O-optimised nodes
  and an ARM edge node on Wi-Fi), `asymmetric-edge-5` (two core nodes and
  three edge nodes behind a shared 50 MiB/s WAN uplink), `unreliable-thermal-3`
  (a hot fast node with scripted failures, a reliable node, a failure-prone
  edge node), and `local-i5-8300h` (the development laptop).
- **15 closed workflows**: linear, diamond, deep, wide, fan-out, fan-in,
  CPU-heavy, memory-heavy, I/O-heavy, cache-sensitive, data-heavy pipeline,
  mixed ETL, gang, critical-deadline (priority), and checkpoint-long
  (failure recovery). Shapes follow the structures documented for
  scientific workflows (Bharathi et al., 2008; Juve et al., 2013).
- **4 overlapping workloads**: `mixed-arrivals`, `priority-burst`,
  `data-locality-wave`, `recovery-gang-mix`, where several workflows
  arrive over time and compete.

### 7.2 Discovering that most seeds were the same experiment

The old matrices reported "20 seeds per case". But a seed only changes a run
through (a) sampled calibration uncertainty, (b) jitter, and (c) random
failures. Four of the five clusters declare **zero** calibration
uncertainty, and most workflows never meet a random failure. Counting
distinct outcomes per (cluster, workflow, policy) over the 20 exploratory
seeds of the final code:

| Cluster | Mean distinct makespans out of 20 seeds |
|---|---:|
| homogeneous-4 | 1.4 |
| balanced-heterogeneous-4 | 3.0 |
| unreliable-thermal-3 | 4.0 |
| asymmetric-edge-5 | 4.3 |
| local-i5-8300h | 20.0 |

On the homogeneous cluster, the "20 seeds" were on average 1.4 different
experiments repeated. (These values come from the final synthesis, whose
exploratory phase includes all twelve policies; an earlier eleven-policy
pass gave 1.5, 3.2, 4.2 and 4.7.) Confidence intervals computed as if they were 20
independent samples were far too narrow.

**Fix.** The confirmatory and holdout phases use `--uncertainty-floor 0.1`:
every node's throughput, memory, storage, power, thermal and reliability
parameters and every link's bandwidth, latency and jitter are multiplied
by independent seeded factors drawn uniformly from [0.9, 1.1] (wider where
the cluster declares more uncertainty); structural parameters such as core
counts, cache sizes, temperature thresholds and loss rates are unchanged.
Each seed is then a genuinely different cluster instance, close to the
declared one. The exploratory phase keeps the declared uncertainty, so
the three phases also test whether conclusions survive this perturbation.
The synthesis reports distinct outcomes for both the development and the
holdout phase, so this cannot silently happen again: on the holdout every
one of the 50 seeds gives a distinct outcome for every cluster, case and
policy.

### 7.3 Protocol

| Phase | Seeds | Uncertainty | Policies | Closed records | Workload records |
|---|---|---|---|---:|---:|
| Exploratory | 0-19 | declared | 12 + oracle | 18,300 | 4,800 |
| Confirmatory | 200-249 | floor 0.1 | 10 | 37,500 | 10,000 |
| Holdout | 400-449 | floor 0.1 | 10 | 37,500 | 10,000 |
| Fresh holdout | 600-649 | floor 0.1 | 10 | 37,500 | 10,000 |

FCFS and Random are excluded from the later phases because they were never
competitive; the static oracle (workflows of at most three tasks) only
exists in the exploratory phase. Regret is always computed against the
best deployable policy of the same phase, and the exploratory phase is
also reported restricted to the same ten policies, so that phases can be
compared like for like. The fresh holdout was added because `heft-ra-cap`
was designed after the first holdout had been seen (section 9.4); it was
run once, after the code was frozen. All matrices were regenerated with
the final code and policy set, so every phase contains `heft-ra-cap`. All
tables are in `results/final-evidence-synthesis.md`.

### 7.4 Closed DAG results (fresh holdout, 3,750 cases)

| Policy | Mean regret [95% CI] | P95 regret | Win/tie rate | First holdout mean / P95 |
|---|---:|---:|---:|---:|
| **heft-ra** | **0.37%** [0.28, 0.47] | **0.00%** | **95.39%** | 0.39% / 0.12% |
| **heft-ra-cap** | **0.37%** [0.28, 0.47] | **0.00%** | **95.39%** | 0.39% / 0.12% |
| heft | 12.42% [10.57, 14.45] | 49.02% | 80.05% | 12.06% / 50.59% |
| eft | 12.54% [10.68, 14.57] | 49.02% | 79.65% | 12.23% / 50.59% |
| legacy | 13.61% [11.62, 15.77] | 54.23% | 78.83% | 13.39% / 52.05% |
| adaptive | 13.21% [11.83, 14.69] | 57.83% | 71.95% | 13.42% / 64.20% |
| adaptive-no-preempt | 13.21% [11.83, 14.69] | 57.83% | 71.95% | 13.42% / 64.20% |
| legacy-tuned | 18.34% [16.57, 20.15] | 73.00% | 56.64% | 18.42% / 68.09% |
| legacy-v1 | 27.80% [25.35, 30.21] | 173.88% | 52.91% | 27.64% / 175.47% |
| k8s-default | 160.69% [144.58, 176.40] | 1296.03% | 42.32% | 162.56% / 1257.52% |

How to read this table:

- HEFT-RA is within 0.37% of the best deployable policy on average, and in
  95% of cases it is the best policy or tied with it.
- `heft-ra-cap` and `heft-ra` are identical on every closed case of every
  phase (paired ratio 1.0000, 3,750 ties): simulated nodes run 8 to 16
  tasks at once, so the children always find capacity and the two
  lookaheads agree.
- HEFT's 12.42% looks much worse than the old journal's 1.69%. Most of
  the difference is the reference point: regret is measured against the
  best policy, which is now HEFT-RA. Recomputing the first-holdout regret
  *without* HEFT-RA in the set gives HEFT 2.44% mean and 9.44% P95, the
  best of the remaining policies, which is consistent with the old
  conclusion that HEFT was the best of the old portfolio.
- Paired fresh-holdout contrast, HEFT-RA vs HEFT: mean makespan ratio
  0.9602 [0.9561, 0.9642], 712 wins, 3,015 ties, 23 losses (first
  holdout: 0.9604, 701 / 3,023 / 26).
- The confirmatory phase gives the same order and nearly the same
  numbers (HEFT-RA 0.38% / 0.00% P95; HEFT 12.31% / 49.91%).
- Adaptive and Adaptive-without-preemption are identical on closed DAGs:
  a single workflow has nothing to preempt for.
- The exploratory phase (declared uncertainty, all 12 policies) has the
  same leader (HEFT-RA 1.76% mean, 5.75% P95); FCFS (502%) and Random
  (2811%) confirm that placement matters a great deal.
- Against the exhaustive static oracle on the 300 small exploratory cases,
  HEFT-RA has the smallest mean gap (2.53%, median 0%), followed by HEFT
  (3.06%).
- Crossing 15 workflow families with 5 cluster families gives 75 cells;
  in 69 HEFT-RA is the best policy or tied with it. The only large
  exception is `unreliable-thermal` / `mixed` (HEFT-RA 18.18% mean regret,
  Adaptive and tuned Legacy 0.00%), the risk-neutral failure case of
  section 6.5. The others are small: homogeneous / cache-sensitive (Legacy
  0.43% vs 1.74%), unreliable / priority (k8s-default 0.00% vs 1.58%),
  balanced / priority (k8s-default 0.18% vs 0.38%), homogeneous / fan-in
  (Adaptive 0.00% vs 0.08%), homogeneous / io-heavy (EFT, HEFT, Legacy;
  HEFT-RA below 0.005%).

### 7.5 Overlapping workload results (fresh holdout, 1,000 cases)

Ranked by median response regret (response = finish - arrival per
workflow, averaged per case):

| Policy | Median response regret | P95 response regret | Deadline hit rate | Median makespan |
|---|---:|---:|---:|---:|
| **heft-ra-cap** | **0.00%** | **63.76%** | **71.47%** | **47.21 s** |
| heft-ra | 0.51% | 63.76% | 70.53% | 48.20 s |
| adaptive | 0.65% | 83.76% | 69.20% | 53.28 s |
| eft | 0.97% | 82.56% | 68.13% | 53.10 s |
| adaptive-no-preempt | 1.00% | 83.76% | 69.20% | 50.91 s |
| legacy | 1.05% | 82.87% | 67.73% | 56.06 s |
| heft | 1.29% | 82.56% | 68.13% | 52.23 s |
| legacy-tuned | 8.11% | 98.70% | 69.73% | 57.56 s |
| legacy-v1 | 32.64% | 214.11% | 66.53% | 69.15 s |
| k8s-default | 86.62% | 1420.09% | 36.27% | 115.43 s |

Here the two lookaheads differ, because several workflows compete for the
same nodes: `heft-ra-cap` vs `heft-ra` has a paired mean-response ratio of
0.9973 [0.9951, 0.9995] (98 wins / 873 ties / 29 losses) on the fresh
holdout and 0.9998 [0.9983, 1.0014] on the first holdout, i.e. a small
gain at best. Other paired fresh-holdout contrasts: `heft-ra-cap` vs HEFT
0.9617 [0.9494, 0.9735]; Adaptive vs Adaptive-without-preemption 0.9935
[0.9889, 0.9978]; Adaptive vs HEFT 0.9671 [0.9555, 0.9789]; HEFT vs
k8s-default 0.6689 [0.6446, 0.6937].

By workload (fresh holdout): `heft-ra-cap` has the lowest mean response
regret on `data-locality-wave`, `mixed-arrivals` and `priority-burst`; EFT
and HEFT tie on `recovery-gang-mix`. On the first holdout Adaptive led
`priority-burst`. Mean response regret averages all workflows of a
workload and hides what happens to the urgent one; the priority study of
section 7.7 separates them. Two cluster families are exceptions for
overlapping work: on the single-node laptop profile, Adaptive leads
`mixed-arrivals` (2.26% vs 29.55% for HEFT-RA) and `priority-burst`
(0.00% vs 64.12%), and Legacy leads `data-locality-wave`; on the
unreliable cluster, Adaptive leads `mixed-arrivals` (0.66% vs 38.77%) and
EFT and Legacy lead `data-locality-wave` (15.29% vs 42.32%).

### 7.6 Legacy weight search, factor ablation, contextual search

- **Weight search** (`results/legacy-weight-search.json`): 256 seeded
  log-normal candidates at a fixed absolute weight scale of 125, 90
  training, 45 validation, 75 holdout cases. The corrected run selects the
  same vector as before. On holdout with warm profiles the tuned/original
  objective is 0.9759 (mean makespan 42.18 s to 41.80 s, transfer 434.7 to
  272.5 MiB); with cold profiles it is 0.8601 (mean makespan 99.98 s to
  54.89 s). Tuning helps, but tuned Legacy is still far behind HEFT-RA
  (18.42% vs 0.39% holdout regret): the original scorer was not merely
  badly tuned; its structure (weighted sum of normalised factors) cannot
  express finish time.
- **Leave-one-factor-out** (`results/legacy-factor-ablation.json`, seeds
  300-309, 750 cases): type affinity is by far the most important factor
  (removing it multiplies the objective by 3.4); data locality (+0.088)
  and load balance (+0.072) matter; removing resource fit (-0.0005) or node
  availability (-0.0002) changes nothing of practical size. Warm image,
  failure penalty and memory pressure never change a decision in the broad
  corpus, but each one flips the decision in a controlled stress case
  (`results/legacy-factor-stress.json`), so they are kept.
- **Contextual search** (`results/contextual-policy-search.json`): 160
  generated workflows in 20 (resource mix x topology) contexts; a
  specialist replaces the default only with a validation gain of at least
  0.5% and no mean or P95 regression. The corrected search keeps HEFT in
  19 contexts and selects HEFT-RA for `cpu:fanout`. On 600 holdout cases
  (objective relative to HEFT, lower is better): HEFT-RA 0.9952, contextual
  portfolio 0.9977, Adaptive 0.9989, HEFT 1.0, EFT 1.0254. Using HEFT-RA
  everywhere is better than the portfolio, so the portfolio is not
  recommended.

### 7.7 Mechanism studies: cold starts and priorities

Two research questions are about mechanisms, not whole policies, so
`run_mechanism_studies.py` isolates them on the holdout protocol (seeds
from 400, +/-10% floor), paired on identical cluster instances. Output:
`results/mechanism-studies.md`.

**Cold starts (750 closed runs per variant).**

| Variant | Mean makespan | Mean time-to-running | P95 time-to-running | Cold starts |
|---|---:|---:|---:|---:|
| HEFT-RA | 20.19 s | 1.13 s | 11.76 s | 3.79 |
| HEFT-RA with image-blind estimates | 20.42 s | 1.17 s | 13.82 s | 3.79 |
| HEFT-RA, every image pre-pulled | 19.54 s | 0.82 s | 11.76 s | 0 |
| k8s-default | 31.87 s | 2.96 s | 66.10 s | 3.89 |

Image awareness helps a little (makespan ratio 0.9879 [0.9821, 0.9932],
time-to-running 0.9846), mostly on the unreliable cluster (0.9409). Much
more is left on the table: pre-pulling everything would cut makespan by
7.8% (0.9223, never worse) and time-to-running by about half (0.5103).
Lesson: placement alone cannot remove cold starts; prefetching or lazy
pulling is the bigger lever.

**Priorities and preemption (three workloads with a CRITICAL workflow, 750
runs per policy).**

| Policy | Urgent response | Other response | Urgent deadline hit | Preemptions per run |
|---|---:|---:|---:|---:|
| adaptive | **10.93 s** | 56.93 s | 67.60% | 0.34 |
| adaptive-no-preempt | 15.51 s | 56.86 s | 67.60% | 0 |
| heft-ra | 20.94 s | **55.20 s** | **69.47%** | 0 |
| heft | 21.33 s | 57.36 s | 66.13% | 0 |
| eft | 21.76 s | 56.87 s | 66.13% | 0 |
| k8s-default | 42.25 s | 76.97 s | 35.20% | 0 |

Preemption fires in 100 of the 750 runs and, when it does, roughly halves
the urgent response (mean ratio 0.534); over all runs Adaptive vs
no-preemption is 0.9379 [0.9236, 0.9512] (99 wins / 1 loss), and it costs
the others almost nothing (1.0006). On `priority-burst` the urgent
workflow takes 12.5 s under Adaptive, 25.3 s without preemption, and
36.5 s under HEFT-RA (means).

**The means hide the real trade-off** (found in the verification pass,
section 9.5). Per run, HEFT-RA's urgent workflow is faster more often in
every workload (139 vs 83 runs on `mixed-arrivals`, 99 vs 73 on
`priority-burst`, 138 vs 6 on `recovery-gang-mix`), and it meets the 12 s
urgent deadline as often or slightly more often (80% vs 80%, 54% vs 52%,
75% vs 71%; the runs where only one of them meets it are decided by about
a tenth of a second, 22 of the 24 favouring HEFT-RA on the homogeneous
cluster). What Adaptive buys is the tail:

| Urgent response (750 runs) | Median | P95 |
|---|---:|---:|
| heft-ra | **7.6 s** | 126.6 s |
| adaptive-no-preempt | 9.7 s | 75.4 s |
| adaptive | 9.7 s | **21.0 s** |

Priority ordering cuts the P95 from 127 s to 75 s and preemption to 21 s.
Priority ordering also has a price: against HEFT-RA, Adaptive makes the
other workflows 7.8% slower per run (1.0781 [1.0519, 1.1097]). So the
choice between the two families depends on whether the urgent workflow's
*worst case* matters, not merely on whether urgent work exists.

---

## 8. Phase N - rebuilding the live emulation

### 8.1 Why the old live experiments had to be redone

Section 4 listed the problems; here they are from the point of view of an
experiment designer:

1. **No heterogeneity of speed** (J7). Every task ran the same work at the
   same 500m CPU quota on every node, so placement could only matter
   through slot counts and links.
2. **No DAG in the scheduler** (J8). HEFT, the simulator's best baseline at
   the time, could not be run live at all.
3. **Logical versus physical bytes.** The scheduler planned transfers of
   100 MiB while Pods actually moved 10 MiB, so transfer costs were
   over-weighted ten times compared with measured runtimes.
4. **Coarse clocks** (J16). Runtimes came from 1 s kubelet timestamps for
   2-5 s tasks, and workflow response from `creationTimestamp` and a 2 s
   reconcile tick.
5. **Correctness defects** (J2-J6, J17): double dispatch, refresh
   overcommit, swallowed bind failures, Pod name aliasing, races.

### 8.2 The new emulation

The design goal: *a Kind cluster on one laptop whose nodes behave like a
small heterogeneous cluster in the ways that matter to placement, and
which the simulator can model with calibrated parameters.*

**Profiles.** `clusters/kind_profiles.yaml` is the single source of truth.
Three profiles mirror simulator families:

| Profile | Mirrors | Workers |
|---|---|---|
| `balanced` | `balanced-heterogeneous-4` | `ts-node-cpu` (2 slots; CPU 1.0, memory 0.55, I/O 0.5), `ts-node-mem` (2; 0.45/1.0/0.55), `ts-node-io` (1; 0.4/0.5/1.0), `ts-node-edge` (1; 0.3/0.3/0.3, 10 ms, 60 Mbit/s) |
| `homogeneous` | `homogeneous-4` | three equal workers, 2 slots each, 4 ms, 400 Mbit/s |
| `edge` | `asymmetric-edge-5` | two core workers (2 slots, speed 1.0, 1 ms, 1 Gbit/s), two edge workers (1 slot, speed 0.35, 15 ms, 40 Mbit/s) |

Fast-node links in `balanced` use 2 ms and 800 Mbit/s. Every profile has six
task slots in total, i.e. three CPUs of task work, which leaves the 4-core
/ 8-thread host room for the Kubernetes agents.

**Slots.** Every validation task requests and is limited to 500m CPU, so a
node with `slots: 2` runs two tasks at once. Its logical CPU capacity
label is `slots x 0.5`.

**Container caps.** `kind_lab.py create` sets each worker container to
`cpus = slots x 0.5 + 0.35` (0.35 CPU for kubelet, kube-proxy, kindnet and
the DaemonSets), `memory = slots x 224 + 576` MiB, and `cpu-shares = 1024
x slots`, so that if the host is ever contended, CPU is shared fairly per
task rather than per container.

**Speeds.** A ConfigMap `ts-emulation` holds each node's CPU, memory and
I/O speed. Tasks learn their node through the downward API
(`TS_NODE_NAME`) and read the speeds (`TS_NODE_SPEEDS`). Work is scaled by
`1 / speed` for the task's resource class.

**CPU and memory work as CPU-time budgets.** A CPU task is told "consume
1.5 CPU-seconds at speed 1.0"; on a node with CPU speed 0.45 it keeps
working until it has consumed 1.5 / 0.45 = 3.33 CPU-seconds of process
time. The first version of the emulation counted *iterations* instead (a
fixed number of hashes). Calibration showed why that was wrong: the same
iteration count ran at 0.99-1.09 million hashes per second on 1-slot nodes
but at 0.63 million on the 2-slot CPU node, because two busy tasks on a
2-slot node shared physical cores through SMT and the containers shared
the host by cgroup weight. The emulated "fast" node was therefore slower
per task than the "slow" edge node. Counting consumed CPU time instead
makes a task's duration depend only on its budget and its 500m quota.
The fix was verified on the next calibration: a CPU task on `ts-node-mem`
reported 6.83 s for a 3.33 CPU-second budget (0.49 CPU on average), i.e.
6.83 x 0.45 = 3.07 s of normalised work against 3.0 s expected.

**I/O work** writes real bytes at a paced rate (`TS_IO_RATE_MIB_S x I/O
speed`, with `fsync`), so it scales exactly; calibration measured 3.01-3.02
s of normalised work on every node.

**Network.** `tc netem` on each worker's `eth0` egress sets delay and rate.
Task outputs stay on the producing node (`/var/lib/ts-data`) and children
fetch them over HTTP from the per-node fileserver, so a producer's shaped
egress determines how long its children wait. Shaping only egress is a
deliberate simplification: in this data plane, all bulk data flows out of
producers.

**Real-size data.** The live templates move the bytes they report (24 MiB
and 96 MiB outputs), so the scheduler's transfer estimates and the
measured transfers refer to the same quantity.

**Clocks.** Tasks report their own start and finish as epoch timestamps
with sub-millisecond resolution. Workflow response is measured from the
controller's `admittedAt` to the latest task finish.

**Bandwidth telemetry.** The `ts-bw-probe` DaemonSet measures every peer
and annotates nodes with `ts.io/bw-from-<peer>`. The first version probed
all peers at the same time, so probes competed: the edge node measured
1.7-3.0 MiB/s against a shaped 7.15 MiB/s. Probes now run in wall-clock
slots 60 s apart; the edge now measures 6.79-6.82 MiB/s.

### 8.3 The live policies

The live scheduler (`services/policy.py`) implements `legacy-v1`,
`legacy-tuned`, `eft`, `heft`, `heft-ra`, `heft-ra-cap`, `adaptive`,
`adaptive-no-preempt`, `kube-default`, and a `calibrate` policy used only
during warm-up. All receive the workflow graph through a `workflow_dag`
annotation, from which upward ranks are computed (`services/dag_metrics.py`).
Live HEFT-RA uses the measured bandwidth annotations and the learned
runtime profiles, counts transfers in flight and planned in the same round,
and applies the same one-level lookahead as the simulator. No policy
overcommits a node: a CRITICAL task that does not fit waits (Adaptive
first frees room by preemption).

### 8.4 Problems met while building it

| Problem | How it showed up | Fix |
|---|---|---|
| Concurrent bandwidth probes | Edge measured at 25-40% of its shaped rate | Wall-clock probe slots |
| Iteration-count work | "Fast" node slower per task than "slow" node | CPU-time budgets and per-slot CPU shares |
| Long Pod names | Different tasks mapped to one Pod name | Hashed DNS-1123 names |
| `powershell -File` with `-Profiles a,b,c` | The list arrived as one string | The script splits comma-separated names |
| Image loading under CPU caps | `kind load` takes several minutes per image | Operational note: load before timing starts |

---

## 9. Phase O - live Kubernetes validation and the simulator twin

### 9.1 What the live matrix has to show

The simulator says HEFT-RA is the best placement policy and that the
default Kubernetes scoring is far behind. The live matrix asks two
questions:

1. **Does the policy ranking hold on a real control plane?** Same
   policies, same kinds of workflows, real Pods, kubelets, cgroups and
   shaped links.
2. **Does the simulator predict the live system?** If a simulator model of
   the emulated cluster (the *twin*), calibrated only on a warm-up phase,
   predicts each live trial well, then the simulator's conclusions can be
   trusted to transfer at least to systems like the emulated one.

### 9.2 Protocol

For each profile (`balanced`, `homogeneous`, `edge`):

1. `kind_lab.py create <profile> --record results/kind-validation/<profile>-cluster.json`
   builds the cluster, applies container caps and `tc netem`, writes the
   speed ConfigMap, and records the applied qdisc, caps and speeds as
   evidence.
2. All images are loaded and the control plane is installed with
   `kubectl apply -k .`.
3. `run_k8s_experiment.py` refreshes the bandwidth probes, then runs a
   **warm-up** with the `calibrate` policy: every template runs three times
   on each worker (12 runs per template on a four-worker profile), so every
   node has a learned profile. The resulting profile store is saved and
   **every trial starts from the same warm profiles**, so no policy
   benefits from what an earlier trial learned.
4. **Trials**: two batches x nine policies x three repetitions = 54 trials,
   in a seeded random order (seed 2026) so that drift on the host cannot
   systematically favour one policy.
   - `priority` batch: a best-effort `pressure` workflow of six long CPU
     tasks fills the cluster; 7 s later a CRITICAL two-task workflow and
     three BATCH workflows (a pipeline, a fan-out, independent tasks)
     arrive. 20 tasks.
   - `dag` batch: four BATCH DAGs arrive together: a data pipeline with a
     96 MiB producer, a data-heavy fan-out (one 96 MiB producer, four
     consumers), a fork-join, and independent mixed tasks. 17 tasks.
5. `kind_twin_predict.py` calibrates the twin from the warm-up records only
   and predicts every trial with the corresponding simulator policy; with
   `--slot-release measured` the twin also keeps a finished task's slot
   reserved for the warm-up's median completion-to-rebind latency.
6. `analyze_kind_validation.py` checks integrity and compares live and twin
   results.

**Integrity checks.** The analyzer refuses to report a profile unless all
of these hold: the observed qdisc on every worker matches the profile's
delay and rate, and the published speeds and capacity labels match; the
warm-up produced at least three observations per template and worker;
every trial ran the intended policy with the warm profiles loaded and no
scheduler errors; every workflow and task finished, with no container
restarts, on the node its own metrics report, at the emulated speed of
that node; every consumer read exactly the bytes its producer wrote, in
local mode when co-located and remote mode otherwise; the number of binds
equals the number of tasks plus preemptions; and only Adaptive preempted.

### 9.3 Results

All numbers come from `results/kind-validation/summary.md` (fixed
scheduler, 162 trials, integrity gate passed on every profile).

**Emulation fidelity.** Calibrated on the warm-up, speed-normalised base
runtimes were 3.03-3.07 s for the CPU template (target 3.0), 10.02-10.06 s
for long CPU (10.0), 3.01 s for I/O (3.0), 3.05-3.25 s for large I/O (3.0)
and 4.16-4.43 s for memory (4.0). Edge egress measured 6.8 MiB/s against a
shaped 7.15 MiB/s (balanced) and 4.5 MiB/s against 4.77 MiB/s (edge).

**Pooled results (medians of nine trials per policy, seconds).**

| Policy | dag makespan | dag mean response | priority makespan | priority mean response | Critical response | Preemptions (9 trials) |
|---|---:|---:|---:|---:|---:|---:|
| heft-ra | **34.04** | 28.36 | 47.52 | 34.09 | 23.60 | 0 |
| heft-ra-cap | 34.74 | **27.47** | 47.81 | 35.04 | 23.89 | 0 |
| heft | 36.67 | 28.06 | 47.61 | 34.93 | 23.73 | 0 |
| adaptive | 36.94 | 27.87 | 51.81 | 33.45 | **15.31** | 31 |
| legacy-tuned | 38.11 | 28.66 | 48.05 | 31.46 | 16.64 | 0 |
| adaptive-no-preempt | 38.93 | 28.46 | 49.02 | 32.12 | 16.65 | 0 |
| kube-default | 38.99 | 28.58 | 48.37 | 31.83 | 17.33 | 0 |
| legacy-v1 | 39.30 | 28.45 | 48.05 | 31.52 | 16.59 | 0 |
| eft | 41.79 | 29.23 | **47.27** | **31.08** | 16.56 | 0 |

**What the paired contrasts say** (ratio of matched trials, 95% bootstrap
interval, wins / losses):

- Concurrent DAGs: HEFT-RA / HEFT 0.917 [0.837, 0.998], 8 / 1 (the
  interval barely excludes one). HEFT / kube-default 0.930 [0.857, 1.006]
  and HEFT / EFT 0.917 [0.824, 1.031] are not conclusive with nine pairs.
  `heft-ra-cap` / `heft-ra` 1.002 [0.930, 1.074]: no difference. The
  stronger live statement is about ranks: a HEFT-RA variant was the
  fastest of nine policies in 8 of the 9 (profile, repetition) runs
  (probability about 4e-5 if the policies were interchangeable; the
  exception is edge repetition 3, won by HEFT).
- Per profile: `heft-ra-cap` is fastest on balanced (35.9 s, then
  Adaptive-without-preemption 39.2 s, `heft-ra` 40.0 s; HEFT 46.0 s is
  seventh), `heft-ra` on edge (34.5 s; HEFT 36.7 s), and on homogeneous
  HEFT-RA (28.3 s) and HEFT (28.9 s) are equivalent, as expected with
  identical nodes. EFT, nearly equal to HEFT in simulation, is the slowest
  live policy on the DAG batch.
- Mechanism (balanced profile): HEFT-RA put the 96 MiB fan-out producer on
  the CPU node (fastest egress and CPU for its four CPU consumers) in all
  six of its trials; HEFT left it on the memory node in all three and
  spent up to 2.8x as long staging inputs (12.2 s vs 4.4 s summed over the
  batch). This is the simulated mechanism (section 6) appearing live.
- Tuned Legacy did not transfer: `legacy-tuned` / `legacy-v1` 1.043
  [0.997, 1.092] on makespan and 1.053 [1.021, 1.086] on mean response.
- Urgent work: Adaptive / Adaptive-without-preemption has critical
  response 0.878 [0.754, 0.969], 8 / 0, but makespan 1.139 [0.997, 1.285]
  and P95 response 1.258 [1.054, 1.458] because 31 killed tasks restart
  from scratch (the makespan interval includes no change). HEFT /
  kube-default has critical response 1.476 [1.245, 1.746], 0 / 9: the
  HEFT family orders by upward rank only, so the two short urgent tasks
  wait behind batch work, while kube-default's PrioritySort queue puts
  them first. Even first-come ordering (EFT, 16.56 s) does almost as well
  as priority ordering (Adaptive without preemption, 16.65 s) here, so
  most of the live gap comes from ranking urgent work last. This batch is
  the tail case of the simulated priority study: the critical workflow
  always arrives into a full cluster.
- `heft-ra-cap` / `heft-ra` on the priority batch: mean response 1.024
  [1.001, 1.046], 2 / 7. Marginal, and one of 45 contrast-metric pairs, so
  reported but not interpreted.
- Note: live `kube-default` is the default-scoring model running inside
  the custom scheduler (same control path for every policy); the stock
  kube-scheduler binary was not part of the comparison.

**Twin agreement (makespan).**

| Profile / batch | Original twin MAPE / bias | Release-latency twin MAPE / bias | Spearman |
|---|---|---|---:|
| balanced / dag | 6.8% / -6.0% | 6.0% / -0.8% | 0.62 |
| balanced / priority | 12.6% / -12.6% | 3.1% / +0.0% | 0.30 |
| edge / dag | 5.8% / -5.4% | 4.4% / +0.0% | 0.50 |
| edge / priority | 19.6% / -15.5% | 6.4% / -0.3% | 0.47 |
| homogeneous / dag | 17.8% / -17.8% | 12.4% / -12.4% | 0.15 |
| homogeneous / priority | 11.1% / -9.6% | 6.2% / -3.2% | -0.19 |

The release latency calibrated on the warm-up was 2.75 s (balanced),
2.25 s (homogeneous) and 2.34 s (edge). Adding it removes most of the
optimistic bias and brings the mean absolute error to 3-8% on the
heterogeneous profiles. Rank agreement is moderate there and absent on the
homogeneous profile, where policies differ by less than run-to-run noise.
The twin picks the right winner for balanced / dag but favours
kube-default on edge / dag (35.6 s predicted, 39.0 s measured), and it
does not reproduce the HEFT family's penalty on urgent work. The
remaining known bias: a DAG edge costs 0.4-0.9 s more control-plane latency
than a root task (10th percentile parent-finish to child-bind 2.9-3.2 s,
against a calibrated 2.1-2.7 s), and the warm-up has no edges to measure it.

### 9.4 What the live runs taught (and changed)

The live validation was not a single run; it was the part of the project
that found the most surprising problems.

**First full run: HEFT-RA looked worse than HEFT.** On the balanced
profile, HEFT-RA's DAG makespan was close to HEFT's and its priority-batch
makespan was much worse (75.0 s median against 61.3 s), while the twin
predicted HEFT-RA best. Two explanations were investigated:

1. *The lookahead ignored capacity.* In one trial the lookahead put the
   fan-out source on the fast CPU node so that its four leaves could run
   locally, but that node has two slots, and other workflows' tasks took
   them, so every leaf ran remotely on slower nodes. The simulator never
   showed this because its nodes run 8-16 tasks at a time. This led to
   `heft-ra-cap` (thesis: "HEFT-RA with a capacity-aware lookahead"):
   children are assigned to the
   capacity that will actually be free, exactly as the non-delay list
   scheduler will place them. A first version let a child *wait* for a busy
   fast node, which the real scheduler never does; working through a
   concrete four-node example exposed that before any data was collected.
   A second issue followed: the live scheduler only learns a template when
   one of its Pods appears, so children's requests were unknown at scoring
   time and the new lookahead silently degenerated to the old one; it now
   looks templates up in any workflow and otherwise assumes the producer's
   size. Regression tests cover both surfaces.
2. *The scheduler froze nodes.* Comparing timelines showed Pods created by
   the controller within 0.05 s of becoming ready, yet bound more than 30 s
   later while nodes were idle. The cause was a race in the
   refresh introduced by the earlier fix for J3: the inventory is polled
   outside the lock, so a Pod that finished between the poll and the merge
   was still "running" in it and was charged as an untracked Pod, making
   its node look full until the next refresh 30 s later. Locality-seeking
   policies were hit hardest, because the frozen node is exactly the one
   where a producer's children were meant to run. The harness had not
   noticed anything because it kept only log lines containing a Pod name,
   and refresh or tick errors contain none.

**What was done.** The merge now ignores Pods the scheduler has already
released (completion or preemption); a regression test reproduces the race;
the harness records scheduler-wide errors and gates on tick errors; the
scheduler logs completions with timestamps. The contaminated run was
stopped, archived in `results/kind-validation/pre-fix-run/`, and the whole
live matrix was rerun with nine policies. In the rerun a HEFT-RA variant
was the fastest live policy on concurrent DAGs, as the simulator had
predicted. (This is a statement about the rerun, not a before/after
effect of the fix: HEFT-RA's own balanced-profile median was about 40 s
in both runs, while HEFT's moved from 39.4 s to 46.0 s; see 9.5.)

**The twin was optimistic.** Measuring the time from a task finishing to
the next bind on the same node during the warm-up gave a stable 2.3-2.8 s
(kubelet status update, watch event, log read, next tick). The original
twin freed a slot instantly. `slot_release_delay_s` was added to the
simulator (default 0, so the scenario corpus is unaffected) and to the
twin, calibrated only from the warm-up, and the report shows both twins.

**Sizing the live evidence.** Even after the fix, run-to-run spread is
several percent, so only contrasts whose paired intervals exclude one are
interpreted, and the twin is judged on error and rank agreement, not on
individual trials.

### 9.5 Verification pass on the final results

Before writing the conclusions, every headline number was re-derived from
the raw records, and four checks were added that the summary tables could
not answer.

1. **Did the fix remove the stalls?** For every trial without preemption,
   the longest period during which a Pod was pending while some slot was
   free (occupancy from bind to the task's own finish time):

   | Run | Profile | Trials | Longest stall, median / P90 / max | Stalls over 10 s |
   |---|---|---:|---|---:|
   | before fix | balanced | 42 | 6.1 / 16.8 / 38.1 s | 11 |
   | before fix | homogeneous | 27 | 4.2 / 7.1 / 33.0 s | 2 |
   | after fix | balanced | 48 | 5.6 / 9.0 / 10.3 s | 2 |
   | after fix | homogeneous | 48 | 4.1 / 5.9 / 8.0 s | 0 |
   | after fix | edge | 48 | 6.1 / 7.3 / 8.3 s | 0 |

   The 30+ s stalls are gone; the remaining 4-6 s is the normal slot
   hand-over (the twin's measured release latency plus a tick).
2. **Is the live HEFT-RA lead robust?** A HEFT-RA variant won 8 of 9
   runs (above). But HEFT's own result is not stable between complete runs:
   on balanced, HEFT's DAG makespans were 39.7 / 35.9 / 39.4 s in the
   earlier run and 57.5 / 46.0 / 42.9 s in the rerun, while HEFT-RA's
   were 39.5 / 44.0 / 40.4 s and 40.0 / 43.2 / 33.9 s. The three
   repetitions of a profile share one warm-up, so they are not independent
   with respect to calibration noise. The 8.3% HEFT-RA/HEFT figure is
   therefore reported as "HEFT-RA consistently among the fastest", not as a
   precise effect size.
3. **Why was HEFT slow?** The fan-out placements above: HEFT keeps the
   producer on a node whose consumers cannot run there and pays for the
   transfers; HEFT-RA's lookahead moves it. Same mechanism as section 6.
4. **Does Adaptive really serve urgent work better?** Only in the tail
   (section 7.7). The earlier draft recommended Adaptive for "workloads
   with urgent workflows" from mean response alone; the recommendation now
   distinguishes the worst case (Adaptive) from the typical case and
   deadline hits (HEFT-RA).
5. **How much of HEFT-RA's simulated lead is by construction?** In
   simulation every policy plans with the exact parameters of the cluster
   instance it runs on (only jitter and random failure times are unknown
   to it), and HEFT-RA's estimator calls the simulator's own resource
   models (`simulation/estimation.py` imports them from
   `simulation/resources.py`). The perturbed phases therefore test
   robustness across cluster instances, not robustness to wrong
   estimates, and the 0.37% simulated regret is a best case for a
   scheduler whose model matches reality. HEFT and EFT also plan with
   exact parameters, so the simulated gap measures what modelling
   contention is worth. The live trials, with learned runtimes and
   measured bandwidths, are the real test: HEFT-RA stayed the fastest
   policy there, but its advantage is known only to within the wide
   interval above.

Corrected in the documents as a result: the claim that the live ordering
of all policies matches the simulator (only the top does), the claim that
HEFT beats the default scoring live (not conclusive), the statement that
`heft-ra-cap` is never worse than `heft-ra` (it is better on average but
loses 29 of 1,000 overlapping-workload cases), and the urgent-work
recommendation.



---

## 10. Recommendations: which algorithm for which workflow and cluster

The decision table below is the project's final answer to "which
algorithm for which workflow and cluster". "HEFT-RA" means the
capacity-aware variant `heft-ra-cap`: identical to `heft-ra` on closed
DAGs, slightly better on average on overlapping workloads in simulation
(0.9973 of `heft-ra`'s mean response, although it loses 29 of 1,000
cases), and indistinguishable live.

| Situation | Use | Why (evidence) |
|---|---|---|
| Known DAGs on a heterogeneous cluster | HEFT-RA | Lowest mean regret in every workflow and cluster family (fresh holdout 0.37%); live: fastest of nine policies in 8 of 9 runs, 0.917 of HEFT |
| Data-heavy, fan-out, pipelines; edge or asymmetric networks | HEFT-RA | Contention-aware transfers and lookahead (fan-out 40.4 s -> 6.6 s); 0.00% regret on the asymmetric family |
| Homogeneous cluster | HEFT or HEFT-RA (equivalent) | 0.14% simulated regret; live 28.3 s vs 28.9 s |
| Overlapping workloads without urgent work | HEFT-RA | Best median and P95 response regret and deadline hit rate |
| Urgent workflows that may arrive while long batch work fills the cluster, where the urgent worst case matters | Adaptive; without preemption if restarts are expensive | P95 urgent response 21.0 s (75.4 s without preemption) vs 126.6 s for HEFT-RA; live critical 15.3 s vs 23.6 s; preemption raised live P95 response by 26% |
| Urgent workflows with spare capacity, where typical latency or deadline hits matter | HEFT-RA | Median urgent response 7.6 s vs 9.7 s for Adaptive; urgent deadline hit 69.47% vs 67.60% |
| Failure-prone or thermally constrained fast nodes, mixed pipelines | Adaptive or tuned Legacy | HEFT-RA's risk-neutral estimate: 18.18% regret on unreliable / mixed; 38-42% on unreliable workloads |
| Single node | Any placement; priority-aware ordering for overlapping work | All policies tie on closed DAGs; Adaptive leads overlapping work |
| Original eight-factor scorer must stay | Tuned weights, re-validated live | 0.971 of `legacy-v1` in simulation, 1.043 live |
| Start-up latency matters | Pre-pull or lazy-pull images as well | Placement recovers 1.2%; pre-pulling 7.8% makespan, about 49% time-to-running |
| Stock Kubernetes scoring | Do not use it to place DAGs; do keep a priority-first queue | 160.69% mean regret; 39.0 s vs 34.0 s live; its PrioritySort serves urgent work 1.48x faster than rank order |

The most promising next policy is a priority-aware HEFT-RA: order by
priority class first and by upward rank within a class, keep HEFT-RA's
placement. It should combine Adaptive's urgent response with HEFT-RA's
makespan.

---

## 11. Claims withdrawn or corrected

| Earlier claim | Status now | Why |
|---|---|---|
| HEFT has the lowest closed-DAG mean regret (1.69%) and P95 regret (5.75%). | Superseded. | HEFT-RA leads (holdout 0.39% / 0.12%). HEFT is still the best *classic* heuristic: 2.44% / 9.44% when HEFT-RA is excluded. |
| Under overlap, historical Legacy has the best deadline hit rate (60%) and Adaptive the lowest P95 response regret (35.07%). | Superseded. | HEFT-RA leads deadline hit (69.47%) and P95 response regret (63.88%); Adaptive leads only the priority-burst workload. |
| Adaptive improves P95 response regret from 38.54% to 35.07% at 0.755 interruptions per case. | Superseded. | Paired holdout mean-response ratio Adaptive / no-preemption is 0.9934; see also the priority study. |
| The contextual profile equals HEFT on holdout; HEFT kept in all 20 contexts. | Corrected. | The corrected search picks HEFT-RA for `cpu:fanout`, and HEFT-RA everywhere beats the portfolio. |
| Removing availability improves the objective by 0.000111; removing load balance, locality, type affinity worsens it by 0.074, 0.085, 0.705. | Re-measured. | Now -0.000245 (availability), -0.000516 (resource fit), +0.072 (load balance), +0.088 (locality), +2.43 (type affinity). Same qualitative ranking. |
| Tuned/original Legacy objective 0.97525 (warm) and 0.90022 (cold). | Re-measured. | 0.9759 (warm) and 0.8601 (cold), with the same selected vector. |
| "20 seeds per case" as independent evidence. | Withdrawn. | 1.5 to 4.7 distinct outcomes per case on four of five clusters. |
| `makespan / lower bound >= 1` sanity checks. | Withdrawn for old data. | The old bound was not a bound (350 of 26,250 records below it). |
| Kind: Adaptive improved urgent response; 42 preemptions; one 100 MiB transfer in 2.571 s. | Withdrawn as comparative evidence. | Every node ran tasks at the same speed, the scheduler had no DAG, timings were quantised to 1-2 s. Kept only as integration history. |
| Kind link checks (12.63 ms RTT, 11.35 MiB/s from a 100 Mbit worker). | Superseded. | The emulation was rebuilt; probes were biased by concurrency. |
| First corrected live run: HEFT-RA no better than HEFT on DAGs and worse on the priority batch. | Withdrawn. | A refresh race froze nodes for about 30 s, hitting locality-seeking policies most; the rerun with the fix has HEFT-RA fastest (0.917 of HEFT). Data kept in `results/kind-validation/pre-fix-run/` as evidence of the defect only. |
| Draft recommendation "Adaptive for workloads with urgent workflows", from mean urgent response (10.9 s vs 20.9 s). | Corrected. | Adaptive bounds the urgent tail (P95 21 s vs 127 s); HEFT-RA is faster in the typical case (median 7.6 s vs 9.7 s) and meets urgent deadlines as often (section 9.5). |
| Draft live claims "the live ordering is the one the simulator predicts" and "HEFT beats the default scoring live". | Corrected. | Only the top transfers (a HEFT-RA variant fastest in 8 of 9 runs); HEFT vs kube-default 0.930 [0.857, 1.006] is not conclusive, and EFT, close to HEFT in simulation, was last live. |
| Holdout (400-449) values for the overlapping workloads quoted before `heft-ra-cap` existed (e.g. HEFT-RA 0.00% median response regret). | Superseded. | Adding a policy changes the best-per-case reference; current values are in section 7.5. Closed-DAG values did not change because `heft-ra-cap` equals `heft-ra` there. |

---

## 12. Design choices considered and rejected

| Option | Why it was rejected |
|---|---|
| Virtual machines per node | They share the same four cores (a "fast" VM cannot be faster than the host) and cost gigabytes each; the user also asked not to use them. |
| An exact optimiser (ILP/CP) as the scheduler | DAG scheduling is NP-hard; the exhaustive oracle is only tractable for tiny workflows, and the online setting with uncertain estimates rewards fast heuristics. The oracle is kept as a reference. |
| A learned (reinforcement-learning) scheduler | Needs a lot of training data, is hard to explain, and would learn the simulator's quirks. The measured failure had an analytical cause with an analytical fix. |
| Re-tuning Adaptive's penalty weights per cluster | Fixed penalties were part of the problem; per-cluster tuning risks overfitting, and the contextual search found gains below its 0.5% threshold almost everywhere. |
| Recommending the contextual portfolio | HEFT-RA alone beats it on holdout (0.9952 vs 0.9977 relative to HEFT). |
| Special-casing the fan-in packing case | Tuning for one case on development seeds is how overfitting starts; it is recorded as a limitation instead. |
| Iteration-count work emulation | Measured to make "fast" nodes slower per task (section 8.2). |
| Random loss and jitter on live links | Three repetitions per (batch, policy) are not enough to average random loss; shaped delay and rate are deterministic and enough to make placement matter. |
| Synthetic temperatures on live nodes | All Kind nodes share one CPU package; fake signals would make policies react to noise. The fallback exists but is off by default. |
| metrics-server dependency | Not needed: the scheduler keeps its own reservations and tasks report their own runtimes. |
| A kube-scheduler framework plugin in Go | The right production form, but a Python secondary scheduler lets the live policies share models and code paths with the simulator, which is what the validation needs. |
| Argo Workflows as the DAG engine | The original plan kept Argo for dependencies. The project uses its own CRDs and controller instead, because the experiments need control over Pod creation, annotations (DAG, data provenance, gang size), the producer-local data plane, and precise timestamps. Integrating with Argo through a CWSI-like contract remains possible. |

---

## 13. Evidence map

| Artifact | Supports | Produced by |
|---|---|---|
| `results/sim/closed-*/records.jsonl`, `summary.*` | Closed-DAG results per phase (including the fresh holdout `closed-holdout2-s600-649`) | `run_experiment_lab.py run` |
| `results/sim/workload-*/workload-records.jsonl`, `workload-summary.*` | Overlapping-workload results per phase (including `workload-holdout2-s600-649`) | `run_experiment_lab.py workload` |
| `results/final-evidence-synthesis.{json,md}` | All cross-phase tables, contrasts, seed independence, family leaders | `analyze_final_evidence.py` |
| `results/mechanism-studies.{json,md}`, `mechanism-studies-records.jsonl` | Cold-start and priority/preemption studies | `run_mechanism_studies.py` |
| `results/legacy-weight-search.json` | Tuned Legacy weights and their holdout | `tune_legacy_weights.py` |
| `results/legacy-factor-ablation.json`, `legacy-factor-stress.json` | Factor importance | `run_legacy_ablation.py`, `run_legacy_factor_stress.py` |
| `results/contextual-policy-search.json`, `scenarios/policy-profiles/contextual-v1.yaml` | Portfolio search | `tune_contextual_policy.py` |
| `results/kind-validation/<profile>-cluster.json` | Applied emulation (qdiscs, caps, speeds) | `kind_lab.py create --record` |
| `results/kind-validation/<profile>.json` | Live warm-up and trials | `run_k8s_experiment.py` |
| `results/kind-validation/<profile>-twin.json` | Twin calibration and predictions | `kind_twin_predict.py` |
| `results/kind-validation/<profile>-twin-release.json` | Twin with measured slot-release latency | `kind_twin_predict.py --slot-release measured` |
| `results/kind-validation/pre-fix-run/` | Live data affected by the refresh race (evidence of the defect only) | first live run |
| `results/kind-validation/summary.{json,md}` | Integrity, live ranking, twin agreement | `analyze_kind_validation.py` |
| `results/run1.txt`, `run2.txt`, `run34.txt` | Phase A history only | historical scripts |
| `results/archive/pre-correction-2026-10-03.zip` | Everything before the correction round | archive |

---

## 14. Open work, in priority order

1. A priority-aware HEFT-RA (priority class first, upward rank within a
   class) to combine urgent-work response with HEFT-RA's makespan.
2. A risk-averse HEFT-RA (quantile or variance-penalised failure term),
   evaluated on the same protocol.
3. Group-level packing for fan-ins on homogeneous clusters.
4. Physical validation on a k3s cluster of separate machines (scripts in
   `scripts/`), including real thermal and failure behaviour.
5. A warm-up that includes DAG edges, so the twin can calibrate per-edge
   control-plane latency.
6. A kube-scheduler framework plugin (Score, Permit for gangs, PostFilter
   for preemption) and integration with Argo through a CWSI-like
   interface.
7. Quota-based admission control (Kueue or DRF-style).
8. Learned runtime models (input size and features) instead of
   per-template medians.
9. A CUBIC/BBR option in the network model.

---

## 15. Glossary

- **DAG** - directed acyclic graph of tasks and data dependencies.
- **EFT** - earliest finish time; place each task where it is estimated to
  finish first.
- **HEFT** - EFT placement with tasks ordered by upward rank.
- **Upward rank** - a task's average cost plus the longest
  (communication + rank) path through its children; approximates the
  remaining critical path.
- **HEFT-RA** - HEFT with resource-aware estimates (contention, shared
  image pulls, heat, failures) and one-level lookahead.
- **Regret** - relative distance from the best deployable policy on the
  same case.
- **P95** - 95th percentile.
- **Holdout** - data not looked at until all choices were frozen.
- **Uncertainty floor** - minimum per-parameter perturbation applied per
  seed so that every seed is a distinct cluster instance.
- **Max-min fairness** - allocation in which no flow can get more without
  taking from a flow that has less.
- **Mathis bound** - steady-state TCP throughput limit under random loss,
  `MSS * sqrt(3/2) / (RTT * sqrt(p))`.
- **SMT** - simultaneous multithreading (Hyper-Threading).
- **Kind** - Kubernetes in Docker: nodes are containers.
- **netem** - Linux traffic-control discipline that adds delay, rate
  limits, loss and jitter.
- **Twin** - simulator model of an emulated Kind cluster, calibrated on
  its warm-up.
- **Gang** - tasks that must start together.
- **Preemption** - stopping lower-priority work to free resources; here
  always kill-and-restart.
