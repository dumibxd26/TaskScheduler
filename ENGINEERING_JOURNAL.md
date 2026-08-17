# TaskScheduler Engineering Journal

This journal records why the current system has its shape, what failed during integration, what evidence was produced, and which claims were deliberately rejected. It complements the architecture documents; it is not a replacement for code or raw experiment artifacts.

Evidence snapshot: 2026-08-17.

## 1. Evidence policy

The project has three evidence levels:

1. **Controlled simulation**: deterministic discrete-event runs under explicit scenario assumptions. This is the comparative policy evidence.
2. **Same-host Kubernetes**: real CRD reconciliation, Pod creation, custom scheduling, binding, deletion, retry, data movement, and profile learning in Kind. This is integration evidence and descriptive live performance evidence.
3. **Physical multi-machine Kubernetes**: planned through k3s scripts, but not executed for the retained evaluation. No current result should be described as physical-cluster validation.

A claim belongs to the weakest level that actually supports it. Generated manifests and deployable scripts are implementation evidence, not proof that the deployment was run.

## 2. Architecture decisions

### D1. Keep simulator and live control plane separate

**Decision:** Maintain independent simulator and Kubernetes policy implementations over partly shared domain models.

**Rationale:** The simulator needs complete DAGs, deterministic virtual time, direct scenario parameters, and fluid resource equations. The live scheduler operates on Kubernetes observations, learned profiles, annotations, and the ready-Pod frontier.

**Consequence:** Results can be compared by mechanism and policy tendency, but the two surfaces are not numerically interchangeable and do not run identical algorithm code.

### D2. Make assumptions executable

**Decision:** Represent clusters, workflows, and overlapping arrival traces as validated scenario files.

**Rationale:** CPU, memory, storage, network, thermal, failure, checkpoint, release, and gang behavior must be reproducible rather than hidden in experiment scripts.

**Consequence:** The definitive simulation corpus can be regenerated from `scenarios/` and `run_experiment_lab.py`.

### D3. Use a deterministic discrete-event engine

**Decision:** Advance virtual time to releases, completions, transfers, thermal steps, failures, repairs, and checkpoints instead of sleeping in wall-clock time.

**Rationale:** A broad matrix needs reproducibility, speed, and direct control of uncertainty.

**Consequence:** The engine can compare thousands of cases cheaply, but its resource-sharing and overlap assumptions remain model assumptions rather than measurements of a specific kernel.

### D4. Compare a policy portfolio, not only Adaptive

**Decision:** Implement FCFS, seeded Random, EFT, HEFT, the historical simulator Legacy heuristic, exact Legacy v1, tuned exact Legacy, Adaptive, and Adaptive without preemption.

**Rationale:** A new policy is not meaningful without simple controls, established list heuristics, the project's previous simulator heuristic, the actual live scorer, a tuned version of that scorer, and a mechanism ablation.

**Consequence:** The evidence identifies specialists and Pareto tradeoffs instead of forcing a universal-winner claim.

### D5. Preserve an exact preemption ablation

**Decision:** `adaptive-no-preempt` uses the same simulator ordering and placement score as Adaptive with priority preemption disabled.

**Rationale:** This isolates interruption effects from score changes.

**Consequence:** In confirmation, both variants share median makespan, energy, and deadline rate. Adaptive improves P95 response regret from 38.54% to 35.07% at 0.755 interruptions per case, so preemption changes the response tail rather than the broad median result.

### D6. Split DAG lifecycle from placement

**Decision:** The controller owns dependency readiness and Workflow status; the scheduler owns ready-Pod ordering, placement, binding, profile updates, and victim deletion.

**Rationale:** Kubernetes controllers and schedulers have different reconciliation responsibilities. Keeping the split explicit makes retry and failure propagation testable.

**Consequence:** The controller knows the complete CRD DAG, but the current scheduler adapter reconstructs workflow templates with no future edges. Live Adaptive therefore does not validate full HEFT upward ranking.

### D7. Use producer-local storage and explicit provenance

**Decision:** Store outputs under node-local `/var/lib/ts-data`, expose them through a per-node fileserver, and propagate producer node plus byte metadata to children.

**Rationale:** A shared volume would erase the locality decision and make transfer estimates untestable.

**Consequence:** Same-node consumers read locally; remote consumers fetch over HTTP. The live experiment captured one real 100 MiB remote transfer in 2.571 s and four local reads across both policies.

### D8. Learn task-template/node profiles online

