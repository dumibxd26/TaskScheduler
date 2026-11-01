# Cross-computer simulation comparison: i5-8300H vs Ryzen 7 PRO 8840HS

## Verdict

All local scientific results reproduce within 1e-9 relative/absolute tolerance.

Live Kubernetes results are measured separately below; numerical identity is not expected. A small difference is not proof of equivalence.

**The answer is different for the two execution surfaces:** the local scientific results are exactly the same, while live Kubernetes timings and the strength of some policy advantages are not.

### Live difference overview

| Metric | Rows | Small | Moderate | Large | Median row delta |
|---|---|---|---|---|---|
| makespan_seconds | 54 | 25 | 23 | 6 | -5.56% |
| mean_response_seconds | 54 | 21 | 30 | 3 | -6.38% |
| p95_response_seconds | 54 | 25 | 21 | 8 | -5.81% |
| critical_response_seconds | 27 | 10 | 13 | 4 | -6.07% |

The median row delta is an unweighted descriptive summary of policy/profile/batch median changes, not a universal hardware speedup.

## Computer specifications

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

## Local matrices: keyed record-by-record comparison

Keys include cluster, workflow/workload, seed and policy; missing, extra and duplicate records cannot silently pass. Every numeric and nonnumeric field is compared. Exact means parsed values are identical, not that JSON whitespace or file bytes match.

| Suite | Old / new records | Exact | Within 1e-9 | Missing / extra | Max absolute delta |
|---|---|---|---|---|---|
| closed-exploratory-s0-19 | 18300 / 18300 | 18300 | 18300 | 0 / 0 | 0 |
| workload-exploratory-s0-19 | 4800 / 4800 | 4800 | 4800 | 0 / 0 | 0 |
| closed-confirmatory-s200-249 | 37500 / 37500 | 37500 | 37500 | 0 / 0 | 0 |
| workload-confirmatory-s200-249 | 10000 / 10000 | 10000 | 10000 | 0 / 0 | 0 |
| closed-holdout-s400-449 | 37500 / 37500 | 37500 | 37500 | 0 / 0 | 0 |
| workload-holdout-s400-449 | 10000 / 10000 | 10000 | 10000 | 0 / 0 | 0 |
| closed-holdout2-s600-649 | 37500 / 37500 | 37500 | 37500 | 0 / 0 | 0 |
| workload-holdout2-s600-649 | 10000 / 10000 | 10000 | 10000 | 0 / 0 | 0 |

## Targeted and search studies: complete JSON comparison

| Study | Scalar values | Exact differences | Outside tolerance | Structural differences |
|---|---|---|---|---|
| mechanism-studies | 374 | 0 | 0 | 0 |
| legacy-weight-search | 6132 | 0 | 0 | 0 |
| legacy-factor-ablation | 248 | 0 | 0 | 0 |
| legacy-factor-stress | 128 | 0 | 0 | 0 |
| contextual-policy-search | 2023 | 0 | 0 | 0 |

Selected weights, full leaderboards, cold/warm results, priorities and factor choices are included, not merely headline rankings.

## Live cross-host comparisons

Delta = 100 x (new median / old median - 1). Negative is faster. Old/new ranges expose run-to-run variability. Each row uses three repetitions.

### makespan_seconds

