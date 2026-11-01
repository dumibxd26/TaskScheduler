# Experiment Summary

Records: 37500  
Comparable cases: 3750

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.12% | 94.80% | 1.196 |
| 2 | heft-ra-cap | 0.00% | 0.12% | 94.80% | 1.196 |
| 3 | heft | 0.00% | 50.59% | 80.03% | 1.205 |
| 4 | eft | 0.00% | 50.59% | 79.36% | 1.205 |
| 5 | legacy | 0.00% | 52.05% | 78.75% | 1.205 |
| 6 | adaptive | 0.00% | 64.20% | 73.17% | 1.225 |
| 7 | adaptive-no-preempt | 0.00% | 64.20% | 73.17% | 1.225 |
| 8 | legacy-tuned | 0.00% | 68.09% | 58.24% | 1.241 |
| 9 | legacy-v1 | 0.00% | 175.47% | 54.61% | 1.256 |
| 10 | k8s-default | 5.09% | 1257.52% | 42.51% | 1.456 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| homogeneous | heft-ra, heft-ra-cap | 0.00% | 1.07% |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| unreliable-thermal | heft-ra, heft-ra-cap | 0.00% | 14.37% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft-ra, heft-ra-cap | 0.00% | 5.69% |
| cpu-heavy | heft-ra, heft-ra-cap | 0.00% | 31.36% |
| data-heavy | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| deep | heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| diamond | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-in | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-out | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| gang | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap | 0.00% | 0.00% |
| linear | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| memory-heavy | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| mixed | heft-ra, heft-ra-cap | 0.00% | 20.67% |
| priority | heft-ra, heft-ra-cap | 0.00% | 2.77% |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
