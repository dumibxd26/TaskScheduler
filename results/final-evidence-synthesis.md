# Final Evidence Synthesis

This file is generated from retained JSONL artifacts. Confirmatory seeds were not used to tune Legacy weights.

## Closed DAGs

### Exploratory (all policies)

Records: 13800; cases: 1500.

| Rank | Policy | Mean regret | 95% CI | P95 regret | Win rate |
|---:|---|---:|---:|---:|---:|
| 1 | heft | 12.19% | [9.28%, 15.28%] | 57.83% | 85.20% |
| 2 | eft | 12.35% | [9.43%, 15.44%] | 57.83% | 85.20% |
| 3 | legacy | 13.35% | [10.36%, 16.53%] | 57.83% | 85.20% |
| 4 | adaptive | 13.38% | [11.47%, 15.41%] | 62.57% | 76.07% |
| 5 | adaptive-no-preempt | 13.38% | [11.47%, 15.41%] | 62.57% | 76.07% |
| 6 | legacy-tuned | 16.91% | [14.15%, 19.96%] | 84.32% | 77.33% |
| 7 | legacy-v1 | 25.19% | [21.47%, 29.17%] | 131.09% | 70.67% |
| 8 | fcfs | 128.77% | [112.50%, 144.72%] | 691.00% | 61.33% |
| 9 | random | 347.97% | [308.85%, 390.52%] | 1673.04% | 28.33% |

### Exploratory (confirmation policy set)

Records: 10500; cases: 1500.

| Rank | Policy | Mean regret | 95% CI | P95 regret | Win rate |
|---:|---|---:|---:|---:|---:|
| 1 | heft | 1.69% | [1.26%, 2.15%] | 5.75% | 91.93% |
| 2 | eft | 1.85% | [1.40%, 2.30%] | 10.05% | 91.93% |
| 3 | legacy | 2.49% | [1.97%, 3.03%] | 17.73% | 91.87% |
| 4 | legacy-tuned | 6.95% | [5.72%, 8.36%] | 54.68% | 83.93% |
| 5 | adaptive | 4.94% | [4.14%, 5.84%] | 55.14% | 86.73% |
| 6 | adaptive-no-preempt | 4.94% | [4.14%, 5.84%] | 55.14% | 86.73% |
| 7 | legacy-v1 | 13.05% | [11.22%, 15.00%] | 84.32% | 74.60% |

### Confirmatory

Records: 26250; cases: 3750.

| Rank | Policy | Mean regret | 95% CI | P95 regret | Win rate |
|---:|---|---:|---:|---:|---:|
| 1 | heft | 1.69% | [1.40%, 1.98%] | 5.75% | 91.97% |
| 2 | eft | 1.85% | [1.58%, 2.15%] | 10.05% | 91.97% |
| 3 | legacy | 2.50% | [2.17%, 2.84%] | 17.73% | 91.89% |
| 4 | legacy-tuned | 6.97% | [6.14%, 7.79%] | 54.68% | 83.87% |
| 5 | adaptive | 4.96% | [4.42%, 5.53%] | 55.14% | 86.61% |
| 6 | adaptive-no-preempt | 4.96% | [4.42%, 5.53%] | 55.14% | 86.61% |
| 7 | legacy-v1 | 13.07% | [11.88%, 14.17%] | 84.32% | 74.53% |

### Confirmatory family leaders

| Family | Lowest mean regret | Mean regret | Lowest P95 regret |
|---|---|---:|---|
| cache-sensitive | heft | 1.15% | heft |
| cpu-heavy | eft, heft | 0.02% | adaptive, adaptive-no-preempt, eft, heft, legacy |
| data-heavy | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | eft, heft, legacy, legacy-tuned, legacy-v1 |
| deep | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | eft, heft, legacy, legacy-tuned, legacy-v1 |
| diamond | adaptive, adaptive-no-preempt, legacy-tuned | 0.00% | adaptive, adaptive-no-preempt, legacy-tuned |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 |
| fan-in | eft, heft, legacy | 0.00% | eft, heft, legacy |
| fan-out | adaptive, adaptive-no-preempt | 0.00% | adaptive, adaptive-no-preempt |
| gang | adaptive, adaptive-no-preempt, eft, heft, legacy | 0.00% | adaptive, adaptive-no-preempt, eft, heft, legacy |
| io-heavy | eft, heft, legacy | 0.00% | eft, heft, legacy |
| linear | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | eft, heft, legacy, legacy-tuned, legacy-v1 |
| memory-heavy | eft, heft, legacy | 0.00% | eft, heft, legacy |
| mixed | adaptive, adaptive-no-preempt | 0.00% | adaptive, adaptive-no-preempt |
| priority | eft, heft, legacy, legacy-v1 | 0.00% | eft, heft, legacy, legacy-v1 |
| wide | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | eft, heft, legacy, legacy-tuned, legacy-v1 |