| Profile / batch | Policy | i5 median (s) | Ryzen median (s) | Delta | Size | i5 range | Ryzen range |
|---|---|---|---|---|---|---|---|
| balanced / priority | kube-default | 59.37 | 54.87 | -7.58% | moderate (5-15%) | 57.87 - 61.99 | 53.62 - 57.05 |
| balanced / priority | legacy-v1 | 61.56 | 53.20 | -13.58% | moderate (5-15%) | 56.67 - 65.22 | 52.73 - 54.84 |
| balanced / priority | legacy-tuned | 59.59 | 53.94 | -9.48% | moderate (5-15%) | 55.32 - 67.17 | 52.89 - 54.53 |
| balanced / priority | eft | 55.95 | 52.78 | -5.66% | moderate (5-15%) | 55.47 - 63.74 | 51.84 - 53.32 |
| balanced / priority | heft | 57.70 | 53.69 | -6.95% | moderate (5-15%) | 53.28 - 72.28 | 51.90 - 54.24 |
| balanced / priority | heft-ra | 59.39 | 53.28 | -10.29% | moderate (5-15%) | 57.46 - 60.59 | 52.03 - 54.48 |
| balanced / priority | heft-ra-cap | 62.78 | 52.39 | -16.56% | large (>15%) | 59.01 - 63.23 | 52.35 - 52.91 |
| balanced / priority | adaptive-no-preempt | 61.19 | 53.05 | -13.30% | moderate (5-15%) | 55.39 - 65.63 | 52.52 - 55.32 |
| balanced / priority | adaptive | 81.63 | 67.30 | -17.56% | large (>15%) | 81.36 - 83.44 | 62.86 - 83.11 |
| balanced / dag | kube-default | 45.87 | 44.13 | -3.79% | small (<=5%) | 43.52 - 48.66 | 33.84 - 46.91 |
| balanced / dag | legacy-v1 | 42.54 | 40.60 | -4.57% | small (<=5%) | 41.73 - 45.44 | 38.87 - 40.66 |
| balanced / dag | legacy-tuned | 47.57 | 40.42 | -15.04% | large (>15%) | 47.11 - 52.74 | 40.02 - 40.55 |
| balanced / dag | eft | 46.51 | 41.04 | -11.76% | moderate (5-15%) | 43.88 - 51.32 | 39.99 - 43.40 |
| balanced / dag | heft | 45.97 | 33.69 | -26.71% | large (>15%) | 42.87 - 57.48 | 32.55 - 35.07 |
| balanced / dag | heft-ra | 40.02 | 32.91 | -17.77% | large (>15%) | 33.89 - 43.25 | 32.65 - 33.64 |
| balanced / dag | heft-ra-cap | 35.88 | 34.83 | -2.93% | small (<=5%) | 34.74 - 36.31 | 32.77 - 37.09 |
| balanced / dag | adaptive-no-preempt | 39.21 | 37.99 | -3.12% | small (<=5%) | 35.97 - 48.24 | 35.16 - 39.61 |
| balanced / dag | adaptive | 43.46 | 36.51 | -15.99% | large (>15%) | 41.47 - 46.43 | 34.76 - 40.37 |
| homogeneous / priority | kube-default | 40.24 | 40.80 | +1.39% | small (<=5%) | 39.70 - 42.32 | 37.54 - 41.95 |
| homogeneous / priority | legacy-v1 | 40.46 | 40.20 | -0.66% | small (<=5%) | 40.31 - 40.87 | 39.73 - 42.74 |
| homogeneous / priority | legacy-tuned | 41.51 | 40.48 | -2.50% | small (<=5%) | 40.80 - 44.61 | 38.43 - 41.85 |
| homogeneous / priority | eft | 42.27 | 38.62 | -8.64% | moderate (5-15%) | 41.20 - 43.38 | 35.29 - 41.46 |
| homogeneous / priority | heft | 40.14 | 36.95 | -7.95% | moderate (5-15%) | 39.90 - 41.61 | 35.39 - 40.00 |
| homogeneous / priority | heft-ra | 41.44 | 40.87 | -1.37% | small (<=5%) | 40.60 - 41.44 | 38.77 - 40.95 |
| homogeneous / priority | heft-ra-cap | 41.88 | 37.87 | -9.58% | moderate (5-15%) | 41.60 - 42.10 | 37.40 - 47.14 |
| homogeneous / priority | adaptive-no-preempt | 43.26 | 38.70 | -10.55% | moderate (5-15%) | 42.87 - 47.67 | 38.67 - 38.76 |
| homogeneous / priority | adaptive | 40.75 | 39.62 | -2.77% | small (<=5%) | 40.15 - 41.19 | 36.31 - 39.96 |
| homogeneous / dag | kube-default | 35.16 | 32.00 | -8.99% | moderate (5-15%) | 34.66 - 35.35 | 25.77 - 36.22 |
| homogeneous / dag | legacy-v1 | 34.35 | 34.11 | -0.69% | small (<=5%) | 33.88 - 34.70 | 28.69 - 35.85 |
| homogeneous / dag | legacy-tuned | 34.61 | 33.34 | -3.67% | small (<=5%) | 33.47 - 35.17 | 30.84 - 35.32 |
| homogeneous / dag | eft | 34.19 | 37.45 | +9.55% | moderate (5-15%) | 33.99 - 37.28 | 35.15 - 38.98 |
| homogeneous / dag | heft | 28.86 | 27.46 | -4.85% | small (<=5%) | 28.71 - 33.16 | 25.94 - 28.12 |
| homogeneous / dag | heft-ra | 28.32 | 25.85 | -8.72% | moderate (5-15%) | 27.38 - 28.38 | 25.10 - 27.27 |
| homogeneous / dag | heft-ra-cap | 29.73 | 27.89 | -6.18% | moderate (5-15%) | 28.31 - 33.22 | 27.12 - 33.14 |
| homogeneous / dag | adaptive-no-preempt | 36.59 | 34.59 | -5.46% | moderate (5-15%) | 36.21 - 38.93 | 29.32 - 40.53 |
| homogeneous / dag | adaptive | 36.42 | 32.52 | -10.72% | moderate (5-15%) | 29.53 - 36.94 | 28.15 - 32.65 |
| edge / priority | kube-default | 48.37 | 47.03 | -2.78% | small (<=5%) | 47.42 - 48.42 | 46.02 - 48.46 |
| edge / priority | legacy-v1 | 48.05 | 46.59 | -3.04% | small (<=5%) | 47.94 - 48.42 | 46.10 - 48.02 |
| edge / priority | legacy-tuned | 48.05 | 47.22 | -1.73% | small (<=5%) | 47.21 - 48.62 | 46.33 - 49.79 |
| edge / priority | eft | 47.27 | 47.32 | +0.11% | small (<=5%) | 46.97 - 47.97 | 45.95 - 47.45 |
| edge / priority | heft | 47.61 | 45.61 | -4.19% | small (<=5%) | 47.53 - 49.37 | 45.42 - 50.41 |
| edge / priority | heft-ra | 47.52 | 47.14 | -0.80% | small (<=5%) | 47.18 - 48.81 | 45.48 - 48.44 |
| edge / priority | heft-ra-cap | 47.81 | 46.70 | -2.32% | small (<=5%) | 46.56 - 48.74 | 40.58 - 48.43 |
| edge / priority | adaptive-no-preempt | 49.02 | 48.06 | -1.96% | small (<=5%) | 48.18 - 50.28 | 43.63 - 49.97 |
| edge / priority | adaptive | 51.81 | 45.17 | -12.82% | moderate (5-15%) | 48.03 - 68.23 | 42.70 - 68.68 |
| edge / dag | kube-default | 38.99 | 35.26 | -9.58% | moderate (5-15%) | 38.70 - 42.39 | 33.25 - 39.73 |
| edge / dag | legacy-v1 | 39.30 | 38.87 | -1.09% | small (<=5%) | 35.23 - 40.26 | 35.23 - 41.37 |
| edge / dag | legacy-tuned | 38.11 | 35.16 | -7.74% | moderate (5-15%) | 34.50 - 41.09 | 32.06 - 41.28 |
| edge / dag | eft | 41.79 | 37.80 | -9.55% | moderate (5-15%) | 38.34 - 44.42 | 36.18 - 37.80 |
| edge / dag | heft | 36.67 | 33.77 | -7.92% | moderate (5-15%) | 29.76 - 38.37 | 29.68 - 37.34 |
| edge / dag | heft-ra | 34.46 | 33.60 | -2.50% | small (<=5%) | 34.04 - 36.18 | 28.75 - 35.15 |
| edge / dag | heft-ra-cap | 35.29 | 34.83 | -1.30% | small (<=5%) | 33.04 - 35.90 | 34.04 - 36.69 |
| edge / dag | adaptive-no-preempt | 39.01 | 39.51 | +1.28% | small (<=5%) | 35.95 - 39.18 | 35.11 - 41.64 |
| edge / dag | adaptive | 36.59 | 37.86 | +3.48% | small (<=5%) | 36.04 - 37.10 | 36.84 - 38.58 |