**Decision:** Persist startup, runtime, and failure observations by task template and node. Exact Legacy v1, tuned Legacy, and Adaptive force three observations per compatible node. Adaptive then applies an optimistic UCB cost adjustment; both Legacy variants retain a 10% random exploration branch after coverage.

**Rationale:** Cold-start choices should not permanently lock a recurring task onto the first observed node.

**Consequence:** Profile learning is a live-only mechanism in the current comparative design. Simulator Adaptive reads scenario parameters directly and does not perform UCB learning within a run.

### D9. Make live preemption explicit kill/restart

**Decision:** Select only strictly lower-priority victims under per-node and per-task anti-thrashing budgets, delete their Pods, and let the controller recreate interrupted tasks.

**Rationale:** Kubernetes cannot migrate arbitrary process memory. Describing deletion as migration or checkpoint restore would be false.

**Consequence:** Adaptive improved urgent response in Kind but discarded pressure-task progress and increased total makespan. Final container restart counters remain zero because recreation produces new Pods.

### D10. Treat Kind as same-host systems evidence

**Decision:** Use one control-plane container and three worker containers with resource caps and producer-local disks on one Windows/Docker host.

**Rationale:** This environment is repeatable and exercises the real Kubernetes API and control path.

**Consequence:** It does not create independent hardware, links, thermal domains, or failure domains. Thermal annotations in the retained trial are synthetic signal inputs.

### D11. Recommend by objective

**Decision:** Do not select one project-wide winner.

**Rationale:** The policy ordering changes across makespan, deadline success, response, energy, and restart cost.

**Consequence:** Recommendations must identify both the execution surface and objective. HEFT/EFT are candidates for known static DAGs, Adaptive is a candidate for urgent response when restart cost is acceptable, and tuned exact Legacy is preferred over exact Legacy v1 when the live eight-factor scorer is required. The historical simulator Legacy result cannot be advertised as a result for the live Legacy implementation.

### D12. Give each Legacy implementation a distinct identity

**Decision:** Preserve three names in simulation and two weight choices in Kubernetes:

- `legacy` is the historical simulator approximation. It starts from EFT-like timing and adds simulator-specific bonuses and penalties. It is useful for comparison with earlier project results, but it is not the live eight-factor equation.
- `legacy-v1` is the exact live eight-factor scorer with the original fixed weights.
- `legacy-tuned` is the same exact scorer, exploration rule, and profile mechanism with only the eight weights changed.
- In the live policy factory, `legacy` is a backward-compatible alias for the original exact scorer, so live artifacts use `legacy-v1` when the distinction matters.

**Rationale:** The earlier documentation used “Legacy” for two different formulas. That made simulator success look like validation of the live scorer and made weight tuning impossible to interpret.

**Consequence:** Every result and recommendation must include the variant name. Comparisons between `legacy-v1` and `legacy-tuned` isolate weights; comparisons with historical `legacy` also change the algorithm.

### D13. Tune weights on disjoint evidence and fixed scale

**Decision:** Search seeded log-normal candidates with the sum of absolute weights fixed at 125, select finalists on training cases, choose among them on validation cases, and report the selected vector on untouched holdout cases.

**Rationale:** Multiplying all eight weights by a common scalar does not change placement order, so unconstrained scale creates duplicate policies. Reusing search cases for the final claim would overstate generalisation.

**Consequence:** The retained search has 256 candidates, 90 training cases, 45 validation cases, and 75 holdout cases. Its scalar objective is fixed before search: 45% mean makespan ratio, 20% P95 makespan ratio, 15% mean response ratio, 10% energy ratio, 5% transfer ratio, and 5% deadline-lateness ratio. This is a reproducible finite search, not proof of a global optimum.

### D14. Separate simulator load from live timing and balance live order

**Decision:** Run CPU-intensive simulation before Kubernetes timing, never concurrently, and rotate three live policies cyclically by repetition.

**Rationale:** All Kind workers and the simulator share one PC. Concurrent simulation would contaminate wall-clock measurements. A simple alternating order balances two policies but not three.

**Consequence:** The three-policy harness uses Latin rotation: v1/tuned/Adaptive, tuned/Adaptive/v1, then Adaptive/v1/tuned. Each topology is recreated before its trials, and every result records the topology name and exact Legacy weights.

## 3. Integration incident log

### I1. Cleanup races returned HTTP 404

**Symptom:** Repeated live trials could fail while deleting objects that had already disappeared.

**Root cause:** Cleanup assumed every listed resource would still exist at deletion time.

**Fix:** Make deletion idempotent and accept Not Found during cleanup.

