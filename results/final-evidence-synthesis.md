# Final Evidence Synthesis

Generated from retained JSONL artifacts with bootstrap seed 2026.
Exploratory seeds 0-19 use each cluster's declared uncertainty and were
used for development (including the design of HEFT-RA). Confirmatory
seeds 200-249 and untouched holdout seeds 400-449 multiply every
node's performance, power, thermal and reliability parameters and every
link's bandwidth, latency and jitter by seeded factors drawn uniformly
from [0.9, 1.1] (or the cluster's wider declared range), so every seed
is a distinct cluster instance. Holdout data was never used for any
choice. Seeds 600-649 form a second, fresh holdout with the same
perturbation, run once after HEFT-RA-cap (capacity-aware lookahead) was
added following the live validation.

## Closed DAGs

### Exploratory, all policies (development)

Records: 18300; cases: 1500.

Ranked by median regret, then P95 regret, then mean regret.

| Rank | Policy | Median regret | P95 regret | Mean regret | 95% CI | Win/tie rate |
|---:|---|---:|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 5.75% | 1.76% | [1.31%, 2.26%] | 92.00% |
| 2 | heft-ra-cap | 0.00% | 5.75% | 1.76% | [1.31%, 2.26%] | 92.00% |
| 3 | heft | 0.00% | 57.20% | 11.34% | [8.42%, 14.40%] | 85.33% |
| 4 | eft | 0.00% | 57.20% | 11.50% | [8.57%, 14.57%] | 85.33% |
| 5 | legacy | 0.00% | 57.20% | 11.50% | [8.57%, 14.57%] | 85.33% |
| 6 | adaptive | 0.00% | 60.08% | 10.17% | [8.90%, 11.45%] | 78.67% |
| 7 | adaptive-no-preempt | 0.00% | 60.08% | 10.17% | [8.90%, 11.45%] | 78.67% |
| 8 | legacy-tuned | 0.00% | 74.07% | 17.09% | [14.29%, 20.16%] | 74.67% |
| 9 | legacy-v1 | 0.00% | 174.70% | 25.96% | [22.16%, 29.96%] | 68.00% |
| 10 | k8s-default | 0.00% | 1263.72% | 159.08% | [135.51%, 183.73%] | 55.13% |
| 11 | fcfs | 0.00% | 3253.38% | 502.33% | [438.42%, 565.46%] | 61.33% |
| 12 | random | 190.98% | 11379.65% | 2811.34% | [2190.63%, 3536.46%] | 27.67% |

### Exploratory, confirmation policy set

Records: 15000; cases: 1500.

Ranked by median regret, then P95 regret, then mean regret.

| Rank | Policy | Median regret | P95 regret | Mean regret | 95% CI | Win/tie rate |
|---:|---|---:|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 2.48% | 0.75% | [0.55%, 0.97%] | 93.33% |
| 2 | heft-ra-cap | 0.00% | 2.48% | 0.75% | [0.55%, 0.97%] | 93.33% |
| 3 | heft | 0.00% | 48.50% | 10.33% | [7.45%, 13.47%] | 86.67% |
| 4 | eft | 0.00% | 48.50% | 10.49% | [7.60%, 13.61%] | 86.67% |
| 5 | legacy | 0.00% | 48.50% | 10.49% | [7.60%, 13.61%] | 86.67% |
| 6 | legacy-tuned | 0.00% | 56.32% | 15.51% | [12.80%, 18.43%] | 74.67% |
| 7 | adaptive | 0.00% | 59.09% | 9.16% | [7.94%, 10.46%] | 80.00% |
| 8 | adaptive-no-preempt | 0.00% | 59.09% | 9.16% | [7.94%, 10.46%] | 80.00% |
| 9 | legacy-v1 | 0.00% | 168.88% | 24.38% | [20.75%, 28.27%] | 68.00% |
| 10 | k8s-default | 0.00% | 1263.72% | 157.50% | [134.00%, 182.25%] | 55.13% |

### Confirmatory (+/-10% perturbation)

Records: 37500; cases: 3750.

Ranked by median regret, then P95 regret, then mean regret.

| Rank | Policy | Median regret | P95 regret | Mean regret | 95% CI | Win/tie rate |
|---:|---|---:|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.00% | 0.38% | [0.29%, 0.46%] | 95.39% |
| 2 | heft-ra-cap | 0.00% | 0.00% | 0.38% | [0.29%, 0.46%] | 95.39% |
| 3 | heft | 0.00% | 49.91% | 12.31% | [10.47%, 14.32%] | 79.36% |
| 4 | eft | 0.00% | 49.91% | 12.45% | [10.59%, 14.45%] | 78.93% |
| 5 | legacy | 0.00% | 52.55% | 13.45% | [11.45%, 15.54%] | 78.35% |
| 6 | adaptive | 0.00% | 62.25% | 13.18% | [11.81%, 14.65%] | 72.03% |
| 7 | adaptive-no-preempt | 0.00% | 62.25% | 13.18% | [11.81%, 14.65%] | 72.03% |
| 8 | legacy-tuned | 0.00% | 71.85% | 18.46% | [16.66%, 20.33%] | 57.84% |
| 9 | legacy-v1 | 0.00% | 174.40% | 27.60% | [25.13%, 30.06%] | 53.84% |
| 10 | k8s-default | 5.27% | 1248.49% | 159.55% | [144.15%, 174.15%] | 41.95% |

### Holdout (+/-10% perturbation, untouched)

Records: 37500; cases: 3750.

Ranked by median regret, then P95 regret, then mean regret.

| Rank | Policy | Median regret | P95 regret | Mean regret | 95% CI | Win/tie rate |
|---:|---|---:|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.12% | 0.39% | [0.31%, 0.49%] | 94.80% |
| 2 | heft-ra-cap | 0.00% | 0.12% | 0.39% | [0.31%, 0.49%] | 94.80% |
| 3 | heft | 0.00% | 50.59% | 12.06% | [10.24%, 14.00%] | 80.03% |
| 4 | eft | 0.00% | 50.59% | 12.23% | [10.39%, 14.15%] | 79.36% |
| 5 | legacy | 0.00% | 52.05% | 13.39% | [11.34%, 15.49%] | 78.75% |
| 6 | adaptive | 0.00% | 64.20% | 13.42% | [11.99%, 14.95%] | 73.17% |
| 7 | adaptive-no-preempt | 0.00% | 64.20% | 13.42% | [11.99%, 14.95%] | 73.17% |
| 8 | legacy-tuned | 0.00% | 68.09% | 18.42% | [16.65%, 20.33%] | 58.24% |
| 9 | legacy-v1 | 0.00% | 175.47% | 27.64% | [25.11%, 30.20%] | 54.61% |
| 10 | k8s-default | 5.09% | 1257.52% | 162.56% | [146.51%, 177.42%] | 42.51% |

### Fresh holdout (seeds 600-649, run once for the final policy set)

Records: 37500; cases: 3750.

Ranked by median regret, then P95 regret, then mean regret.

| Rank | Policy | Median regret | P95 regret | Mean regret | 95% CI | Win/tie rate |
|---:|---|---:|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.00% | 0.37% | [0.28%, 0.47%] | 95.39% |
| 2 | heft-ra-cap | 0.00% | 0.00% | 0.37% | [0.28%, 0.47%] | 95.39% |
| 3 | heft | 0.00% | 49.02% | 12.42% | [10.57%, 14.45%] | 80.05% |
| 4 | eft | 0.00% | 49.02% | 12.54% | [10.68%, 14.57%] | 79.65% |
| 5 | legacy | 0.00% | 54.23% | 13.61% | [11.62%, 15.77%] | 78.83% |
| 6 | adaptive | 0.00% | 57.83% | 13.21% | [11.83%, 14.69%] | 71.95% |
| 7 | adaptive-no-preempt | 0.00% | 57.83% | 13.21% | [11.83%, 14.69%] | 71.95% |
| 8 | legacy-tuned | 0.00% | 73.00% | 18.34% | [16.57%, 20.15%] | 56.64% |
| 9 | legacy-v1 | 0.00% | 173.88% | 27.80% | [25.35%, 30.21%] | 52.91% |
| 10 | k8s-default | 5.20% | 1296.03% | 160.69% | [144.58%, 176.40%] | 42.32% |

### Independence of seeds

Distinct outcomes per (cluster, case, policy): 20 exploratory seeds use the declared uncertainty, 50 holdout seeds use the uncertainty floor.

| Cluster | Exploratory mean (of 20) | Min | Max | Holdout mean (of 50) | Min | Max |
|---|---:|---:|---:|---:|---:|---:|
| asymmetric-edge-5 | 4.3 | 1 | 20 | 50.0 | 50 | 50 |
| balanced-heterogeneous-4 | 3.0 | 1 | 20 | 50.0 | 50 | 50 |
| homogeneous-4 | 1.4 | 1 | 15 | 50.0 | 50 | 50 |
| local-i5-8300h | 20.0 | 20 | 20 | 50.0 | 50 | 50 |
| unreliable-thermal-3 | 4.0 | 1 | 20 | 50.0 | 50 | 50 |

### Holdout (+/-10% perturbation, untouched): paired contrasts

| Policy vs baseline | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| heft-ra vs heft | makespan_s | 3750 | 0.9604 | [0.9562, 0.9646] | 701 / 3023 / 26 |
| heft-ra-cap vs heft-ra | makespan_s | 3750 | 1.0000 | [1.0000, 1.0000] | 0 / 3750 / 0 |
| heft-ra-cap vs heft | makespan_s | 3750 | 0.9604 | [0.9562, 0.9646] | 701 / 3023 / 26 |
| heft vs eft | makespan_s | 3750 | 0.9987 | [0.9982, 0.9992] | 36 / 3691 / 23 |
| heft vs k8s-default | makespan_s | 3750 | 0.8110 | [0.8014, 0.8208] | 2017 / 1488 / 245 |
| legacy-tuned vs legacy-v1 | makespan_s | 3750 | 0.9715 | [0.9673, 0.9757] | 586 / 3059 / 105 |
| adaptive vs heft | makespan_s | 3750 | 1.0360 | [1.0307, 1.0415] | 208 / 3175 / 367 |
| heft-ra vs adaptive | makespan_s | 3750 | 0.9385 | [0.9338, 0.9434] | 962 / 2716 / 72 |

### Holdout (+/-10% perturbation, untouched): leaders by workflow family

| Workflow family | Lowest mean regret | Mean regret | Lowest P95 regret |
|---|---|---:|---|
| cache-sensitive | heft-ra, heft-ra-cap | 0.49% | heft-ra, heft-ra-cap |
| cpu-heavy | heft-ra, heft-ra-cap | 1.77% | heft-ra, heft-ra-cap |
| data-heavy | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| deep | heft-ra, heft-ra-cap | 0.03% | heft-ra, heft-ra-cap, legacy |
| diamond | heft-ra, heft-ra-cap | 0.01% | heft-ra, heft-ra-cap |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy |
| fan-in | heft-ra, heft-ra-cap | 0.03% | eft, heft, heft-ra, heft-ra-cap, legacy |
| fan-out | heft-ra, heft-ra-cap | 0.00% | heft-ra, heft-ra-cap |
| gang | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| io-heavy | heft-ra, heft-ra-cap | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap |
| linear | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| memory-heavy | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| mixed | heft-ra, heft-ra-cap | 3.04% | heft-ra, heft-ra-cap |
| priority | heft-ra, heft-ra-cap | 0.54% | heft-ra, heft-ra-cap |
| wide | eft, heft, heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |

### Holdout (+/-10% perturbation, untouched): leaders by cluster family

| Cluster family | Lowest mean regret | Mean regret | Lowest P95 regret |
|---|---|---:|---|
| asymmetric-network | heft-ra, heft-ra-cap | 0.01% | heft-ra, heft-ra-cap |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.03% | heft-ra, heft-ra-cap |
| homogeneous | heft-ra, heft-ra-cap | 0.22% | heft-ra, heft-ra-cap |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 |
| unreliable-thermal | heft-ra, heft-ra-cap | 1.72% | heft-ra, heft-ra-cap |

### Holdout (+/-10% perturbation, untouched): leaders by workflow family and cluster family

Each cell names the policy (or exact tie) with the lowest mean regret in that cell, followed by that regret and, in brackets, HEFT-RA's own mean regret.

| Workflow family | asymmetric-network | balanced-heterogeneous | homogeneous | local-calibration | unreliable-thermal |
|---|---|---|---|---|---|
| cache-sensitive | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft (1.36%) [2.44%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| cpu-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (8.86%) [8.86%] |
| data-heavy | heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.02%) [0.02%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| deep | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.16%) [0.16%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| diamond | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.07%) [0.07%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| fan-in | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.15%) [0.15%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| fan-out | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] |
| gang | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] |
| linear | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| memory-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] |
| mixed | heft-ra, heft-ra-cap (0.02%) [0.02%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, legacy-tuned (0.00%) [15.18%] |
| priority | heft-ra, heft-ra-cap (0.11%) [0.11%] | k8s-default (0.22%) [0.47%] | heft-ra, heft-ra-cap (0.41%) [0.41%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | k8s-default (0.00%) [1.70%] |
| wide | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |

### Fresh holdout (seeds 600-649, run once for the final policy set): paired contrasts

| Policy vs baseline | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| heft-ra vs heft | makespan_s | 3750 | 0.9602 | [0.9561, 0.9642] | 712 / 3015 / 23 |
| heft-ra-cap vs heft-ra | makespan_s | 3750 | 1.0000 | [1.0000, 1.0000] | 0 / 3750 / 0 |
| heft-ra-cap vs heft | makespan_s | 3750 | 0.9602 | [0.9561, 0.9642] | 712 / 3015 / 23 |
| heft vs eft | makespan_s | 3750 | 0.9991 | [0.9986, 0.9995] | 27 / 3703 / 20 |
| heft vs k8s-default | makespan_s | 3750 | 0.8133 | [0.8039, 0.8230] | 2009 / 1500 / 241 |
| legacy-tuned vs legacy-v1 | makespan_s | 3750 | 0.9710 | [0.9668, 0.9754] | 589 / 3048 / 113 |
| adaptive vs heft | makespan_s | 3750 | 1.0335 | [1.0285, 1.0387] | 203 / 3125 / 422 |
| heft-ra vs adaptive | makespan_s | 3750 | 0.9385 | [0.9338, 0.9430] | 1016 / 2672 / 62 |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by workflow family

| Workflow family | Lowest mean regret | Mean regret | Lowest P95 regret |
|---|---|---:|---|
| cache-sensitive | heft-ra, heft-ra-cap | 0.35% | heft-ra, heft-ra-cap |
| cpu-heavy | heft-ra, heft-ra-cap | 1.12% | heft-ra, heft-ra-cap |
| data-heavy | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| deep | heft-ra, heft-ra-cap | 0.02% | heft-ra, heft-ra-cap |
| diamond | heft-ra, heft-ra-cap | 0.00% | heft-ra, heft-ra-cap |
| failure-recovery | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy |
| fan-in | heft-ra, heft-ra-cap | 0.02% | eft, heft, heft-ra, heft-ra-cap, legacy |
| fan-out | heft-ra, heft-ra-cap | 0.00% | heft-ra, heft-ra-cap |
| gang | eft, heft, heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| io-heavy | heft-ra, heft-ra-cap | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap |
| linear | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| memory-heavy | heft-ra, heft-ra-cap | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |
| mixed | heft-ra, heft-ra-cap | 3.64% | heft-ra, heft-ra-cap |
| priority | heft-ra, heft-ra-cap | 0.43% | heft-ra, heft-ra-cap |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | eft, heft, heft-ra, heft-ra-cap, legacy |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by cluster family

| Cluster family | Lowest mean regret | Mean regret | Lowest P95 regret |
|---|---|---:|---|
| asymmetric-network | heft-ra, heft-ra-cap | 0.00% | heft-ra, heft-ra-cap |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.03% | heft-ra, heft-ra-cap |
| homogeneous | heft-ra, heft-ra-cap | 0.14% | heft-ra, heft-ra-cap |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 |
| unreliable-thermal | heft-ra, heft-ra-cap | 1.69% | heft-ra, heft-ra-cap |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by workflow family and cluster family

Each cell names the policy (or exact tie) with the lowest mean regret in that cell, followed by that regret and, in brackets, HEFT-RA's own mean regret.

| Workflow family | asymmetric-network | balanced-heterogeneous | homogeneous | local-calibration | unreliable-thermal |
|---|---|---|---|---|---|
| cache-sensitive | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | legacy (0.43%) [1.74%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| cpu-heavy | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (5.62%) [5.62%] |
| data-heavy | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.02%) [0.02%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| deep | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.12%) [0.12%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| diamond | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.01%) [0.01%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] |
| failure-recovery | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| fan-in | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt (0.00%) [0.08%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| fan-out | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.01%) [0.01%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] |
| gang | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | eft, heft, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] |
| linear | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.01%) [0.01%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |
| memory-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] |
| mixed | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.03%) [0.03%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | adaptive, adaptive-no-preempt, legacy-tuned (0.00%) [18.18%] |
| priority | heft-ra, heft-ra-cap (0.03%) [0.03%] | k8s-default (0.18%) [0.38%] | heft-ra, heft-ra-cap (0.14%) [0.14%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | k8s-default (0.00%) [1.58%] |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy (0.00%) [0.00%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] |

## Overlapping Workloads

### Exploratory, all policies (development)

Records: 4800; cases: 400.

Ranked by median response regret, then P95 response regret.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | legacy | 0.13% | 69.81% | 66.67% | 54.682 |
| 2 | eft | 0.75% | 69.81% | 66.67% | 54.549 |
| 3 | adaptive | 0.99% | 66.23% | 66.67% | 51.792 |
| 4 | heft-ra | 1.01% | 48.64% | 66.67% | 47.657 |
| 5 | adaptive-no-preempt | 1.13% | 66.23% | 66.67% | 49.035 |
| 6 | heft | 1.27% | 69.81% | 66.67% | 54.549 |
| 7 | heft-ra-cap | 1.75% | 48.64% | 66.67% | 47.657 |
| 8 | legacy-tuned | 5.24% | 82.75% | 66.67% | 60.192 |
| 9 | legacy-v1 | 28.61% | 117.88% | 66.67% | 69.480 |
| 10 | fcfs | 37.90% | 8102.99% | 66.67% | 79.722 |
| 11 | k8s-default | 75.57% | 1384.96% | 30.33% | 115.185 |
| 12 | random | 157.75% | 9655.08% | 26.33% | 170.228 |

### Exploratory, confirmation policy set

Records: 4000; cases: 400.

Ranked by median response regret, then P95 response regret.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | legacy | 0.13% | 69.81% | 66.67% | 54.682 |
| 2 | eft | 0.59% | 69.81% | 66.67% | 54.549 |
| 3 | adaptive | 0.92% | 66.23% | 66.67% | 51.792 |
| 4 | heft-ra | 0.93% | 48.64% | 66.67% | 47.657 |
| 5 | adaptive-no-preempt | 1.13% | 66.23% | 66.67% | 49.035 |
| 6 | heft | 1.27% | 69.81% | 66.67% | 54.549 |
| 7 | heft-ra-cap | 1.75% | 48.64% | 66.67% | 47.657 |
| 8 | legacy-tuned | 5.24% | 82.75% | 66.67% | 60.192 |
| 9 | legacy-v1 | 28.61% | 117.88% | 66.67% | 69.480 |
| 10 | k8s-default | 75.57% | 1384.96% | 30.33% | 115.185 |

### Confirmatory (+/-10% perturbation)

Records: 10000; cases: 1000.

Ranked by median response regret, then P95 response regret.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.04% | 63.78% | 70.67% | 48.562 |
| 2 | heft-ra | 0.46% | 63.78% | 70.00% | 49.345 |
| 3 | adaptive | 0.71% | 82.46% | 67.73% | 53.825 |
| 4 | adaptive-no-preempt | 1.14% | 82.46% | 67.73% | 51.485 |
| 5 | legacy | 1.20% | 82.46% | 66.40% | 56.027 |
| 6 | eft | 1.21% | 81.55% | 66.80% | 54.904 |
| 7 | heft | 1.58% | 81.55% | 66.80% | 53.676 |
| 8 | legacy-tuned | 9.43% | 98.64% | 67.47% | 58.469 |
| 9 | legacy-v1 | 30.65% | 190.29% | 67.33% | 69.667 |
| 10 | k8s-default | 81.27% | 1639.35% | 34.13% | 117.250 |

### Holdout (+/-10% perturbation, untouched)

Records: 10000; cases: 1000.

Ranked by median response regret, then P95 response regret.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.04% | 63.88% | 70.27% | 47.832 |
| 2 | heft-ra | 0.42% | 63.88% | 69.47% | 48.733 |
| 3 | adaptive | 0.77% | 83.64% | 67.60% | 53.173 |
| 4 | eft | 1.01% | 82.36% | 66.13% | 53.537 |
| 5 | legacy | 1.04% | 83.09% | 66.00% | 55.091 |
| 6 | adaptive-no-preempt | 1.20% | 83.64% | 67.60% | 50.981 |
| 7 | heft | 1.57% | 82.36% | 66.13% | 52.639 |
| 8 | legacy-tuned | 9.19% | 97.30% | 67.87% | 57.433 |
| 9 | legacy-v1 | 30.20% | 208.81% | 67.47% | 69.878 |
| 10 | k8s-default | 82.68% | 1735.10% | 35.20% | 117.989 |

### Fresh holdout (seeds 600-649, run once for the final policy set)

Records: 10000; cases: 1000.

Ranked by median response regret, then P95 response regret.

| Rank | Policy | Median response regret | P95 response regret | Deadline hit | Median makespan (s) |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.00% | 63.76% | 71.47% | 47.211 |
| 2 | heft-ra | 0.51% | 63.76% | 70.53% | 48.201 |
| 3 | adaptive | 0.65% | 83.76% | 69.20% | 53.275 |
| 4 | eft | 0.97% | 82.56% | 68.13% | 53.099 |
| 5 | adaptive-no-preempt | 1.00% | 83.76% | 69.20% | 50.913 |
| 6 | legacy | 1.05% | 82.87% | 67.73% | 56.058 |
| 7 | heft | 1.29% | 82.56% | 68.13% | 52.228 |
| 8 | legacy-tuned | 8.11% | 98.70% | 69.73% | 57.560 |
| 9 | legacy-v1 | 32.64% | 214.11% | 66.53% | 69.147 |
| 10 | k8s-default | 86.62% | 1420.09% | 36.27% | 115.425 |

### Independence of seeds

Distinct outcomes per (cluster, case, policy): 20 exploratory seeds use the declared uncertainty, 50 holdout seeds use the uncertainty floor.

| Cluster | Exploratory mean (of 20) | Min | Max | Holdout mean (of 50) | Min | Max |
|---|---:|---:|---:|---:|---:|---:|
| asymmetric-edge-5 | 5.8 | 1 | 20 | 50.0 | 50 | 50 |
| balanced-heterogeneous-4 | 4.6 | 1 | 20 | 50.0 | 50 | 50 |
| homogeneous-4 | 3.2 | 1 | 20 | 50.0 | 50 | 50 |
| local-i5-8300h | 20.0 | 20 | 20 | 50.0 | 50 | 50 |
| unreliable-thermal-3 | 9.3 | 1 | 20 | 50.0 | 50 | 50 |

### Holdout (+/-10% perturbation, untouched): paired contrasts

| Policy vs baseline | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| heft-ra vs heft | mean_response_s | 1000 | 0.9562 | [0.9449, 0.9676] | 488 / 327 / 185 |
| heft-ra-cap vs heft-ra | mean_response_s | 1000 | 0.9998 | [0.9983, 1.0014] | 84 / 880 / 36 |
| heft-ra-cap vs heft | mean_response_s | 1000 | 0.9560 | [0.9445, 0.9673] | 522 / 283 / 195 |
| adaptive vs adaptive-no-preempt | mean_response_s | 1000 | 0.9934 | [0.9889, 0.9977] | 79 / 850 / 71 |
| adaptive vs heft | mean_response_s | 1000 | 0.9685 | [0.9561, 0.9821] | 365 / 457 / 178 |
| heft vs k8s-default | mean_response_s | 1000 | 0.6748 | [0.6501, 0.7000] | 721 / 50 / 229 |
| legacy-tuned vs legacy-v1 | mean_response_s | 1000 | 0.8984 | [0.8880, 0.9085] | 558 / 349 / 93 |

### Holdout (+/-10% perturbation, untouched): leaders by workload

| Workload | Lowest mean response regret | Best deadline hit | Lowest median makespan |
|---|---|---|---|
| data-locality-wave | heft-ra | n/a | heft-ra-cap |
| mixed-arrivals | heft-ra-cap | adaptive, adaptive-no-preempt, heft-ra-cap | adaptive, adaptive-no-preempt |
| priority-burst | adaptive | legacy-tuned | heft-ra, heft-ra-cap |
| recovery-gang-mix | eft, heft, legacy | legacy-v1 | adaptive, adaptive-no-preempt |

### Holdout (+/-10% perturbation, untouched): leaders by cluster family

| Cluster family | Lowest mean response regret | Best deadline hit | Lowest median makespan |
|---|---|---|---|
| asymmetric-network | heft-ra, heft-ra-cap | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | eft, heft, legacy |
| balanced-heterogeneous | heft-ra-cap | legacy-tuned | heft-ra-cap |
| homogeneous | eft | heft-ra-cap | heft-ra-cap |
| local-calibration | adaptive | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | heft, heft-ra, heft-ra-cap |
| unreliable-thermal | heft-ra, heft-ra-cap | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | eft, heft |

### Holdout (+/-10% perturbation, untouched): leaders by workload and cluster family

Each cell names the policy (or exact tie) with the lowest mean regret in that cell, followed by that regret and, in brackets, HEFT-RA's own mean regret.

| Workload | asymmetric-network | balanced-heterogeneous | homogeneous | local-calibration | unreliable-thermal |
|---|---|---|---|---|---|
| data-locality-wave | heft-ra, heft-ra-cap (0.34%) [0.34%] | heft-ra-cap (0.05%) [2.18%] | heft-ra (1.05%) [1.05%] | eft, legacy (1.67%) [26.54%] | eft, legacy (18.72%) [34.28%] |
| mixed-arrivals | heft-ra, heft-ra-cap (0.07%) [0.07%] | heft-ra (0.00%) [0.00%] | legacy (0.82%) [4.36%] | adaptive (1.67%) [28.21%] | adaptive, adaptive-no-preempt (1.33%) [37.07%] |
| priority-burst | eft, heft, legacy (0.45%) [0.92%] | heft-ra-cap (0.06%) [0.36%] | legacy-tuned (0.06%) [1.52%] | adaptive (0.00%) [64.26%] | heft-ra, heft-ra-cap (23.39%) [23.39%] |
| recovery-gang-mix | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | legacy-v1 (0.81%) [1.31%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft, legacy, legacy-tuned (0.00%) [4.62%] |

### Fresh holdout (seeds 600-649, run once for the final policy set): paired contrasts

| Policy vs baseline | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| heft-ra vs heft | mean_response_s | 1000 | 0.9643 | [0.9520, 0.9761] | 460 / 318 / 222 |
| heft-ra-cap vs heft-ra | mean_response_s | 1000 | 0.9973 | [0.9951, 0.9995] | 98 / 873 / 29 |
| heft-ra-cap vs heft | mean_response_s | 1000 | 0.9617 | [0.9494, 0.9735] | 504 / 281 / 215 |
| adaptive vs adaptive-no-preempt | mean_response_s | 1000 | 0.9935 | [0.9889, 0.9978] | 82 / 850 / 68 |
| adaptive vs heft | mean_response_s | 1000 | 0.9671 | [0.9555, 0.9789] | 360 / 453 / 187 |
| heft vs k8s-default | mean_response_s | 1000 | 0.6689 | [0.6446, 0.6937] | 724 / 50 / 226 |
| legacy-tuned vs legacy-v1 | mean_response_s | 1000 | 0.8944 | [0.8837, 0.9055] | 573 / 339 / 88 |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by workload

| Workload | Lowest mean response regret | Best deadline hit | Lowest median makespan |
|---|---|---|---|
| data-locality-wave | heft-ra-cap | n/a | heft-ra-cap |
| mixed-arrivals | heft-ra-cap | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap | adaptive, adaptive-no-preempt |
| priority-burst | heft-ra-cap | legacy-tuned | heft-ra, heft-ra-cap |
| recovery-gang-mix | eft, heft | legacy-v1 | adaptive, adaptive-no-preempt |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by cluster family

| Cluster family | Lowest mean response regret | Best deadline hit | Lowest median makespan |
|---|---|---|---|
| asymmetric-network | heft-ra, heft-ra-cap | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | eft, heft, legacy |
| balanced-heterogeneous | heft-ra-cap | legacy-tuned | heft-ra-cap |
| homogeneous | eft | heft-ra, heft-ra-cap | heft-ra-cap |
| local-calibration | adaptive | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | eft, legacy |
| unreliable-thermal | heft-ra, heft-ra-cap | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | eft, heft, legacy |

### Fresh holdout (seeds 600-649, run once for the final policy set): leaders by workload and cluster family

Each cell names the policy (or exact tie) with the lowest mean regret in that cell, followed by that regret and, in brackets, HEFT-RA's own mean regret.

| Workload | asymmetric-network | balanced-heterogeneous | homogeneous | local-calibration | unreliable-thermal |
|---|---|---|---|---|---|
| data-locality-wave | legacy (0.46%) [0.49%] | heft-ra-cap (0.00%) [6.78%] | eft (2.18%) [3.67%] | legacy-tuned, legacy-v1 (1.21%) [24.83%] | eft, legacy (15.29%) [42.32%] |
| mixed-arrivals | heft-ra, heft-ra-cap (0.05%) [0.05%] | heft-ra (0.00%) [0.00%] | heft-ra-cap (1.06%) [5.02%] | adaptive (2.26%) [29.55%] | adaptive, adaptive-no-preempt (0.66%) [38.77%] |
| priority-burst | legacy (0.46%) [1.07%] | heft-ra-cap (0.02%) [0.39%] | legacy-tuned (0.04%) [1.50%] | adaptive (0.00%) [64.12%] | heft-ra, heft-ra-cap (18.86%) [18.86%] |
| recovery-gang-mix | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.00%) [0.00%] | heft-ra, heft-ra-cap (0.84%) [0.84%] | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 (0.00%) [0.00%] | eft, heft (0.00%) [3.96%] |

## Static Oracle Subset (development phase)

The oracle is excluded from deployable-policy regret. This table
reports the tractable exploratory cases where it exists.

| Policy | Cases | Mean gap | 95% CI | Median gap | P95 gap |
|---|---:|---:|---:|---:|---:|
| heft-ra | 300 | 2.53% | [1.71%, 3.40%] | 0.00% | 29.55% |
| heft-ra-cap | 300 | 2.53% | [1.71%, 3.40%] | 0.00% | 29.55% |
| heft | 300 | 3.06% | [2.22%, 3.95%] | 0.00% | 29.55% |
| eft | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| legacy | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| legacy-v1 | 300 | 3.85% | [2.91%, 4.83%] | 0.00% | 29.55% |
| k8s-default | 300 | 4.07% | [3.09%, 5.08%] | 0.00% | 29.55% |
| adaptive | 300 | 5.60% | [4.32%, 7.04%] | 0.00% | 40.77% |
| adaptive-no-preempt | 300 | 5.60% | [4.32%, 7.04%] | 0.00% | 40.77% |
| legacy-tuned | 300 | 6.01% | [4.65%, 7.48%] | 0.00% | 40.77% |
| fcfs | 300 | 614.26% | [434.79%, 809.46%] | 0.00% | 6085.26% |
| random | 300 | 932.36% | [657.08%, 1238.43%] | 15.89% | 5974.14% |

