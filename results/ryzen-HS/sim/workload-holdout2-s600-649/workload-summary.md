# Multi-Workflow Experiment Summary

Records: 10000  
Comparable cases: 1000

Ranked by median per-case response regret; other objectives are listed
as separate leaders below.

| Rank | Policy | Median response regret | Mean response regret | P95 response regret | Deadline hit rate | Mean preemptions | Median makespan (s) | Median energy (J) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.00% | 11.72% | 63.76% | 71.47% | 0.00 | 47.211 | 2649.9 |
| 2 | heft-ra | 0.51% | 12.11% | 63.76% | 70.53% | 0.00 | 48.201 | 2646.9 |
| 3 | adaptive | 0.65% | 15.90% | 83.76% | 69.20% | 0.77 | 53.275 | 2745.0 |
| 4 | eft | 0.97% | 19.80% | 82.56% | 68.13% | 0.00 | 53.099 | 2767.4 |
| 5 | adaptive-no-preempt | 1.00% | 17.03% | 83.76% | 69.20% | 0.00 | 50.913 | 2760.6 |
| 6 | legacy | 1.05% | 21.00% | 82.87% | 67.73% | 0.00 | 56.058 | 2768.8 |
| 7 | heft | 1.29% | 21.53% | 82.56% | 68.13% | 0.00 | 52.228 | 2767.4 |
| 8 | legacy-tuned | 8.11% | 27.92% | 98.70% | 69.73% | 0.00 | 57.560 | 3120.7 |
| 9 | legacy-v1 | 32.64% | 51.41% | 214.11% | 66.53% | 0.00 | 69.147 | 3426.9 |
| 10 | k8s-default | 86.62% | 347.09% | 1420.09% | 36.27% | 0.00 | 115.425 | 4970.2 |

## Objective leaders

| Objective | Policies | Value |
|---|---|---:|
| median_response_regret | heft-ra-cap | 0 |
| p95_response_regret | heft-ra, heft-ra-cap | 0.637551 |
| deadline_hit_rate | heft-ra-cap | 0.714667 |
| median_makespan_s | heft-ra-cap | 47.2109 |
| median_energy_j | heft-ra | 2646.88 |