**Lesson:** Teardown races are normal in an eventually consistent control plane; cleanup must describe a desired absence.

### I2. Shared tmpfs exhausted during data tests

**Symptom:** Larger generated outputs exhausted the shared in-memory filesystem.

**Root cause:** The original data path used a shared temporary mount, which also invalidated locality experiments.

**Fix:** Move outputs to disk-backed, node-local storage and serve remote files from the producer node.

**Lesson:** Storage topology is part of scheduler semantics, not only deployment plumbing.

### I3. Timestamp arithmetic mixed naive and aware values

**Symptom:** Live duration calculation failed or produced inconsistent comparisons.

**Root cause:** Kubernetes timestamps and locally constructed values used different timezone conventions.

**Fix:** Parse and emit UTC-aware timestamps consistently.

**Lesson:** Experiment timestamps are data contracts and need one canonical timezone.

### I4. Live metadata contracts were missing or stale

**Symptom:** The scheduler could not reliably reconstruct task type, workflow identity, producer locations, or expected bytes from Pods.

**Root cause:** Controller and scheduler evolved independently without a complete annotation contract.

**Fix:** Add explicit task, workflow, priority, data-location, and output-size metadata and parse it in the live scheduler adapter.

**Lesson:** Cross-process model reconstruction must be versioned and tested like an API.

### I5. Transfer cost lacked producer-node locality

**Symptom:** ECT could price bytes but did not know which candidate placement made a dependency local.

**Root cause:** Output size and producer identity were not propagated together.

**Fix:** Carry `input_bytes_by_node` into the live task model and charge only bytes whose producer differs from the candidate node.

**Lesson:** Data amount without location is insufficient for locality-aware scheduling.

### I6. Planned preemptions were not applied to Kubernetes

**Symptom:** The policy emitted victim actions, but running Pods remained in place.

**Root cause:** The scheduling adapter handled placements and bindings but had no live action callback for preemption.

**Fix:** Wire victim actions to Pod deletion, record the preemption, and rely on controller retry.

**Lesson:** A policy decision object is not systems behavior until the adapter applies it and observes the result.

### I7. Failed ancestors left descendants waiting forever

**Symptom:** A workflow could remain non-terminal after an unrecoverable parent failure.

**Root cause:** Readiness checked successful parents, but terminal failure was not recursively propagated through blocked descendants.

**Fix:** Mark blocked descendants failed and continue propagation until the DAG reaches a terminal state.

**Lesson:** DAG controllers need both readiness propagation and impossibility propagation.

### I8. Root Kustomize entry point was invalid

**Symptom:** The documented install path did not render the complete deployment.

**Root cause:** Resource aggregation and paths were inconsistent at the repository root.

**Fix:** Add a valid root `manifests/kustomization.yaml` composition and validate rendered and server-dry-run output.

**Lesson:** Individual valid YAML files do not prove that the installation entry point works.

### I9. Thesis build depended on absent assets and a TikZ library

**Symptom:** LaTeX failed when logos were absent and when coordinate arithmetic used an unloaded TikZ library.

**Root cause:** The template assumed local binary assets and implicit TikZ support.

**Fix:** Add logo fallbacks and load the TikZ `calc` library.

**Lesson:** The paper is a build artifact and needs the same reproducibility standard as code.

### I10. The first exact-Legacy adapter used wall-clock availability

**Symptom:** Replaying the same simulated case could change the availability component because the live scorer subtracted `datetime.now()` from running-task timestamps.

**Root cause:** The exact scorer had been moved into a deterministic simulator without replacing its real-time running-task view.

**Fix:** Introduce a deterministic running-task adapter whose elapsed time comes from simulator virtual time. Add a replay test that compares makespan and every placement across repeated runs.

**Lesson:** Reusing an equation is not enough; every clock and observation feeding that equation must cross the abstraction boundary deliberately.

### I11. A four-candidate search was too weak to tune weights

**Symptom:** The smoke search retained the original vector and provided no useful evidence that the search space had been explored.

**Root cause:** Four candidates and three finalists were intended only to validate the pipeline, not to optimize an eight-dimensional score.

**Fix:** Keep the smoke artifact as a pipeline check, then run 256 normalized candidates and validate only the top 25 on a separate split.

**Lesson:** A successful optimization program is not itself a meaningful optimization experiment.

### I12. The full search exceeded the command window but did not fail

**Symptom:** The execution tool stopped waiting at 120 seconds, creating the appearance of a timeout.

