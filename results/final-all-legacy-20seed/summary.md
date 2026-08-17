# Experiment Summary

Records: 13800  
Comparable cases: 1500

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft | 0.00% | 57.83% | 83.87% | 1.173 |
| 2 | eft | 0.00% | 57.83% | 83.87% | 1.173 |
| 3 | legacy | 0.00% | 57.83% | 83.87% | 1.173 |
| 4 | adaptive | 0.00% | 62.57% | 74.73% | 1.181 |
| 5 | adaptive-no-preempt | 0.00% | 62.57% | 74.73% | 1.181 |
| 6 | legacy-tuned | 0.00% | 84.32% | 76.00% | 1.181 |
| 7 | legacy-v1 | 0.00% | 131.09% | 69.33% | 1.189 |
| 8 | fcfs | 0.00% | 691.00% | 60.00% | 1.493 |
| 9 | random | 105.23% | 1673.04% | 27.93% | 2.884 |

## Reference Oracles

These are non-deployable references evaluated only where exhaustive search was tractable.

| Reference | Cases | Median regret | P95 regret | Median / lower bound |
|---|---:|---:|---:|---:|
| oracle-static | 300 | 0.00% | 0.00% | 1.249 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | adaptive, adaptive-no-preempt, heft | 0.00% | 0.00% |
| balanced-heterogeneous | adaptive, adaptive-no-preempt | 0.00% | 305.08% |
| homogeneous | adaptive, adaptive-no-preempt, eft, heft, legacy | 0.00% | 103.33% |
| local-calibration | adaptive, adaptive-no-preempt, eft, fcfs, heft, legacy, legacy-tuned, legacy-v1, random | 0.00% | 0.00% |
| unreliable-thermal | eft, heft | 0.00% | 61.36% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft | 0.00% | 5.75% |
| cpu-heavy | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 42.49% |
| data-heavy | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| deep | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| diamond | adaptive, adaptive-no-preempt, legacy-tuned | 0.00% | 48.50% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 29.55% |
| fan-in | eft, heft, legacy | 0.00% | 103.33% |
| fan-out | adaptive, adaptive-no-preempt | 0.00% | 305.08% |
| gang | adaptive, adaptive-no-preempt, eft, heft, legacy | 0.00% | 0.00% |
| io-heavy | eft, heft, legacy | 0.00% | 0.00% |
| linear | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| memory-heavy | eft, heft, legacy | 0.00% | 0.00% |
| mixed | adaptive, adaptive-no-preempt | 0.00% | 20.68% |
| priority | eft, heft, legacy, legacy-v1 | 0.00% | 8.38% |
| wide | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