### mean_response_seconds

| Profile / batch | Policy | i5 median (s) | Ryzen median (s) | Delta | Size | i5 range | Ryzen range |
|---|---|---|---|---|---|---|---|
| balanced / priority | kube-default | 40.58 | 36.64 | -9.72% | moderate (5-15%) | 39.89 - 40.83 | 35.29 - 37.45 |
| balanced / priority | legacy-v1 | 42.28 | 36.41 | -13.90% | moderate (5-15%) | 38.73 - 43.49 | 35.63 - 37.68 |
| balanced / priority | legacy-tuned | 40.13 | 36.34 | -9.43% | moderate (5-15%) | 37.51 - 44.81 | 35.70 - 37.30 |
| balanced / priority | eft | 38.10 | 36.44 | -4.36% | small (<=5%) | 37.88 - 41.64 | 35.14 - 36.69 |
| balanced / priority | heft | 45.43 | 40.26 | -11.38% | moderate (5-15%) | 40.01 - 55.57 | 38.65 - 41.72 |
| balanced / priority | heft-ra | 43.87 | 40.33 | -8.08% | moderate (5-15%) | 43.70 - 47.81 | 38.28 - 42.22 |
| balanced / priority | heft-ra-cap | 47.05 | 40.36 | -14.21% | moderate (5-15%) | 45.77 - 47.81 | 39.66 - 40.82 |
| balanced / priority | adaptive-no-preempt | 42.62 | 35.66 | -16.33% | large (>15%) | 38.12 - 45.10 | 35.45 - 37.39 |
| balanced / priority | adaptive | 40.84 | 35.77 | -12.41% | moderate (5-15%) | 38.72 - 42.57 | 33.80 - 41.17 |
| balanced / dag | kube-default | 34.61 | 31.99 | -7.58% | moderate (5-15%) | 33.82 - 35.86 | 29.95 - 33.82 |
| balanced / dag | legacy-v1 | 32.32 | 30.25 | -6.42% | moderate (5-15%) | 31.34 - 34.62 | 28.84 - 32.66 |
| balanced / dag | legacy-tuned | 35.86 | 30.61 | -14.63% | moderate (5-15%) | 34.62 - 39.21 | 30.20 - 30.67 |
| balanced / dag | eft | 34.53 | 30.34 | -12.14% | moderate (5-15%) | 33.36 - 37.48 | 29.73 - 32.29 |
| balanced / dag | heft | 37.25 | 27.62 | -25.86% | large (>15%) | 34.59 - 48.22 | 27.12 - 28.99 |
| balanced / dag | heft-ra | 32.90 | 27.62 | -16.06% | large (>15%) | 28.55 - 33.68 | 26.50 - 28.54 |
| balanced / dag | heft-ra-cap | 30.29 | 29.25 | -3.42% | small (<=5%) | 30.12 - 31.19 | 26.04 - 30.44 |
| balanced / dag | adaptive-no-preempt | 31.20 | 29.23 | -6.32% | moderate (5-15%) | 30.86 - 37.08 | 28.64 - 30.74 |
| balanced / dag | adaptive | 33.00 | 29.97 | -9.18% | moderate (5-15%) | 31.52 - 34.09 | 27.29 - 30.92 |
| homogeneous / priority | kube-default | 23.88 | 23.04 | -3.49% | small (<=5%) | 23.19 - 24.27 | 22.89 - 24.98 |
| homogeneous / priority | legacy-v1 | 24.32 | 24.12 | -0.81% | small (<=5%) | 24.22 - 24.54 | 21.37 - 25.65 |
| homogeneous / priority | legacy-tuned | 24.03 | 24.81 | +3.22% | small (<=5%) | 23.91 - 27.38 | 21.81 - 25.11 |
| homogeneous / priority | eft | 24.55 | 22.52 | -8.26% | moderate (5-15%) | 24.37 - 25.62 | 20.21 - 25.10 |
| homogeneous / priority | heft | 24.74 | 22.12 | -10.60% | moderate (5-15%) | 23.87 - 25.24 | 21.72 - 23.17 |
| homogeneous / priority | heft-ra | 23.89 | 23.39 | -2.10% | small (<=5%) | 23.65 - 24.93 | 23.26 - 24.31 |
| homogeneous / priority | heft-ra-cap | 24.02 | 21.82 | -9.13% | moderate (5-15%) | 23.95 - 24.77 | 21.19 - 24.75 |
| homogeneous / priority | adaptive-no-preempt | 25.01 | 23.34 | -6.66% | moderate (5-15%) | 24.36 - 25.36 | 22.55 - 23.95 |
| homogeneous / priority | adaptive | 24.08 | 23.67 | -1.72% | small (<=5%) | 23.88 - 24.39 | 20.94 - 23.93 |
| homogeneous / dag | kube-default | 25.57 | 24.63 | -3.68% | small (<=5%) | 25.18 - 26.43 | 22.15 - 26.38 |
| homogeneous / dag | legacy-v1 | 25.01 | 25.79 | +3.11% | small (<=5%) | 24.87 - 25.06 | 25.24 - 27.51 |
| homogeneous / dag | legacy-tuned | 25.62 | 23.66 | -7.65% | moderate (5-15%) | 25.21 - 26.79 | 21.82 - 25.31 |
| homogeneous / dag | eft | 25.49 | 27.43 | +7.59% | moderate (5-15%) | 25.45 - 27.97 | 25.74 - 28.58 |
| homogeneous / dag | heft | 24.94 | 23.02 | -7.73% | moderate (5-15%) | 24.64 - 27.05 | 22.79 - 25.97 |
| homogeneous / dag | heft-ra | 24.62 | 22.09 | -10.26% | moderate (5-15%) | 24.15 - 24.65 | 21.94 - 23.43 |
| homogeneous / dag | heft-ra-cap | 25.16 | 23.82 | -5.33% | moderate (5-15%) | 25.00 - 25.68 | 23.76 - 29.00 |
| homogeneous / dag | adaptive-no-preempt | 27.44 | 25.25 | -7.99% | moderate (5-15%) | 27.18 - 27.65 | 24.18 - 28.89 |
| homogeneous / dag | adaptive | 26.56 | 25.25 | -4.93% | small (<=5%) | 25.18 - 27.87 | 24.46 - 25.36 |
| edge / priority | kube-default | 31.83 | 30.78 | -3.32% | small (<=5%) | 31.20 - 31.94 | 29.43 - 31.40 |
| edge / priority | legacy-v1 | 31.52 | 30.19 | -4.22% | small (<=5%) | 31.48 - 31.86 | 28.41 - 31.76 |
| edge / priority | legacy-tuned | 31.46 | 30.49 | -3.10% | small (<=5%) | 30.96 - 31.66 | 29.70 - 33.27 |
| edge / priority | eft | 31.08 | 29.31 | -5.69% | moderate (5-15%) | 30.85 - 31.09 | 29.11 - 30.34 |
| edge / priority | heft | 34.93 | 30.96 | -11.36% | moderate (5-15%) | 34.33 - 35.70 | 30.40 - 35.31 |
| edge / priority | heft-ra | 34.09 | 32.49 | -4.67% | small (<=5%) | 33.29 - 34.42 | 30.08 - 32.94 |
| edge / priority | heft-ra-cap | 35.04 | 33.37 | -4.77% | small (<=5%) | 33.93 - 35.52 | 29.69 - 35.33 |
| edge / priority | adaptive-no-preempt | 32.12 | 31.12 | -3.09% | small (<=5%) | 31.58 - 32.74 | 28.28 - 32.20 |
| edge / priority | adaptive | 33.45 | 30.33 | -9.35% | moderate (5-15%) | 32.80 - 37.24 | 28.20 - 38.55 |
| edge / dag | kube-default | 28.58 | 26.77 | -6.35% | moderate (5-15%) | 28.36 - 29.80 | 25.90 - 27.44 |
| edge / dag | legacy-v1 | 28.45 | 28.28 | -0.60% | small (<=5%) | 28.23 - 29.32 | 26.67 - 28.52 |
| edge / dag | legacy-tuned | 28.66 | 28.34 | -1.10% | small (<=5%) | 28.34 - 29.74 | 27.16 - 28.94 |
| edge / dag | eft | 29.23 | 27.25 | -6.77% | moderate (5-15%) | 28.86 - 30.04 | 26.86 - 29.12 |
| edge / dag | heft | 28.06 | 26.64 | -5.07% | moderate (5-15%) | 24.39 - 30.88 | 24.28 - 28.21 |
| edge / dag | heft-ra | 28.36 | 26.00 | -8.33% | moderate (5-15%) | 27.87 - 28.50 | 24.66 - 31.13 |
| edge / dag | heft-ra-cap | 27.47 | 27.18 | -1.03% | small (<=5%) | 26.50 - 29.33 | 24.35 - 27.62 |
| edge / dag | adaptive-no-preempt | 28.46 | 27.87 | -2.09% | small (<=5%) | 27.68 - 29.81 | 25.99 - 29.76 |
| edge / dag | adaptive | 27.64 | 26.87 | -2.81% | small (<=5%) | 27.12 - 28.07 | 25.87 - 26.88 |

