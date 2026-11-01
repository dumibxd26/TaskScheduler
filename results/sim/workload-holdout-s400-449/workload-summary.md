# Multi-Workflow Experiment Summary

Records: 10000  
Comparable cases: 1000

Ranked by median per-case response regret; other objectives are listed
as separate leaders below.

| Rank | Policy | Median response regret | Mean response regret | P95 response regret | Deadline hit rate | Mean preemptions | Median makespan (s) | Median energy (J) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.04% | 11.47% | 63.88% | 70.27% | 0.00 | 47.832 | 2690.0 |
| 2 | heft-ra | 0.42% | 11.52% | 63.88% | 69.47% | 0.00 | 48.733 | 2685.8 |
| 3 | adaptive | 0.77% | 15.83% | 83.64% | 67.60% | 0.76 | 53.173 | 2784.0 |
| 4 | eft | 1.01% | 19.75% | 82.36% | 66.13% | 0.00 | 53.537 | 2811.3 |
| 5 | legacy | 1.04% | 20.62% | 83.09% | 66.00% | 0.00 | 55.091 | 2807.5 |
| 6 | adaptive-no-preempt | 1.20% | 16.96% | 83.64% | 67.60% | 0.00 | 50.981 | 2799.3 |
| 7 | heft | 1.57% | 21.51% | 82.36% | 66.13% | 0.00 | 52.639 | 2820.5 |
| 8 | legacy-tuned | 9.19% | 28.82% | 97.30% | 67.87% | 0.00 | 57.433 | 3136.7 |
| 9 | legacy-v1 | 30.20% | 51.32% | 208.81% | 67.47% | 0.00 | 69.878 | 3439.9 |
| 10 | k8s-default | 82.68% | 350.64% | 1735.10% | 35.20% | 0.00 | 117.989 | 5245.5 |

## Objective leaders

| Objective | Policies | Value |
|---|---|---:|
| median_response_regret | heft-ra-cap | 0.00040848 |
| p95_response_regret | heft-ra, heft-ra-cap | 0.638801 |
| deadline_hit_rate | heft-ra-cap | 0.702667 |
| median_makespan_s | heft-ra-cap | 47.832 |
| median_energy_j | heft-ra | 2685.81 |