**Root cause:** Candidate evaluation required about 132.5 seconds on this machine; the process continued in its persistent terminal.

**Fix:** Preserve the process, inspect its terminal once, and retain the completed JSON artifact instead of restarting or truncating the search.

**Lesson:** Distinguish a client wait limit from process failure before rerunning an expensive deterministic experiment.

### I13. Broad ablation could not activate every factor

**Symptom:** Removing warm-image, failure-penalty, or memory-pressure weights produced exactly the same aggregate result as the full tuned vector across 750 fresh-seed cases.

**Root cause:** Those factors rarely changed the node ordering in the broad corpus; an aggregate tie does not prove that their implementations are inert.

**Fix:** Add controlled two-node interventions that hold other terms constant enough for each factor to reverse one placement. Warm image changes `cold` to `warm`, failure history changes `risky` to `healthy`, and memory pressure changes `pressured` to `safe`.

**Lesson:** Use broad ablation to measure prevalence and controlled intervention to establish causal activation. Neither substitutes for the other.

## 4. Chronological experiment record

### Phase A: inherited live scheduler and direct Pod runs

The repository began with a custom scheduler, three synthetic task containers, two cluster profiles, and a sequential workflow submission script. The retained `results/run1.txt` run submitted three three-task workflows and completed all nine tasks across five workers. `results/run2.txt` increased this to ten workflows and thirty successful tasks. These runs established that custom binding and repeated placement worked, but they had no controlled policy baseline, no DAG CRD, and no isolated experiment protocol.

A later 100 MiB output and larger memory-stress run, retained in `results/run34.txt`, produced multiple terminal task failures concentrated on an overloaded CPU worker. It exposed the limits of direct submission and host resource assumptions. The failure was not promoted as algorithm evidence; it motivated explicit resource schemas, failure propagation, idempotent cleanup, and a repeatable control plane.

### Phase B: audit and deterministic simulator

The first audit traced the live score, profile persistence, task scripts, deployment manifests, and old result logs. The main gap was evidential: there was runnable code but no way to compare scheduling policies over the same cluster/workflow conditions. The simulator was therefore built around validated cluster, workflow, and workload schemas and a discrete-event engine. Focused tests were added for CPU, memory/cache, storage, routed transfer, image startup, thermal throttling, failure/repair, checkpoints, gang admission, releases, deadlines, and preemption.

FCFS and seeded Random provided controls; EFT and full-DAG HEFT provided established list baselines; historical simulator Legacy preserved the project comparison point; Adaptive and `adaptive-no-preempt` separated score effects from interruption. Small `lab-smoke`, `lab-smoke-v2`, `workload-smoke`, and `new-trace-smoke` artifacts checked serialization, summaries, uncertainty sampling, new arrival traces, and failure termination before large matrices were launched.

### Phase C: first broad simulation matrices

The first definitive matrices crossed five cluster families with fifteen closed-DAG families and four overlapping-arrival traces. Seeds 0--19 sampled declared calibration uncertainty deterministically. The initial seven-policy outputs were retained under `results/final-single-20seed/` and `results/final-workload-20seed/`. Later all-version reruns added exact Legacy v1 and tuned Legacy without overwriting those artifacts, producing `results/final-all-legacy-20seed/` and `results/final-all-workload-20seed/`.

The closed matrix also ran exhaustive static assignment on tractable workflows. During final synthesis, oracle rows were found to have entered the exploratory regret denominator while no oracle was present in confirmation. The corrected report now computes deployable-policy regret against the best deployable policy and reports distance to the static oracle separately on its 300-case subset. This changed regret magnitudes, but not the leading deployable order.

### Phase D: Kubernetes control-plane integration

The direct submission path was replaced by Workflow and TaskTemplate CRDs, a dependency controller, and a scheduler adapter. Integration work then exercised and repaired cleanup races, timestamp normalization, annotation contracts, producer-local provenance, real victim deletion, recursive descendant failure, Kustomize composition, and reproducible image builds. Node-local output storage plus per-node HTTP fileservers replaced shared temporary storage so locality had observable semantics.

Smoke trials established that all five live workflow shapes could complete through the CRD/controller/scheduler path. The balanced comparison then ran original live Legacy and Adaptive three times each. The harness alternated order, isolated profile files by trial, verified selected policy from logs, and retained every workflow, task, placement, transfer, preemption, and error record in `results/k8s-kind/policy-comparison.json`.

### Phase E: Legacy fairness audit and exact adapter