### p95_response_seconds

| Profile / batch | Policy | i5 median (s) | Ryzen median (s) | Delta | Size | i5 range | Ryzen range |
|---|---|---|---|---|---|---|---|
| balanced / priority | kube-default | 52.34 | 47.82 | -8.63% | moderate (5-15%) | 50.84 - 54.97 | 46.59 - 50.02 |
| balanced / priority | legacy-v1 | 54.49 | 46.18 | -15.26% | large (>15%) | 49.63 - 58.19 | 45.70 - 47.83 |
| balanced / priority | legacy-tuned | 52.56 | 46.92 | -10.73% | moderate (5-15%) | 48.30 - 60.14 | 45.86 - 47.44 |
| balanced / priority | eft | 48.89 | 45.76 | -6.40% | moderate (5-15%) | 48.42 - 56.70 | 44.82 - 46.29 |
| balanced / priority | heft | 50.67 | 46.66 | -7.91% | moderate (5-15%) | 46.24 - 65.25 | 44.87 - 47.22 |
| balanced / priority | heft-ra | 52.34 | 46.24 | -11.64% | moderate (5-15%) | 50.42 - 53.36 | 45.00 - 47.46 |
| balanced / priority | heft-ra-cap | 55.75 | 45.37 | -18.62% | large (>15%) | 51.99 - 56.14 | 45.33 - 45.89 |
| balanced / priority | adaptive-no-preempt | 54.17 | 46.02 | -15.03% | large (>15%) | 48.36 - 58.53 | 45.50 - 48.24 |
| balanced / priority | adaptive | 81.63 | 67.30 | -17.56% | large (>15%) | 81.36 - 83.44 | 62.86 - 83.11 |
| balanced / dag | kube-default | 45.85 | 44.11 | -3.79% | small (<=5%) | 43.50 - 48.64 | 33.83 - 46.88 |
| balanced / dag | legacy-v1 | 42.51 | 40.58 | -4.54% | small (<=5%) | 41.71 - 45.43 | 38.86 - 40.64 |
| balanced / dag | legacy-tuned | 47.55 | 40.40 | -15.03% | large (>15%) | 47.08 - 52.71 | 40.00 - 40.54 |
| balanced / dag | eft | 46.50 | 41.03 | -11.76% | moderate (5-15%) | 43.86 - 51.30 | 39.98 - 43.38 |
| balanced / dag | heft | 45.94 | 33.68 | -26.69% | large (>15%) | 42.85 - 57.42 | 32.54 - 35.06 |
| balanced / dag | heft-ra | 40.02 | 32.90 | -17.78% | large (>15%) | 33.88 - 43.23 | 32.65 - 33.62 |
| balanced / dag | heft-ra-cap | 35.86 | 34.81 | -2.92% | small (<=5%) | 34.73 - 36.31 | 32.75 - 37.08 |
| balanced / dag | adaptive-no-preempt | 39.20 | 37.98 | -3.11% | small (<=5%) | 35.95 - 48.22 | 35.14 - 39.59 |
| balanced / dag | adaptive | 43.44 | 36.50 | -15.99% | large (>15%) | 41.45 - 46.41 | 34.75 - 40.35 |
| homogeneous / priority | kube-default | 33.22 | 33.78 | +1.67% | small (<=5%) | 32.69 - 35.29 | 30.51 - 34.93 |
| homogeneous / priority | legacy-v1 | 33.43 | 33.18 | -0.75% | small (<=5%) | 33.29 - 33.85 | 32.72 - 35.72 |
| homogeneous / priority | legacy-tuned | 34.49 | 33.44 | -3.03% | small (<=5%) | 33.78 - 37.58 | 31.42 - 34.82 |
| homogeneous / priority | eft | 35.25 | 31.60 | -10.36% | moderate (5-15%) | 34.18 - 36.35 | 28.27 - 34.43 |
| homogeneous / priority | heft | 33.11 | 29.93 | -9.59% | moderate (5-15%) | 32.87 - 34.58 | 28.36 - 32.98 |
| homogeneous / priority | heft-ra | 34.41 | 33.85 | -1.62% | small (<=5%) | 33.57 - 34.42 | 31.75 - 33.93 |
| homogeneous / priority | heft-ra-cap | 34.86 | 30.85 | -11.49% | moderate (5-15%) | 34.57 - 35.07 | 30.39 - 40.12 |
| homogeneous / priority | adaptive-no-preempt | 36.23 | 31.68 | -12.57% | moderate (5-15%) | 35.85 - 40.64 | 31.65 - 31.74 |
| homogeneous / priority | adaptive | 33.73 | 32.60 | -3.34% | small (<=5%) | 33.13 - 34.18 | 29.28 - 32.94 |
| homogeneous / dag | kube-default | 35.15 | 31.99 | -8.99% | moderate (5-15%) | 34.65 - 35.33 | 25.75 - 36.20 |
| homogeneous / dag | legacy-v1 | 34.33 | 34.10 | -0.68% | small (<=5%) | 33.87 - 34.69 | 28.69 - 35.84 |
| homogeneous / dag | legacy-tuned | 34.57 | 33.33 | -3.61% | small (<=5%) | 33.46 - 35.15 | 30.83 - 35.30 |
| homogeneous / dag | eft | 34.18 | 37.44 | +9.55% | moderate (5-15%) | 33.97 - 37.26 | 35.14 - 38.96 |
| homogeneous / dag | heft | 28.85 | 27.45 | -4.85% | small (<=5%) | 28.70 - 33.14 | 25.93 - 28.10 |
| homogeneous / dag | heft-ra | 28.31 | 25.85 | -8.70% | moderate (5-15%) | 27.36 - 28.37 | 25.09 - 27.26 |
| homogeneous / dag | heft-ra-cap | 29.71 | 27.88 | -6.15% | moderate (5-15%) | 28.30 - 33.20 | 27.11 - 33.13 |
| homogeneous / dag | adaptive-no-preempt | 36.57 | 34.57 | -5.47% | moderate (5-15%) | 36.19 - 38.91 | 29.31 - 40.51 |
| homogeneous / dag | adaptive | 36.39 | 32.51 | -10.68% | moderate (5-15%) | 29.51 - 36.93 | 28.14 - 32.63 |
| edge / priority | kube-default | 41.34 | 40.01 | -3.22% | small (<=5%) | 40.39 - 41.38 | 38.98 - 41.42 |
| edge / priority | legacy-v1 | 41.02 | 39.55 | -3.58% | small (<=5%) | 40.92 - 41.39 | 39.07 - 40.99 |
| edge / priority | legacy-tuned | 41.03 | 40.19 | -2.04% | small (<=5%) | 40.19 - 41.59 | 39.30 - 42.75 |
| edge / priority | eft | 40.22 | 40.29 | +0.17% | small (<=5%) | 39.93 - 40.94 | 38.92 - 40.43 |
| edge / priority | heft | 40.58 | 38.58 | -4.95% | small (<=5%) | 40.50 - 42.36 | 38.38 - 43.37 |
| edge / priority | heft-ra | 40.48 | 40.10 | -0.94% | small (<=5%) | 40.17 - 41.78 | 38.44 - 41.41 |
| edge / priority | heft-ra-cap | 40.78 | 39.66 | -2.75% | small (<=5%) | 39.53 - 41.69 | 34.21 - 41.39 |
| edge / priority | adaptive-no-preempt | 41.99 | 41.03 | -2.29% | small (<=5%) | 41.14 - 43.25 | 36.61 - 42.95 |
| edge / priority | adaptive | 51.81 | 45.17 | -12.82% | moderate (5-15%) | 48.03 - 68.23 | 42.70 - 68.68 |
| edge / dag | kube-default | 38.97 | 35.23 | -9.60% | moderate (5-15%) | 38.68 - 42.38 | 33.22 - 39.71 |
| edge / dag | legacy-v1 | 39.28 | 38.86 | -1.08% | small (<=5%) | 35.21 - 40.25 | 35.22 - 41.36 |
| edge / dag | legacy-tuned | 38.09 | 35.14 | -7.74% | moderate (5-15%) | 34.49 - 41.07 | 32.06 - 41.27 |
| edge / dag | eft | 41.78 | 37.78 | -9.56% | moderate (5-15%) | 38.33 - 44.40 | 36.16 - 37.79 |
| edge / dag | heft | 36.66 | 33.75 | -7.93% | moderate (5-15%) | 29.75 - 38.37 | 29.66 - 37.33 |
| edge / dag | heft-ra | 34.45 | 33.59 | -2.47% | small (<=5%) | 34.04 - 36.16 | 28.73 - 35.13 |
| edge / dag | heft-ra-cap | 35.27 | 34.82 | -1.28% | small (<=5%) | 33.02 - 35.88 | 34.02 - 36.67 |
| edge / dag | adaptive-no-preempt | 38.99 | 39.49 | +1.28% | small (<=5%) | 35.93 - 39.18 | 35.10 - 41.64 |
| edge / dag | adaptive | 36.58 | 37.85 | +3.48% | small (<=5%) | 36.00 - 37.08 | 36.82 - 38.56 |