## Overlapping Workloads

### Exploratory (all policies)

Records: 3600; cases: 400.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | adaptive | 0.62% | 52.75% | 53.33% | 55.070 |
| 2 | legacy | 0.62% | 71.58% | 60.00% | 62.421 |
| 3 | adaptive-no-preempt | 0.92% | 52.75% | 53.33% | 55.070 |
| 4 | heft | 1.00% | 66.80% | 53.33% | 55.070 |
| 5 | eft | 1.08% | 66.80% | 53.33% | 55.070 |
| 6 | legacy-tuned | 3.26% | 63.16% | 53.33% | 60.242 |
| 7 | legacy-v1 | 3.26% | 88.18% | 46.67% | 67.684 |
| 8 | fcfs | 57.19% | 1111.57% | 53.33% | 73.571 |
| 9 | random | 112.70% | 1755.83% | 23.33% | 148.850 |

### Exploratory (confirmation policy set)

Records: 2800; cases: 400.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | adaptive | 0.00% | 33.11% | 53.33% | 55.070 |
| 2 | legacy | 0.00% | 71.58% | 60.00% | 62.421 |
| 3 | adaptive-no-preempt | 0.17% | 38.54% | 53.33% | 55.070 |
| 4 | eft | 0.27% | 66.80% | 53.33% | 55.070 |
| 5 | heft | 0.27% | 66.80% | 53.33% | 55.070 |
| 6 | legacy-tuned | 1.08% | 42.90% | 53.33% | 60.242 |
| 7 | legacy-v1 | 3.26% | 68.43% | 46.67% | 67.684 |

### Confirmatory

Records: 7000; cases: 1000.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | adaptive | 0.00% | 35.07% | 53.33% | 55.070 |
| 2 | legacy | 0.00% | 74.82% | 60.00% | 62.421 |
| 3 | adaptive-no-preempt | 0.26% | 38.54% | 53.33% | 55.070 |
| 4 | eft | 0.27% | 71.84% | 53.33% | 55.070 |
| 5 | heft | 0.27% | 71.84% | 53.33% | 55.070 |
| 6 | legacy-tuned | 1.08% | 54.70% | 53.33% | 62.421 |
| 7 | legacy-v1 | 3.26% | 77.51% | 46.67% | 67.084 |

### Confirmatory family leaders

| Workload | Lowest mean response regret | Best deadline hit | Lowest median makespan |
|---|---|---|---|
| data-locality-wave | eft | n/a | eft, heft |
| mixed-arrivals | adaptive | legacy | adaptive, adaptive-no-preempt |
| priority-burst | adaptive | legacy-tuned | adaptive, adaptive-no-preempt, eft, heft, legacy |
| recovery-gang-mix | legacy | legacy-v1 | legacy-v1 |

## Static Oracle Subset

The oracle is excluded from deployable-policy regret. This separate table reports the 300 tractable exploratory cases where it exists.

| Policy | Cases | Mean gap | 95% CI | Median gap | P95 gap |
|---|---:|---:|---:|---:|---:|
| heft | 300 | 3.06% | [2.22%, 3.95%] | 0.00% | 29.55% |
| eft | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| legacy | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| legacy-v1 | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| adaptive | 300 | 5.60% | [4.32%, 7.04%] | 0.00% | 40.77% |
| adaptive-no-preempt | 300 | 5.60% | [4.32%, 7.04%] | 0.00% | 40.77% |
| legacy-tuned | 300 | 6.01% | [4.65%, 7.48%] | 0.00% | 40.77% |
| fcfs | 300 | 86.80% | [66.93%, 107.91%] | 0.00% | 691.00% |
| random | 300 | 102.63% | [84.81%, 121.47%] | 15.89% | 375.15% |

## Paired Tuned Legacy Checks

- **closed_exploratory:** tuned/v1 mean ratio 0.9714 (95% bootstrap CI [0.9655, 0.9771]); wins/ties/losses 220/1260/20.
- **closed_confirmatory:** tuned/v1 mean ratio 0.9714 (95% bootstrap CI [0.9677, 0.9751]); wins/ties/losses 550/3150/50.
- **workload_exploratory:** tuned/v1 mean ratio 0.9375 (95% bootstrap CI [0.9264, 0.9476]); wins/ties/losses 192/140/68.
- **workload_confirmatory:** tuned/v1 mean ratio 0.9370 (95% bootstrap CI [0.9303, 0.9433]); wins/ties/losses 474/351/175.

