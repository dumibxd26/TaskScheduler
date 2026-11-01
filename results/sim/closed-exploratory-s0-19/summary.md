# Experiment Summary

Records: 18300  
Comparable cases: 1500

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 5.75% | 92.00% | 1.181 |
| 2 | heft-ra-cap | 0.00% | 5.75% | 92.00% | 1.181 |
| 3 | heft | 0.00% | 57.20% | 85.33% | 1.181 |
| 4 | eft | 0.00% | 57.20% | 85.33% | 1.181 |
| 5 | legacy | 0.00% | 57.20% | 85.33% | 1.181 |
| 6 | adaptive | 0.00% | 60.08% | 78.67% | 1.186 |
| 7 | adaptive-no-preempt | 0.00% | 60.08% | 78.67% | 1.186 |
| 8 | legacy-tuned | 0.00% | 74.07% | 74.67% | 1.186 |
| 9 | legacy-v1 | 0.00% | 174.70% | 68.00% | 1.195 |
| 10 | k8s-default | 0.00% | 1263.72% | 55.13% | 1.428 |
| 11 | fcfs | 0.00% | 3253.38% | 61.33% | 1.424 |
| 12 | random | 190.98% | 11379.65% | 27.67% | 4.133 |

## Reference Oracles

These are non-deployable references evaluated only where exhaustive search was tractable.

| Reference | Cases | Median regret | P95 regret | Median / lower bound |
|---|---:|---:|---:|---:|
| oracle-static | 300 | 0.00% | 0.00% | 1.249 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap | 0.00% | 0.00% |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.00% | 0.16% |
| homogeneous | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 75.73% |
| local-calibration | adaptive, adaptive-no-preempt, eft, fcfs, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1, random | 0.00% | 0.00% |
| unreliable-thermal | heft-ra, heft-ra-cap | 0.00% | 33.37% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft, heft-ra, heft-ra-cap | 0.00% | 5.75% |
| cpu-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 34.88% |
| data-heavy | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| deep | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| diamond | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| fan-in | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 75.73% |
| fan-out | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| gang | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| linear | eft, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| memory-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy | 0.00% | 0.00% |
| mixed | heft-ra, heft-ra-cap | 0.00% | 15.67% |
| priority | heft-ra, heft-ra-cap | 0.00% | 2.48% |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