### critical_response_seconds

| Profile / batch | Policy | i5 median (s) | Ryzen median (s) | Delta | Size | i5 range | Ryzen range |
|---|---|---|---|---|---|---|---|
| balanced / priority | kube-default | 19.29 | 15.57 | -19.27% | large (>15%) | 18.10 - 21.07 | 13.84 - 16.21 |
| balanced / priority | legacy-v1 | 20.13 | 16.13 | -19.89% | large (>15%) | 18.57 - 21.01 | 15.06 - 17.69 |
| balanced / priority | legacy-tuned | 18.74 | 15.88 | -15.26% | large (>15%) | 17.34 - 21.08 | 14.95 - 17.62 |
| balanced / priority | eft | 17.92 | 15.79 | -11.85% | moderate (5-15%) | 17.19 - 19.76 | 15.69 - 16.92 |
| balanced / priority | heft | 36.08 | 31.26 | -13.34% | moderate (5-15%) | 28.34 - 44.04 | 29.17 - 32.73 |
| balanced / priority | heft-ra | 36.66 | 33.40 | -8.91% | moderate (5-15%) | 33.27 - 39.26 | 30.19 - 33.80 |
| balanced / priority | heft-ra-cap | 35.16 | 33.03 | -6.07% | moderate (5-15%) | 34.19 - 36.17 | 31.15 - 33.61 |
| balanced / priority | adaptive-no-preempt | 20.48 | 15.41 | -24.74% | large (>15%) | 17.05 - 21.57 | 15.38 - 15.69 |
| balanced / priority | adaptive | 12.32 | 13.36 | +8.44% | moderate (5-15%) | 12.15 - 16.55 | 12.71 - 13.88 |
| homogeneous / priority | kube-default | 16.21 | 15.99 | -1.37% | small (<=5%) | 14.97 - 16.25 | 15.95 - 17.44 |
| homogeneous / priority | legacy-v1 | 16.59 | 16.20 | -2.33% | small (<=5%) | 16.49 - 17.13 | 12.84 - 18.07 |
| homogeneous / priority | legacy-tuned | 16.32 | 16.88 | +3.39% | small (<=5%) | 15.76 - 18.83 | 13.77 - 19.07 |
| homogeneous / priority | eft | 16.26 | 15.42 | -5.12% | moderate (5-15%) | 16.23 - 17.41 | 13.13 - 16.97 |
| homogeneous / priority | heft | 17.24 | 15.28 | -11.38% | moderate (5-15%) | 16.98 - 18.66 | 14.89 - 15.66 |
| homogeneous / priority | heft-ra | 17.25 | 16.72 | -3.07% | small (<=5%) | 16.41 - 17.36 | 16.03 - 17.78 |
| homogeneous / priority | heft-ra-cap | 16.88 | 15.18 | -10.07% | moderate (5-15%) | 15.92 - 17.02 | 14.39 - 16.65 |
| homogeneous / priority | adaptive-no-preempt | 16.57 | 16.61 | +0.26% | small (<=5%) | 16.21 - 16.65 | 15.48 - 16.85 |
| homogeneous / priority | adaptive | 16.09 | 14.97 | -7.00% | moderate (5-15%) | 15.27 - 16.23 | 14.11 - 16.09 |
| edge / priority | kube-default | 17.33 | 16.06 | -7.34% | moderate (5-15%) | 15.28 - 17.34 | 15.01 - 16.85 |
| edge / priority | legacy-v1 | 15.89 | 16.00 | +0.68% | small (<=5%) | 15.80 - 16.29 | 13.52 - 16.90 |
| edge / priority | legacy-tuned | 16.04 | 16.87 | +5.17% | moderate (5-15%) | 15.36 - 16.64 | 15.06 - 18.04 |
| edge / priority | eft | 16.06 | 14.38 | -10.45% | moderate (5-15%) | 15.19 - 16.56 | 13.81 - 14.50 |
| edge / priority | heft | 23.73 | 22.15 | -6.66% | moderate (5-15%) | 22.25 - 25.61 | 22.05 - 27.64 |
| edge / priority | heft-ra | 23.60 | 22.91 | -2.93% | small (<=5%) | 23.18 - 24.03 | 21.67 - 23.26 |
| edge / priority | heft-ra-cap | 23.89 | 23.95 | +0.23% | small (<=5%) | 22.55 - 26.22 | 21.61 - 28.52 |
| edge / priority | adaptive-no-preempt | 16.60 | 16.20 | -2.39% | small (<=5%) | 15.75 - 17.20 | 14.39 - 17.01 |
| edge / priority | adaptive | 15.31 | 15.87 | +3.66% | small (<=5%) | 15.18 - 16.96 | 15.12 - 16.40 |

