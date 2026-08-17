# Experiment Summary

Records: 26250  
Comparable cases: 3750

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft | 0.00% | 5.75% | 91.97% | 1.173 |
| 2 | eft | 0.00% | 10.05% | 91.97% | 1.173 |
| 3 | legacy | 0.00% | 17.73% | 91.89% | 1.173 |
| 4 | legacy-tuned | 0.00% | 54.68% | 83.87% | 1.181 |
| 5 | adaptive | 0.00% | 55.14% | 86.61% | 1.181 |
| 6 | adaptive-no-preempt | 0.00% | 55.14% | 86.61% | 1.181 |
| 7 | legacy-v1 | 0.00% | 84.32% | 74.53% | 1.189 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | adaptive, adaptive-no-preempt, heft | 0.00% | 0.00% |
| balanced-heterogeneous | adaptive, adaptive-no-preempt | 0.00% | 0.00% |
| homogeneous | eft, legacy | 0.00% | 0.00% |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| unreliable-thermal | eft, heft, legacy | 0.00% | 57.20% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft | 0.00% | 5.75% |
| cpu-heavy | adaptive, adaptive-no-preempt, eft, heft, legacy | 0.00% | 0.00% |
| data-heavy | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| deep | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| diamond | adaptive, adaptive-no-preempt, legacy-tuned | 0.00% | 0.00% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| fan-in | eft, heft, legacy | 0.00% | 0.00% |
| fan-out | adaptive, adaptive-no-preempt | 0.00% | 0.00% |
| gang | adaptive, adaptive-no-preempt, eft, heft, legacy | 0.00% | 0.00% |
| io-heavy | eft, heft, legacy | 0.00% | 0.00% |
| linear | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| memory-heavy | eft, heft, legacy | 0.00% | 0.00% |
| mixed | adaptive, adaptive-no-preempt | 0.00% | 0.00% |
| priority | eft, heft, legacy, legacy-v1 | 0.00% | 0.00% |
| wide | eft, heft, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