The live comparison raised a fairness question: Legacy learns profiles, but its eight score weights had never been optimized. Inspection also showed that historical simulator Legacy was only an approximation of the live formula. The live scorer was parameterized without changing its default vector, and an adapter was built to run that exact scorer against simulator nodes, tasks, images, data locations, running work, and profiles.

The first adapter inherited wall-clock availability and failed the determinism standard. It was replaced by virtual elapsed time and verified by exact placement/makespan replay. A seeded profile builder then generated twelve independent observations per compatible task/node, allowing both cold-profile and warm-profile evaluation without using live run history as hidden state.

### Phase F: weight search, holdout, and policy integration

The four-candidate smoke search validated generation, normalization, evaluation, selection, and JSON output, but was explicitly rejected as optimization evidence. The full run evaluated 256 seeded log-normal vectors at absolute scale 125 on 90 training cases, advanced 25 finalists to 45 validation cases, and selected the best vector satisfying retry, deadline, objective, and P95 guardrails. The complete run took about 132.5 seconds.

On 75 untouched cases, tuned/original objective was 0.97525 with warm profiles and 0.90022 with cold profiles. Warm-profile mean makespan fell from 43.2603 s to 42.8193 s, mean response from 37.1900 s to 36.9444 s, energy by 1.87%, and transfer from 434.667 MiB to 272.533 MiB; P95 makespan was unchanged. The selected vector was registered as `legacy-tuned`, the original exact vector as `legacy-v1`, and historical simulator `legacy` was deliberately preserved.

### Phase G: confirmation and factor studies

The all-version exploratory matrices compared nine policies. A fresh seeds 200--249 closed-DAG run then evaluated the seven serious common policies over 3,750 cases and 26,250 records. Recomputing exploratory and confirmatory regret over the same deployable set showed stable means and tails; an apparent discrepancy was traced to oracle rows in the old exploratory denominator, not to seed instability.

A leave-one-factor-out study used seeds 300--309 over all 750 cluster/workflow cases. Removing availability improved the scalar objective by only 0.000111, too little to justify changing the chosen vector after holdout selection. Removing load balance, locality, and type affinity worsened it by 0.07363, 0.08478, and 0.70451 respectively. Warm image, failure penalty, and memory pressure tied the full vector in this broad corpus, so controlled factor-activation cases were added rather than deleting them. All three controlled cases changed placement in the expected direction.

### Phase H: expanded confirmation and topology validation

The final confidence phase completed an untouched seeds 200--249 overlapping-workload matrix and deterministic aggregate/family synthesis. The generated report preserves all-policy exploration, recomputes a common-policy exploratory view, confirms rank stability, reports paired tuned/v1 intervals, and keeps static-oracle gaps separate. Its retained artifacts are `results/final-evidence-synthesis.json` and `.md`.

The remaining live step recreates three same-host Kind topology mimics: balanced heterogeneous, homogeneous, and strongly resource-skewed. It compares exact Legacy v1, tuned exact Legacy, and Adaptive in balanced cyclic order. Results belong in the definitive section only after every retained artifact passes completion and integrity checks.

## 5. Definitive experiments

### Legacy weight search

- Inputs: 256 normalized candidates; 90 training, 45 validation, and 75 untouched holdout cases; 12 seeded warm-profile observations per compatible task/node.
- Artifact: `results/legacy-weight-search.json`.
- Selection: tuned Legacy is the best eligible validation finalist with objective 0.95898 versus Legacy v1.
- Warm holdout: objective 0.97525; mean makespan 42.8193 s versus 43.2603 s; mean response 36.9444 s versus 37.1900 s; mean transfer 272.533 MiB versus 434.667 MiB; P95 makespan unchanged.
- Cold holdout: objective 0.90022; mean makespan 45.7993 s versus 49.5156 s; retries 5 versus 10; P95 makespan unchanged.
- Scope: best observed guardrail-compliant vector for the fixed search and objective, not a global optimum.

### Closed DAG matrices