## Relative policy behavior within each host

Ratios below compare medians within the same profile, so task speed changes are partly normalized out. Ratio <1 favors the first policy. This is descriptive n=3 evidence, not a conclusive ranking of close policies.

| Profile | Batch | Policy ratio | Metric | i5 | Ryzen |
|---|---|---|---|---|---|
| balanced | dag | heft-ra-cap / heft | makespan_seconds | 0.7805 | 1.0338 |
| balanced | dag | heft-ra / heft | makespan_seconds | 0.8706 | 0.9768 |
| balanced | dag | legacy-tuned / legacy-v1 | makespan_seconds | 1.1182 | 0.9956 |
| balanced | priority | adaptive / adaptive-no-preempt | critical_response_seconds | 0.6015 | 0.8667 |
| balanced | priority | heft / kube-default | critical_response_seconds | 1.8703 | 2.0077 |
| homogeneous | dag | heft-ra-cap / heft | makespan_seconds | 1.0300 | 1.0155 |
| homogeneous | dag | heft-ra / heft | makespan_seconds | 0.9813 | 0.9413 |
| homogeneous | dag | legacy-tuned / legacy-v1 | makespan_seconds | 1.0076 | 0.9774 |
| homogeneous | priority | adaptive / adaptive-no-preempt | critical_response_seconds | 0.9716 | 0.9012 |
| homogeneous | priority | heft / kube-default | critical_response_seconds | 1.0634 | 0.9554 |
| edge | dag | heft-ra-cap / heft | makespan_seconds | 0.9624 | 1.0316 |
| edge | dag | heft-ra / heft | makespan_seconds | 0.9398 | 0.9951 |
| edge | dag | legacy-tuned / legacy-v1 | makespan_seconds | 0.9696 | 0.9044 |
| edge | priority | adaptive / adaptive-no-preempt | critical_response_seconds | 0.9224 | 0.9797 |
| edge | priority | heft / kube-default | critical_response_seconds | 1.3691 | 1.3791 |

