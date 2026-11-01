# Multi-Workflow Experiment Summary

Records: 10000  
Comparable cases: 1000

Ranked by median per-case response regret; other objectives are listed
as separate leaders below.

| Rank | Policy | Median response regret | Mean response regret | P95 response regret | Deadline hit rate | Mean preemptions | Median makespan (s) | Median energy (J) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | heft-ra-cap | 0.04% | 11.98% | 63.78% | 70.67% | 0.00 | 48.562 | 2726.8 |
| 2 | heft-ra | 0.46% | 12.12% | 63.78% | 70.00% | 0.00 | 49.345 | 2742.9 |
| 3 | adaptive | 0.71% | 15.78% | 82.46% | 67.73% | 0.76 | 53.825 | 2843.1 |
| 4 | adaptive-no-preempt | 1.14% | 16.94% | 82.46% | 67.73% | 0.00 | 51.485 | 2849.6 |
| 5 | legacy | 1.20% | 20.23% | 82.46% | 66.40% | 0.00 | 56.027 | 2844.7 |
| 6 | eft | 1.21% | 19.45% | 81.55% | 66.80% | 0.00 | 54.904 | 2844.7 |
| 7 | heft | 1.58% | 21.16% | 81.55% | 66.80% | 0.00 | 53.676 | 2844.7 |
| 8 | legacy-tuned | 9.43% | 29.58% | 98.64% | 67.47% | 0.00 | 58.469 | 3141.2 |
| 9 | legacy-v1 | 30.65% | 49.78% | 190.29% | 67.33% | 0.00 | 69.667 | 3407.6 |
| 10 | k8s-default | 81.27% | 346.92% | 1639.35% | 34.13% | 0.00 | 117.250 | 5184.6 |

## Objective leaders

| Objective | Policies | Value |
|---|---|---:|
| median_response_regret | heft-ra-cap | 0.000422361 |
| p95_response_regret | heft-ra, heft-ra-cap | 0.637849 |
| deadline_hit_rate | heft-ra-cap | 0.706667 |
| median_makespan_s | heft-ra-cap | 48.5625 |
| median_energy_j | heft-ra-cap | 2726.77 |