- Exploratory inputs: 5 cluster families, 15 workflow families, seeds 0-19, and 9 deployable policies.
- Exploratory records: 13,500 deployable rows plus 300 exhaustive static-assignment references over 1,500 cases.
- Confirmatory inputs: untouched seeds 200-249 and 7 serious policies.
- Confirmatory records: 26,250 rows over 3,750 cases.
- Artifacts: `results/final-all-legacy-20seed/` and `results/confirmatory-single-seeds200-249/`.
- Main result: HEFT remains the strongest observed closed-DAG default. On confirmation its deployable-policy mean regret is 1.6902% (95% bootstrap interval 1.4046%-1.9832%), P95 regret is 5.7504%, and win/tie rate is 91.9733%. EFT is close at 1.8500% mean regret but has a 10.0507% P95 tail.
- Stability: all seven common policies retain the same exploratory and confirmatory rank.
- Legacy distinction: historical simulator Legacy ranks third. Tuned/v1 paired makespan ratio is 0.9714 (95% bootstrap interval 0.9677-0.9751), with 550 wins, 3,150 ties, and 50 losses; tuned does not overtake HEFT/EFT globally.
- Reporting correction: deployable regret excludes the static oracle. Oracle gaps are reported separately on the 300 tractable cases.

### Overlapping workload matrices

- Exploratory inputs: the same 5 clusters, 4 arrival traces, seeds 0-19, and 9 policies; 3,600 rows over 400 cases.
- Confirmatory inputs: untouched seeds 200-249 and 7 policies; 7,000 rows over 1,000 cases.
- Artifacts: `results/final-all-workload-20seed/` and `results/confirmatory-workload-seeds200-249/`.
- Stable objective leaders: historical simulator Legacy has the highest deadline hit rate (60.00%); Adaptive has the lowest confirmatory P95 response regret (35.07%); Adaptive, its no-preemption ablation, EFT, and HEFT share the 55.070 s median makespan; EFT/HEFT have the lowest median energy (3080.432 J).
- Stability: all seven common policies retain the same exploratory and confirmatory rank.
- Preemption: Adaptive averages 0.755 interruptions per confirmatory case. Its P95 response regret is 3.47 percentage points lower than the no-preemption ablation, while their median makespan and energy are equal.
- Exact Legacy: tuned/v1 paired mean-response ratio is 0.9370 (95% bootstrap interval 0.9303-0.9433), with 474 wins, 351 ties, and 175 losses. Tuned also improves v1 from 3.26% to 1.08% median response regret, 77.51% to 54.70% P95 regret, 46.67% to 53.33% deadline hit rate, and 67.084 s to 62.421 s median makespan. It still does not become the overall concurrent-workload default.

### Legacy factor evidence

- Broad ablation: 750 fresh-seed cases per variant under seeds 300-309.
- Consequential terms: removing type affinity, data locality, or load balance worsens objective by 0.70451, 0.08478, and 0.07363 respectively.
- Near-neutral terms: removing availability improves objective by 0.000111 and removing resource fit worsens it by 0.000114. These post-selection differences are too small to justify replacing the holdout-selected vector.
- Inactive-in-corpus terms: warm image, failure penalty, and memory pressure tie tuned Legacy in the broad matrix, but each reverses placement under its controlled intervention.
- Artifacts: `results/legacy-factor-ablation.json` and `results/legacy-factor-stress.json`.

### Earlier balanced Kind pilot

- Inputs: Legacy and Adaptive, 3 alternating-order repetitions per policy, 5 workflows and 20 final tasks per trial.
- Records: 6 trials, 30 completed workflows, and 120 successful final tasks.
- Artifact: `results/k8s-kind/policy-comparison.json`.
- Main result: Adaptive lowers median workflow response from 75.585 s to 56.555 s and critical response from 41.336 s to 35.864 s. It raises median trial makespan from 103.251 s to 125.244 s and performs 17 kill/restart preemptions. Legacy performs none.
- Integrity: no failed final tasks, final container restarts, scheduler error lines, or top-level experiment error.

This pilot compares the original exact live Legacy vector with Adaptive. It remains useful mechanism evidence for kill/restart preemption, but it is not the final v1/tuned/Adaptive topology matrix and is not merged with that matrix's timing summaries.

### Final Kind topology matrix

The definitive local systems tier completed with seed 2026. It recreates the `small` (balanced), `homogeneous`, and `skewed` logical worker-capacity profiles. Within each profile it runs Legacy v1, tuned Legacy, and Adaptive for three repetitions each, rotating the policy that executes first. Profiles persist only within one policy/topology sequence. Every trial submits the same five workflow shapes and expects 20 final task records.

Retained artifacts:

- Raw topology documents: `results/k8s-kind-topologies/small.json`, `homogeneous.json`, and `skewed.json`.
- Strict generated summaries: `results/k8s-kind-topologies/summary.json` and `summary.md`.
- Runner: `scripts/run-kind-topology-matrix.ps1`.
- Integrity analyzer: `analyze_kind_topologies.py`.

