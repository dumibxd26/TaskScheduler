# Experiment Summary

Records: 37500  
Comparable cases: 3750

## Overall Ranking

| Rank | Policy | Median regret | P95 regret | Win rate | Median / lower bound |
|---:|---|---:|---:|---:|---:|
| 1 | heft-ra | 0.00% | 0.00% | 95.39% | 1.197 |
| 2 | heft-ra-cap | 0.00% | 0.00% | 95.39% | 1.197 |
| 3 | heft | 0.00% | 49.91% | 79.36% | 1.208 |
| 4 | eft | 0.00% | 49.91% | 78.93% | 1.208 |
| 5 | legacy | 0.00% | 52.55% | 78.35% | 1.208 |
| 6 | adaptive | 0.00% | 62.25% | 72.03% | 1.228 |
| 7 | adaptive-no-preempt | 0.00% | 62.25% | 72.03% | 1.228 |
| 8 | legacy-tuned | 0.00% | 71.85% | 57.84% | 1.242 |
| 9 | legacy-v1 | 0.00% | 174.40% | 53.84% | 1.252 |
| 10 | k8s-default | 5.27% | 1248.49% | 41.95% | 1.451 |

## Cluster Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| asymmetric-network | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| balanced-heterogeneous | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| homogeneous | heft-ra, heft-ra-cap | 0.00% | 0.21% |
| local-calibration | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, k8s-default, legacy, legacy-tuned, legacy-v1 | 0.00% | 0.00% |
| unreliable-thermal | heft-ra, heft-ra-cap | 0.00% | 15.41% |

## Workflow Family Specialists

| Family | Policy | Median regret | P95 regret |
|---|---|---:|---:|
| cache-sensitive | heft-ra, heft-ra-cap | 0.00% | 5.19% |
| cpu-heavy | heft-ra, heft-ra-cap | 0.00% | 15.55% |
| data-heavy | heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| deep | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| diamond | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| failure-recovery | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-in | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| fan-out | heft-ra, heft-ra-cap | 0.00% | 0.00% |
| gang | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| io-heavy | adaptive, adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap | 0.00% | 0.00% |
| linear | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| memory-heavy | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
| mixed | heft-ra, heft-ra-cap | 0.00% | 20.46% |
| priority | heft-ra, heft-ra-cap | 0.00% | 2.69% |
| wide | eft, heft, heft-ra, heft-ra-cap, legacy | 0.00% | 0.00% |