### Pooled paired contrasts (the existing live analyzer)

These are means of the nine within-repetition ratios, not ratios of pooled medians. Bootstrap intervals are reported by the repository analyzer; the small sample and shared calibration still limit inferential strength.

| Batch | Policy ratio | Metric | i5 mean | i5 95% bootstrap | Ryzen mean | Ryzen 95% bootstrap |
|---|---|---|---|---|---|---|
| dag | heft-ra / heft | makespan_seconds | 0.9170 | 0.8373 - 0.9983 | 0.9738 | 0.9106 - 1.0329 |
| dag | heft-ra-cap / heft | makespan_seconds | 0.9187 | 0.8163 - 1.0208 | 1.0588 | 0.9955 - 1.1326 |
| dag | legacy-tuned / legacy-v1 | makespan_seconds | 1.0432 | 0.9966 - 1.0915 | 0.9918 | 0.9124 - 1.0699 |
| priority | adaptive / adaptive-no-preempt | critical_response_seconds | 0.8775 | 0.7538 - 0.9693 | 0.9274 | 0.8814 - 0.9818 |
| priority | heft / kube-default | critical_response_seconds | 1.4759 | 1.2450 - 1.7460 | 1.4903 | 1.1924 - 1.7983 |

### Interpretation of the original live conclusions

- A HEFT-RA variant was the strict fastest DAG policy in **8/9 i5 runs** versus **5/9 Ryzen runs**. The original 8/9 dominance is not replicated at the same strength. A strict winner count does not imply statistically meaningful separation.
- HEFT-RA's DAG advantage over HEFT: mean paired ratio **0.9170** on i5 vs **0.9738** on Ryzen (-2.62% relative to the baseline on Ryzen).
- Capacity-aware HEFT-RA versus HEFT on the DAG batch: mean paired ratio **0.9187** on i5 vs **1.0588** on Ryzen (+5.88% relative to the baseline on Ryzen).
- Adaptive preemption's urgent-workflow advantage: mean paired ratio **0.8775** on i5 vs **0.9274** on Ryzen (-7.26% relative to the baseline on Ryzen).
- In particular, `heft-ra-cap` is not the observed DAG improvement over HEFT on this machine: its pooled mean ratio is above 1. Do not carry the old live recommendation over unconditionally. The bootstrap intervals and per-profile tables show the uncertainty; close rankings are not conclusive with three repeats.
- Adaptive's urgent-workflow benefit remains, but its magnitude is smaller on this host. That does not establish a universal CPU-dependent effect.

## Did the policy ordering stay the same?

Cross-host Spearman compares nine policy medians for a fixed profile/batch/metric. 1 means identical ordering; near 0 means little rank agreement. Policies within 2% of the best median are listed as near-leaders, not statistically proven ties.

| Profile / batch | Metric | Spearman | i5 near-leaders | Ryzen near-leaders |
|---|---|---|---|---|
| balanced / dag | makespan_seconds | 0.417 | heft-ra-cap | heft-ra |
| balanced / dag | mean_response_seconds | 0.317 | heft-ra-cap | heft, heft-ra |
| balanced / dag | p95_response_seconds | 0.417 | heft-ra-cap | heft-ra |
| balanced / priority | makespan_seconds | 0.050 | eft | legacy-v1, eft, heft-ra, heft-ra-cap, adaptive-no-preempt |
| balanced / priority | mean_response_seconds | 0.517 | eft | legacy-tuned, adaptive-no-preempt, adaptive |
| balanced / priority | p95_response_seconds | 0.100 | eft | legacy-v1, eft, heft-ra, heft-ra-cap, adaptive-no-preempt |
| balanced / priority | critical_response_seconds | 0.767 | adaptive | adaptive |
| homogeneous / dag | makespan_seconds | 0.600 | heft, heft-ra | heft-ra |
| homogeneous / dag | mean_response_seconds | 0.433 | legacy-v1, heft, heft-ra | heft-ra |
| homogeneous / dag | p95_response_seconds | 0.600 | heft, heft-ra | heft-ra |
| homogeneous / priority | makespan_seconds | -0.150 | kube-default, legacy-v1, heft, adaptive | heft |
| homogeneous / priority | mean_response_seconds | -0.083 | kube-default, legacy-v1, legacy-tuned, heft-ra, heft-ra-cap, adaptive | heft, heft-ra-cap |
| homogeneous / priority | p95_response_seconds | -0.150 | kube-default, legacy-v1, heft, adaptive | heft |
| homogeneous / priority | critical_response_seconds | 0.250 | kube-default, legacy-tuned, eft, adaptive | heft-ra-cap, adaptive |
| edge / dag | makespan_seconds | 0.700 | heft-ra | heft, heft-ra |
| edge / dag | mean_response_seconds | 0.450 | heft-ra-cap, adaptive | heft-ra |
| edge / dag | p95_response_seconds | 0.700 | heft-ra | heft, heft-ra |
| edge / priority | makespan_seconds | -0.167 | legacy-v1, legacy-tuned, eft, heft, heft-ra, heft-ra-cap | heft, adaptive |
| edge / priority | mean_response_seconds | 0.800 | legacy-v1, legacy-tuned, eft | eft |
| edge / priority | p95_response_seconds | 0.433 | legacy-v1, eft, heft, heft-ra, heft-ra-cap | heft |
| edge / priority | critical_response_seconds | 0.783 | adaptive | eft |

