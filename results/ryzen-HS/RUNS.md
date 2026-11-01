# Simulation runs on the Ryzen 7 PRO 8840HS

Hardware captured: 2026-11-01T12:26:30.9139338Z. Source commit: `0f4d50bf1c0e1bacbbe4a79691574e58eff264ee`.

This is a separate rerun, not a replacement for the retained i5 evidence.

## Hardware and software

| Property | Earlier computer (documented) | This computer (measured) |
|---|---|---|
| CPU | Intel Core i5-8300H | AMD Ryzen 7 PRO 8840HS w/ Radeon 780M Graphics |
| Physical cores / logical threads | 4 / 8 | 8 / 16 |
| Reported base/max clock | 2.3 GHz base, 4.0 GHz turbo | 3.301 GHz CIM MaxClockSpeed (not measured turbo) |
| Installed RAM | 12 GiB (4 + 8), DDR4 2133 MT/s | 64 GiB (32 + 32), configured 5600 MT/s |
| OS-visible RAM | Not retained | 61.78 GiB |
| L2 / L3 | 1 / 8 MiB | 8 / 16 MiB |
| Storage | Samsung 970 EVO Plus 500 GB + 1 TB HDD | SK hynix PC801 HFS002TEJ9X101N |
| OS | Windows 11 Pro | Microsoft Windows 11 Enterprise, build 26300 |
| Python (local) | README tested version 3.12; actual old run version not recorded | Python 3.13.16 |
| Kind | README tested version 0.32; actual old run version not recorded | kind v0.31.0 go1.25.5 windows/amd64 |
| kubectl client | README tested version 1.36 | v1.36.1 |
| Docker | 29.x, WSL2; global limits not retained | 29.9.2, 16 logical CPUs, 30.25 GiB |
| Kernel / cgroups | WSL2; exact kernel not retained | 6.18.33.2-microsoft-standard-WSL2, cgroups v2 |
| Power policy | Not retained | Power Scheme GUID: fb5220ff-7e1a-47aa-9a42-50ffbf45c673  (HP Optimized (Modern Standby)) |

## Fairness and interpretation

- Primary comparison uses the original scenario YAML, policies, seeds, uncertainty floors, thermal steps, work budgets, seeded live order, warm-up protocol and three repetitions.
- Six original live trials lacked a verifiable warm-profile startup confirmation. They were discarded and rerun later in freshly recreated matched-profile clusters, using the SAME saved warm calibration and original case identifiers. The retained matrix therefore spans multiple sessions, not one uninterrupted shuffled sequence. Retry selection was based only on integrity, never observed performance.
- The simulator still models the five original cluster descriptions, including `local-i5-8300h`. Running it on Ryzen does NOT turn that modeled node into a Ryzen.
- Live workers retain the original six 500m task slots (3 task CPUs), relative emulation speeds, network shaping and per-worker Docker CPU/RAM caps. No expanded-capacity experiment was mixed into this comparison.
- Docker's global pool remains 16 logical CPUs / 30.25 GiB; it was NOT reduced to a guessed old limit or increased during these runs. The uncapped control-plane and faster underlying cores are real confounders. Equal CPU quotas do not mean equal work per second.
- Each live profile is recalibrated on this host using warm-up only. Its fresh learned profile is reset before every policy trial. Old warm-up data are not reused.
- All worker containers share this PC and Docker VM. This is NOT physical multi-host Kubernetes validation; no k3s multi-computer result is claimed.
- Local suites run sequentially, never concurrently with timed live trials. Other Windows activity is uncontrolled; the host's power plan is recorded, not changed.
- Absolute seconds and relative policy advantages answer different questions. Simulator virtual seconds, live elapsed seconds, and Python wall-clock runtime are never treated as interchangeable.
- Descriptive difference bins were fixed before analysis: <=5% small, >5-15% moderate, >15% large. These are practical reporting labels, NOT significance or equivalence tests.
- Live n=3 per policy/profile/batch is small; repetitions share a calibration. No claim of statistical equivalence or hardware-only causation is justified.
- The old raw results do not retain local command wall times, global Docker limits, background load or exact dependency/image digests. CPU execution speedup cannot be fairly quantified from virtual makespans or the approximate ten-hour README estimate.

## Matched live worker limits (verified against the old records)