The analyzer accepts all three raw documents. The accepted evidence contains 27/27 trials, 135/135 completed workflows, and 540/540 successful final task records. Every trial has 20 final tasks and at least 20 observed bindings. Policy metadata, exact Legacy vectors, repetition coverage, and profile isolation match the declared design. There are zero final task failures, final container restarts, scheduler error lines, collection errors, and top-level experiment errors.

| Logical profile | Policy | Median workflow (s) | P95 workflow (s) | Critical (s) | Makespan (s) | Preemptions |
|---|---|---:|---:|---:|---:|---:|
| homogeneous | Adaptive | 34.203 | 67.047 | 27.454 | 65.701 | 15 |
| homogeneous | Legacy tuned | 47.527 | 54.558 | 28.187 | 57.083 | 0 |
| homogeneous | Legacy v1 | 47.527 | 53.517 | 28.690 | 59.189 | 0 |
| skewed | Adaptive | 33.978 | 67.579 | 32.115 | 65.734 | 15 |
| skewed | Legacy tuned | 42.992 | 51.401 | 23.558 | 57.382 | 0 |
| skewed | Legacy v1 | 47.443 | 60.388 | 32.254 | 61.560 | 0 |
| small/balanced | Adaptive | 44.372 | 90.339 | 29.243 | 88.361 | 14 |
| small/balanced | Legacy tuned | 50.427 | 71.121 | 31.558 | 73.933 | 0 |
| small/balanced | Legacy v1 | 52.863 | 73.859 | 30.618 | 77.948 | 0 |

Aggregate descriptive results over nine trials per policy are:

| Policy | Completed workflows/tasks | Median workflow (s) | P95 workflow (s) | Critical (s) | Makespan (s) | Preemptions |
|---|---:|---:|---:|---:|---:|---:|
| Adaptive | 45/180 | 38.307 | 70.993 | 28.245 | 67.047 | 44 |
| Legacy tuned | 45/180 | 47.527 | 69.812 | 27.821 | 58.401 | 0 |
| Legacy v1 | 45/180 | 47.665 | 70.948 | 29.431 | 60.517 | 0 |

The exact tuned scorer lowers v1 makespan in 3/3 profiles: 3.56% on homogeneous, 6.79% on skewed, and 5.15% on small. In aggregate it changes median workflow response by -0.29%, P95 response by -1.60%, critical response by -5.47%, and makespan by -3.50%. Tuned is not pointwise better: it ties homogeneous median response, worsens homogeneous P95 by 1.94%, and worsens small critical response by 3.07%. The supported conclusion is aggregate and objective-specific improvement, not dominance.

Adaptive lowers aggregate median workflow response by 19.40% relative to tuned Legacy and has the lowest median in all three profiles. It raises aggregate P95 response by 1.69%, critical response by 1.52%, and makespan by 14.80%; its makespan is higher in all three profiles. Aggregate shape medians explain the trade:

| Shape | Legacy v1 (s) | Legacy tuned (s) | Adaptive (s) |
|---|---:|---:|---:|
| pressure | 50.994 | 47.527 | 67.047 |
| critical | 29.431 | 27.821 | 28.245 |
| pipeline | 52.863 | 50.419 | 38.173 |
| fan-out | 50.828 | 50.382 | 43.634 |
| parallel | 41.121 | 44.240 | 29.871 |

Adaptive's 44 victims are all `pressure-long-*` Pods. Deleting them releases logical capacity for pipeline, fan-out, parallel, and some critical work, while controller recreation discards their in-process CPU work. This observed mechanism explains the lower regular-workflow median and the higher pressure median and trial makespan. Neither exact Legacy variant preempts.

The data path records five remote fetches and four local reads for Adaptive, four remote and five local for tuned Legacy, and eight remote and one local for v1. Measured transfer-time totals are 4.487 s, 5.453 s, and 11.354 s respectively. These observations validate producer provenance, local selection, peer fileserver access, task-side HTTP fetch, and collection. They do not compare physical networks because all workers share one host.

Learning sequences are not monotonic. Across-profile median makespan by repetition is 58.401, 57.292, and 59.440 s for tuned Legacy; 61.560, 64.453, and 59.863 s for v1; and 67.579, 64.351, and 65.734 s for Adaptive. Mean response is also non-monotonic. Persisted profiles demonstrably affect later inputs and choices, but run position, image/cache state, shared-host interference, and exploration co-vary. The matrix therefore rejects a guarantee that profile accumulation makes every next run faster.