## Warm-up: task speed and orchestration costs

Base runtime is speed-normalized task-class work, not end-to-end DAG response. A lower runtime here supports faster execution of that particular workload on the new stack; it does not isolate CPU from kernel, image, memory or storage changes.

| Profile | Calibrated component | i5 (s) | Ryzen (s) | Delta |
|---|---|---|---|---|
| balanced | experiment-cpu | 3.067 | 3.014 | -1.74% |
| balanced | experiment-cpu-long | 10.065 | 10.015 | -0.50% |
| balanced | experiment-io | 3.012 | 3.006 | -0.20% |
| balanced | experiment-io-large | 3.251 | 3.029 | -6.84% |
| balanced | experiment-mem | 4.362 | 4.096 | -6.09% |
| balanced | control_plane_delay_s | 2.122 | 1.194 | -43.74% |
| balanced | slot_release_delay_s | 2.755 | 2.438 | -11.49% |
| homogeneous | experiment-cpu | 3.033 | 3.019 | -0.49% |
| homogeneous | experiment-cpu-long | 10.020 | 10.021 | +0.02% |
| homogeneous | experiment-io | 3.015 | 3.008 | -0.22% |
| homogeneous | experiment-io-large | 3.152 | 3.120 | -1.02% |
| homogeneous | experiment-mem | 4.427 | 4.242 | -4.19% |
| homogeneous | control_plane_delay_s | 2.144 | 1.510 | -29.56% |
| homogeneous | slot_release_delay_s | 2.250 | 1.901 | -15.50% |
| edge | experiment-cpu | 3.046 | 3.013 | -1.08% |
| edge | experiment-cpu-long | 10.023 | 10.013 | -0.09% |
| edge | experiment-io | 3.008 | 3.005 | -0.08% |
| edge | experiment-io-large | 3.053 | 3.071 | +0.58% |
| edge | experiment-mem | 4.164 | 4.117 | -1.12% |
| edge | control_plane_delay_s | 2.708 | 2.643 | -2.39% |
| edge | slot_release_delay_s | 2.341 | 2.230 | -4.72% |

## Simulator twin fidelity on each computer

Both twins use each host's own pre-trial calibration. Measured-release mode models the warm-up completion-to-rebind delay. MAPE is error against the host's live medians, not cross-host speedup; smaller is better.

| Twin | Profile / batch | Metric | i5 MAPE | Ryzen MAPE | i5 rank agreement | Ryzen rank agreement |
|---|---|---|---|---|---|---|
| original | balanced / dag | makespan_seconds | 6.85% | 15.63% | 0.675 | 0.822 |
| original | balanced / dag | mean_response_seconds | 8.27% | 18.77% | 0.496 | 0.901 |
| original | balanced / priority | makespan_seconds | 12.57% | 19.92% | 0.222 | 0.447 |
| original | balanced / priority | mean_response_seconds | 14.27% | 22.53% | 0.674 | 0.674 |
| original | homogeneous / dag | makespan_seconds | 17.83% | 27.28% | -0.009 | 0.037 |
| original | homogeneous / dag | mean_response_seconds | 13.20% | 23.70% | 0.122 | 0.174 |
| original | homogeneous / priority | makespan_seconds | 11.10% | 15.59% | -0.137 | 0.000 |
| original | homogeneous / priority | mean_response_seconds | 18.57% | 26.38% | 0.202 | -0.294 |
| original | edge / dag | makespan_seconds | 5.84% | 12.96% | 0.511 | -0.280 |
| original | edge / dag | mean_response_seconds | 12.20% | 15.61% | 0.694 | -0.486 |
| original | edge / priority | makespan_seconds | 19.61% | 23.93% | 0.587 | -0.138 |
| original | edge / priority | mean_response_seconds | 22.95% | 22.94% | 0.845 | 0.518 |
| measured-release | balanced / dag | makespan_seconds | 6.05% | 12.55% | 0.624 | 0.679 |
| measured-release | balanced / dag | mean_response_seconds | 5.32% | 15.92% | 0.496 | 0.940 |
| measured-release | balanced / priority | makespan_seconds | 3.07% | 4.85% | 0.298 | 0.224 |
| measured-release | balanced / priority | mean_response_seconds | 5.19% | 8.62% | 0.604 | 0.604 |
| measured-release | homogeneous / dag | makespan_seconds | 12.39% | 20.64% | 0.149 | 0.621 |
| measured-release | homogeneous / dag | mean_response_seconds | 6.71% | 16.74% | 0.087 | 0.481 |
| measured-release | homogeneous / priority | makespan_seconds | 6.25% | 10.76% | -0.190 | -0.207 |
| measured-release | homogeneous / priority | mean_response_seconds | 5.84% | 14.96% | 0.168 | -0.361 |
| measured-release | edge / dag | makespan_seconds | 4.38% | 9.27% | 0.505 | -0.275 |
| measured-release | edge / dag | mean_response_seconds | 6.97% | 8.46% | 0.798 | 0.578 |
| measured-release | edge / priority | makespan_seconds | 6.43% | 8.44% | 0.468 | 0.180 |
| measured-release | edge / priority | mean_response_seconds | 8.30% | 7.31% | 0.655 | 0.783 |

## What can and cannot be concluded

- Use the local equivalence tables to decide whether the documented simulated conclusions reproduce. A faster host should reduce execution cost, not virtual seconds.
- Use live absolute deltas to describe this machine's measured timings, and the within-host ratios to judge whether policy advantages persist.
- Faster work execution may make fixed scheduler ticks, controller reconciliation, Pod startup and slot-release delays a larger fraction of total runtime; rankings can change without a simulator regression.
- No CPU-only, RAM-only, power-efficiency or physical-network speedup is inferred. This task does not measure electrical energy or thermal throttling on either PC.
- No high-capacity result is represented as a matched-capacity replication. An expanded-slot study would be a new workload/capacity experiment and requires its own full matrix and calibration.

## Evidence

[Raw differences and paired live ratios](comparison.json), [run ledger and coverage](RUNS.md), [live integrity/twin analysis](kind-validation/summary.md).