| Profile | Worker | Docker CPUs | RAM cap (MiB) | CPU shares | Egress delay (ms) | Egress rate (Mbit/s) |
|---|---|---|---|---|---|---|
| balanced | ts-node-cpu | 1.35 | 1024 | 2048 | 2.0 | 800.0 |
| balanced | ts-node-edge | 0.85 | 800 | 1024 | 10.0 | 60.0 |
| balanced | ts-node-io | 0.85 | 800 | 1024 | 2.0 | 800.0 |
| balanced | ts-node-mem | 1.35 | 1024 | 2048 | 2.0 | 800.0 |
| homogeneous | ts-node-equal-1 | 1.35 | 1024 | 2048 | 4.0 | 400.0 |
| homogeneous | ts-node-equal-2 | 1.35 | 1024 | 2048 | 4.0 | 400.0 |
| homogeneous | ts-node-equal-3 | 1.35 | 1024 | 2048 | 4.0 | 400.0 |
| edge | ts-node-core-1 | 1.35 | 1024 | 2048 | 1.0 | 1000.0 |
| edge | ts-node-core-2 | 1.35 | 1024 | 2048 | 1.0 | 1000.0 |
| edge | ts-node-edge-1 | 0.85 | 800 | 1024 | 15.0 | 40.0 |
| edge | ts-node-edge-2 | 0.85 | 800 | 1024 | 15.0 | 40.0 |

These caps apply to the entire worker (tasks plus node agents). Each task is still capped at 500m CPU. The control-plane container is not worker-capped. Network shaping is deterministic egress emulation, not measured physical links.

## Local execution ledger

| Suite | Exit | Wall time (s) | Log |
|---|---|---|---|
| closed-exploratory-s0-19 | 0 | 69.77 | [closed-exploratory-s0-19.log](closed-exploratory-s0-19.log) |
| workload-exploratory-s0-19 | 0 | 77.15 | [workload-exploratory-s0-19.log](workload-exploratory-s0-19.log) |
| closed-confirmatory-s200-249 | 0 | 137.97 | [closed-confirmatory-s200-249.log](closed-confirmatory-s200-249.log) |
| workload-confirmatory-s200-249 | 0 | 173.04 | [workload-confirmatory-s200-249.log](workload-confirmatory-s200-249.log) |
| closed-holdout-s400-449 | 0 | 130.07 | [closed-holdout-s400-449.log](closed-holdout-s400-449.log) |
| workload-holdout-s400-449 | 0 | 169.96 | [workload-holdout-s400-449.log](workload-holdout-s400-449.log) |
| closed-holdout2-s600-649 | 0 | 128.87 | [closed-holdout2-s600-649.log](closed-holdout2-s600-649.log) |
| workload-holdout2-s600-649 | 0 | 178.85 | [workload-holdout2-s600-649.log](workload-holdout2-s600-649.log) |
| mechanism-studies | 0 | 76.76 | [mechanism-studies.log](mechanism-studies.log) |
| legacy-weight-search | 0 | 29.28 | [legacy-weight-search.log](legacy-weight-search.log) |
| legacy-factor-ablation | 0 | 15.32 | [legacy-factor-ablation.log](legacy-factor-ablation.log) |
| legacy-factor-stress | 0 | 0.22 | [legacy-factor-stress.log](legacy-factor-stress.log) |
| contextual-policy-search | 0 | 41.98 | [contextual-policy-search.log](contextual-policy-search.log) |

Exact argument vectors and UTC start/end times are in [commands.json](commands.json). Hardware and runtime versions are in [hardware.json](hardware.json).

Total local suite wall time: **1229.23 s** (sum of sequential commands; setup and report work excluded).

## Live Kubernetes execution

All three profiles ran 2 batches x 9 policies x 3 repetitions: **162 live trials**, plus a fresh calibration per profile. The standard analyzer's integrity gate passed. Both original and measured-slot-release calibrated twins were generated.

There were **168 executed main/repair trials**: 162 in the initial full matrix, then six integrity-only reruns replacing six unverified records. Exactly 162 trials are retained in the comparison.

| Profile | Start (UTC) | Finish (UTC) | Trials |
|---|---|---|---|
| balanced | 2026-11-01T13:08:17.587376Z | 2026-11-01T17:19:36.194443Z | 54 |
| homogeneous | 2026-11-01T14:37:06.272442Z | 2026-11-01T17:26:25.271833Z | 54 |
| edge | 2026-11-01T15:51:46.069856Z | 2026-11-01T17:40:44.711116Z | 54 |

Detailed live timings, task/transfer/preemption records, caps, network evidence, twin predictions and integrity checks: [Kind summary](kind-validation/summary.md).

