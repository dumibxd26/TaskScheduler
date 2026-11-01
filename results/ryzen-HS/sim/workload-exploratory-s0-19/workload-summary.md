# Multi-Workflow Experiment Summary

Records: 4800  
Comparable cases: 400

Ranked by median per-case response regret; other objectives are listed
as separate leaders below.

| Rank | Policy | Median response regret | Mean response regret | P95 response regret | Deadline hit rate | Mean preemptions | Median makespan (s) | Median energy (J) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | legacy | 0.13% | 17.41% | 69.81% | 66.67% | 0.00 | 54.682 | 2948.3 |
| 2 | eft | 0.75% | 16.98% | 69.81% | 66.67% | 0.00 | 54.549 | 2908.7 |
| 3 | adaptive | 0.99% | 11.34% | 66.23% | 66.67% | 0.75 | 51.792 | 2875.0 |
| 4 | heft-ra | 1.01% | 11.89% | 48.64% | 66.67% | 0.00 | 47.657 | 2788.2 |
| 5 | adaptive-no-preempt | 1.13% | 12.37% | 66.23% | 66.67% | 0.00 | 49.035 | 2901.3 |
| 6 | heft | 1.27% | 18.69% | 69.81% | 66.67% | 0.00 | 54.549 | 2908.7 |
| 7 | heft-ra-cap | 1.75% | 11.97% | 48.64% | 66.67% | 0.00 | 47.657 | 2841.3 |
| 8 | legacy-tuned | 5.24% | 29.45% | 82.75% | 66.67% | 0.00 | 60.192 | 2992.6 |
| 9 | legacy-v1 | 28.61% | 43.68% | 117.88% | 66.67% | 0.00 | 69.480 | 3120.6 |
| 10 | fcfs | 37.90% | 1060.69% | 8102.99% | 66.67% | 0.00 | 79.722 | 3833.1 |
| 11 | k8s-default | 75.57% | 337.40% | 1384.96% | 30.33% | 0.00 | 115.185 | 5275.6 |
| 12 | random | 157.75% | 3308.07% | 9655.08% | 26.33% | 0.00 | 170.228 | 7377.3 |

## Objective leaders

| Objective | Policies | Value |
|---|---|---:|
| median_response_regret | legacy | 0.00134716 |
| p95_response_regret | heft-ra, heft-ra-cap | 0.486425 |
| deadline_hit_rate | adaptive, adaptive-no-preempt, eft, fcfs, heft, heft-ra, heft-ra-cap, legacy, legacy-tuned, legacy-v1 | 0.666667 |
| median_makespan_s | heft-ra, heft-ra-cap | 47.6566 |
| median_energy_j | heft-ra | 2788.21 |