This matrix is descriptive same-PC systems evidence. It validates the Kubernetes control path, exact policy selection, profile persistence, binding, retry preemption, and local/remote data paths across three logical capacity configurations. It does not establish confidence intervals, independent physical-node scaling, real inter-machine bandwidth, hardware thermals, or energy behavior.

## 6. Claims rejected during audit

The following statements are not supported and must not reappear in papers or documentation:

- "Adaptive is the best scheduler." The objective and scenario family change the answer.
- "Simulator and Kubernetes run the same policy code." They are separate implementations.
- "The live experiment validates HEFT." The scheduler does not receive the complete future DAG.
- "The project was validated on a physical two-host or multi-machine k3s cluster." That stage is pending.
- "Kind validates real network or thermal behavior." Its workers share one physical host and the retained thermal values are synthetic.
- "Simulator preemption is cosmetic." It changes task state and restores checkpointed remaining work or restarts work.
- "The live comparison is against the default Kubernetes scheduler." The definitive artifact compares custom Legacy v1, tuned Legacy, and Adaptive policies.
- "Ten IID repetitions support live confidence intervals." There are three sequential repetitions per policy; live summaries are descriptive.
- "Generated resources prove integration." Only observed workflow/task/control-plane outcomes count as integration evidence.
- "Historical simulator Legacy validates the live Legacy scorer." The formulas differ; only `legacy-v1` and `legacy-tuned` use the exact live eight-factor score in simulation.
- "The tuned vector is the globally best set of weights." It is the best guardrail-compliant validation result among 256 seeded normalized candidates for one declared objective.
- "Legacy must get faster after every learned run." Coverage and profile updates change later choices, but workload interference, image state, exploration, and shared-host noise prevent monotonic wall-clock guarantees.
- "A tied leave-one-factor-out result proves the factor is useless." It proves only that the factor did not change aggregate ordering in that corpus. Controlled warm-image, failure, and memory-pressure interventions all reverse a placement.
- "Three Kind topology profiles are three physical clusters." They are three logical capacity configurations recreated as worker containers on the same PC.

## 7. Evidence map

| Claim | Owning evidence |
|---|---|
| Simulator resource and event behavior | `simulation/engine.py`, `simulation/resources.py`, focused tests |
| Full-DAG policy behavior | `simulation/policies.py`, closed and workload raw records |
| Exact Legacy scorer in simulation | `simulation/legacy_weights.py`, replay and scorer tests |
| Legacy candidate generation and objective | `simulation/weight_search.py`, `tune_legacy_weights.py` |
| Selected weights and disjoint holdout | `results/legacy-weight-search.json` |
| Factor prevalence and causal activation | `results/legacy-factor-ablation.json`, `results/legacy-factor-stress.json` |
| Live ECT, UCB, gang, and preemption decisions | `services/policy.py`, `services/ect.py`, `services/preemption.py` |
| DAG lifecycle and retry behavior | `controlplane/controller/main.py` |
| Live binding, profile persistence, and action application | `controlplane/scheduler/main.py` |
| Original seven-policy closed and overlap baselines | `results/final-single-20seed/`, `results/final-workload-20seed/` |
| Overlap ranking and objective leaders | `results/final-all-workload-20seed/`, `results/confirmatory-workload-seeds200-249/` |
| All-version exploratory ranking | `results/final-all-legacy-20seed/`, `results/final-all-workload-20seed/` |
| Fresh-seed closed confirmation | `results/confirmatory-single-seeds200-249/` |
| Fresh-seed workload confirmation | `results/confirmatory-workload-seeds200-249/` |
| Cross-phase statistical synthesis | `analyze_final_evidence.py`, `results/final-evidence-synthesis.json` |
| Live performance and integrity | `results/k8s-kind/policy-comparison.json` |
| Evidence boundaries and academic interpretation | `thesis/thesis.tex` |

## 8. Open work, in priority order

1. Run the alternating policy protocol on independent physical machines with real link, thermal, and energy measurements.
2. Pass the complete DAG or controller-computed upward ranks to the live scheduler.
3. Add and evaluate live Adaptive without preemption.
4. Make preemption benefit-aware using victim progress, restart cost, and urgent-task gain.
5. Run live mechanism ablations for UCB, locality, thermal/failure penalties, and preemption.
6. Calibrate scenario uncertainty from repeated hardware measurements and compare predicted distributions with live task and transfer observations.

## 9. Maintenance rule

When code, experiments, and prose disagree, inspect the owning code path and raw artifact first. Update summaries and architecture documents afterward. Never promote a planned feature or runnable script to an experimental result without a retained successful run.
