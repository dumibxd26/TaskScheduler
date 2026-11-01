# Experiment Summary

Records: 37500  
Comparable cases: 3750

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.00% | 95.39% | 1.194 |
| 2 | heft-ra-cap | 0.00% | 0.00% | 95.39% | 1.194 |
| 3 | heft | 0.00% | 49.02% | 80.05% | 1.203 |
| 4 | eft | 0.00% | 49.02% | 79.65% | 1.203 |
| 5 | legacy | 0.00% | 54.23% | 78.83% | 1.203 |
| 6 | adaptive | 0.00% | 57.83% | 71.95% | 1.226 |
| 7 | adaptive-no-preempt | 0.00% | 57.83% | 71.95% | 1.226 |
| 8 | legacy-tuned | 0.00% | 73.00% | 56.64% | 1.240 |
| 9 | legacy-v1 | 0.00% | 173.88% | 52.91% | 1.254 |
| 10 | k8s-default | 5.20% | 1296.03% | 42.32% | 1.447 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| homogeneous | heft-ra, heft-ra-cap | 0.00% | 0.23% |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| unreliable-thermal | heft-ra, heft-ra-cap | 0.00% | 16.43% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft-ra, heft-ra-cap | 0.00% | 4.21% |
| cpu-heavy | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| data-heavy | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| deep | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| diamond | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-in | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-out | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| gang | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap | 0.00% | 0.00% |
| linear | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| memory-heavy | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| mixed | heft-ra, heft-ra-cap | 0.00% | 21.49% |
| priority | heft-ra, heft-ra-cap | 0.00% | 2.52% |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