Image builds, exact Kubernetes runner arguments, exit codes and wall times are recorded in [kubernetes-commands.json](kubernetes-commands.json). Current server version and image IDs/digests are recorded in [kubernetes-runtime.json](kubernetes-runtime.json). The isolated cluster used its own kubeconfig; the original user context was not changed. The temporary experiment cluster was deleted after collecting evidence.

## Coverage

- Closed DAGs: 5 clusters x 15 workflows x (20 + 50 + 50 + 50) seeds; 12 exploratory policies and 10 later policies, plus small-DAG static oracle.
- Overlapping workloads: 5 clusters x 4 workloads, the same four seed partitions.
- Cold-start/image-prewarming and urgent-workflow/preemption mechanism studies.
- Legacy weight search (256 candidates, 25 requested finalists), factor ablation (seeds 300-309) and all eight factor-activation stresses.
- Contextual policy search (64 candidates, 60 s thermal step); generated profile saved here rather than overwriting the original selected policy.
- Full same-host Kind balanced/homogeneous/edge matrix; both calibrated twin modes.
- Withdrawn pre-correction and pre-fix runs were NOT rerun as endorsed benchmarks. They are historical debugging artifacts, not the current protocol.

## Validation and preservation

Repository tests: **210 passed**, exit 0, with explicit `tests` + `test_simulation.py` and `--import-mode=importlib`. The unqualified initial pytest command failed collection because the untracked `send` copy shadowed root modules. No user files were removed to fix this.

Final complete test run after the readiness fix and comparison tooling: **216 passed**, exit 0, 12.83 s. PowerShell syntax checks passed; all historical SHA-256 hashes are unchanged; temporary experiment clusters are gone. Evidence: [validation.json](validation.json).

The first ordinary control-plane image build failed with an HTTPS handshake error against files.pythonhosted.org. A temporary build-only Dockerfile used the same requirements through the environment's HTTPS package mirror; certificate verification was not disabled. The bandwidth/thermal image builds also used that mirror. This deviation does not change scheduler source or work definitions. Unpinned base image tags and transitive packages can still differ from the old image builds, whose digests were not retained.

An initial Kind setup emitted a Windows cp1252 decoding exception on Kind's UTF-8 stderr. That attempt was stopped and archived under `setup-first-attempt` before collecting the retained trial matrix. The runner was restarted with `PYTHONUTF8=1`; the archived partial calibration is excluded from all comparisons.

The first full-matrix analyzer exited 1 because six trials recorded `warm_profile_loaded: false`. The harness had stopped reading startup logs as soon as the policy banner appeared, but the scheduler prints that banner BEFORE loading profiles. The harness now waits for the exact ConfigMap load confirmation, and records the raw startup log; missing confirmation fails before submitting work. The scheduler algorithm and work budgets were not changed.

Raw startup logs were not retained for the six original failures, so they were NOT relabeled as warm or used as valid timing evidence. All 156 unaffected trials passed the original integrity checks. The six invalid case slots were rerun with the same saved calibration and matched worker limits. Originals are preserved under `integrity-first-matrix`; retry commands and the final analyzer exit code are in [integrity-repair-commands.json](integrity-repair-commands.json). The final gate passes without waivers or relaxed checks.

| Profile | Retried case | Batch | Policy | Rerun finish (UTC) |
|---|---|---|---|---|
| balanced | t016 | dag | adaptive | 2026-11-01T17:19:36.194362Z |
| homogeneous | t053 | priority | legacy-tuned | 2026-11-01T17:26:25.271751Z |
| edge | t004 | priority | legacy-v1 | 2026-11-01T17:37:12.294667Z |
| edge | t015 | dag | heft-ra | 2026-11-01T17:38:16.763519Z |
| edge | t030 | dag | heft-ra | 2026-11-01T17:39:27.317792Z |
| edge | t038 | priority | heft-ra-cap | 2026-11-01T17:40:44.711048Z |

Historical SHA-256 fingerprints: [historical-hashes.json](historical-hashes.json). The cross-computer report is [COMPARISON.md](COMPARISON.md).

## Reproduction

Executed build-only Dockerfiles and orchestration/repair helpers are archived in [provenance](provenance/). Their absolute session paths are historical; adjust path variables when reusing them. No kubeconfig or cluster credentials are included.

Use a NEW result directory with `scripts/run-local-benchmarks.ps1`; it refuses to overwrite an existing run. For live reruns, use `scripts/run-kind-validation.ps1` with a copied profile document, a unique cluster name, a dedicated kubeconfig and a new result directory. Exact original argument vectors, timestamps, image IDs and repair selections are retained in the linked JSON ledgers.
