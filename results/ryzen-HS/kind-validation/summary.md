# Kind validation summary

Integrity: passed. same-host Kind worker containers with emulated speeds, slot capacities, and tc-netem links; descriptive, three repetitions per profile, batch, and policy.

## Pooled `priority` batch (median over trials)

| Policy | Trials | Makespan (s) | Mean response (s) | P95 response (s) | Critical (s) | Preemptions | Remote reads |
|---|---:|---:|---:|---:|---:|---:|---:|
| adaptive | 9 | 45.17 | 30.33 | 45.17 | 14.97 | 26 | 3.0 |
| heft | 9 | 45.61 | 30.96 | 38.58 | 22.15 | 0 | 3.0 |
| legacy-v1 | 9 | 46.59 | 30.19 | 39.55 | 16.13 | 0 | 3.0 |
| kube-default | 9 | 47.03 | 30.78 | 40.01 | 15.99 | 0 | 3.0 |
| heft-ra | 9 | 47.14 | 32.49 | 40.10 | 22.91 | 0 | 3.0 |
| heft-ra-cap | 9 | 47.14 | 33.37 | 40.12 | 23.95 | 0 | 3.0 |
| legacy-tuned | 9 | 47.22 | 30.49 | 40.19 | 16.87 | 0 | 3.0 |
| eft | 9 | 47.32 | 29.31 | 40.29 | 15.42 | 0 | 3.0 |
| adaptive-no-preempt | 9 | 48.06 | 31.12 | 41.03 | 15.69 | 0 | 3.0 |

## Pooled `dag` batch (median over trials)

| Policy | Trials | Makespan (s) | Mean response (s) | P95 response (s) | Critical (s) | Preemptions | Remote reads |
|---|---:|---:|---:|---:|---:|---:|---:|
| heft | 9 | 32.55 | 26.64 | 32.54 | n/a | 0 | 3.0 |
| heft-ra | 9 | 32.65 | 26.00 | 32.65 | n/a | 0 | 3.0 |
| heft-ra-cap | 9 | 34.04 | 27.18 | 34.02 | n/a | 0 | 4.0 |
| kube-default | 9 | 35.26 | 26.77 | 35.23 | n/a | 0 | 5.0 |
| legacy-tuned | 9 | 35.32 | 28.34 | 35.30 | n/a | 0 | 4.0 |
| adaptive | 9 | 36.51 | 26.87 | 36.50 | n/a | 0 | 5.0 |
| eft | 9 | 37.80 | 28.58 | 37.79 | n/a | 0 | 5.0 |
| adaptive-no-preempt | 9 | 37.99 | 28.64 | 37.98 | n/a | 0 | 4.0 |
| legacy-v1 | 9 | 38.87 | 28.28 | 38.86 | n/a | 0 | 5.0 |

## Per-profile medians

### balanced / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra | 32.91 | 27.62 | 25.30 | 21.88 |
| heft | 33.69 | 27.62 | 29.87 | 23.97 |
| heft-ra-cap | 34.83 | 29.25 | 29.87 | 23.97 |
| adaptive | 36.51 | 29.97 | 29.87 | 23.97 |
| adaptive-no-preempt | 37.99 | 29.23 | 29.87 | 23.97 |
| legacy-tuned | 40.42 | 30.61 | 37.21 | 24.61 |
| legacy-v1 | 40.60 | 30.25 | 37.22 | 24.61 |
| eft | 41.04 | 30.34 | 37.21 | 24.61 |
| kube-default | 44.13 | 31.99 | 32.30 | 25.04 |

makespan_seconds: Spearman 0.82, MAPE 15.6%, live best heft-ra, twin best heft-ra.

mean_response_seconds: Spearman 0.90, MAPE 18.8%, live best heft, heft-ra, twin best heft-ra.

### balanced / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra-cap | 52.39 | 40.36 | 41.96 | 32.09 |
| eft | 52.78 | 36.44 | 43.06 | 28.18 |
| adaptive-no-preempt | 53.05 | 35.66 | 43.06 | 28.44 |
| legacy-v1 | 53.20 | 36.41 | 43.06 | 28.18 |
| heft-ra | 53.28 | 40.33 | 41.96 | 32.09 |
| heft | 53.69 | 40.26 | 41.96 | 32.09 |
| legacy-tuned | 53.94 | 36.34 | 43.06 | 28.18 |
| kube-default | 54.87 | 36.64 | 43.06 | 28.18 |
| adaptive | 67.30 | 35.77 | 55.01 | 24.85 |

makespan_seconds: Spearman 0.45, MAPE 19.9%, live best adaptive-no-preempt, eft, heft-ra, heft-ra-cap, legacy-v1, twin best heft, heft-ra, heft-ra-cap.

mean_response_seconds: Spearman 0.67, MAPE 22.5%, live best adaptive, adaptive-no-preempt, legacy-tuned, twin best adaptive.

### edge / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra | 33.60 | 26.00 | 33.55 | 22.79 |
| heft | 33.77 | 26.64 | 33.55 | 22.79 |
| heft-ra-cap | 34.83 | 27.18 | 33.55 | 22.79 |
| legacy-tuned | 35.16 | 28.34 | 28.47 | 22.73 |
| kube-default | 35.26 | 26.77 | 29.91 | 22.99 |
| eft | 37.80 | 27.25 | 28.47 | 24.37 |
| adaptive | 37.86 | 26.87 | 33.55 | 22.79 |
| legacy-v1 | 38.87 | 28.28 | 28.47 | 22.73 |
| adaptive-no-preempt | 39.51 | 27.87 | 33.55 | 22.79 |

makespan_seconds: Spearman -0.28, MAPE 13.0%, live best heft, heft-ra, twin best eft, legacy-tuned, legacy-v1.

mean_response_seconds: Spearman -0.49, MAPE 15.6%, live best heft-ra, twin best adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1.

### edge / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| adaptive | 45.17 | 30.33 | 59.90 | 25.95 |
| heft | 45.61 | 30.96 | 35.70 | 24.18 |
| legacy-v1 | 46.59 | 30.19 | 36.72 | 23.26 |
| heft-ra-cap | 46.70 | 33.37 | 35.70 | 24.18 |
| kube-default | 47.03 | 30.78 | 36.82 | 23.27 |
| heft-ra | 47.14 | 32.49 | 35.70 | 24.18 |
| legacy-tuned | 47.22 | 30.49 | 36.72 | 23.26 |
| eft | 47.32 | 29.31 | 36.72 | 23.26 |
| adaptive-no-preempt | 48.06 | 31.12 | 35.70 | 23.27 |

makespan_seconds: Spearman -0.14, MAPE 23.9%, live best adaptive, heft, twin best adaptive-no-preempt, heft, heft-ra, heft-ra-cap.

mean_response_seconds: Spearman 0.52, MAPE 22.9%, live best eft, twin best adaptive-no-preempt, eft, kube-default, legacy-tuned, legacy-v1.

### homogeneous / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra | 25.85 | 22.09 | 22.83 | 18.60 |
| heft | 27.46 | 23.02 | 22.83 | 18.60 |
| heft-ra-cap | 27.89 | 23.82 | 22.83 | 18.60 |
| kube-default | 32.00 | 24.63 | 21.90 | 18.51 |
| adaptive | 32.52 | 25.25 | 22.83 | 18.60 |
| legacy-tuned | 33.34 | 23.66 | 21.10 | 18.19 |
| legacy-v1 | 34.11 | 25.79 | 21.64 | 18.43 |
| adaptive-no-preempt | 34.59 | 25.25 | 22.83 | 18.60 |
| eft | 37.45 | 27.43 | 26.14 | 19.98 |

makespan_seconds: Spearman 0.04, MAPE 27.3%, live best heft-ra, twin best legacy-tuned.

mean_response_seconds: Spearman 0.17, MAPE 23.7%, live best heft-ra, twin best kube-default, legacy-tuned, legacy-v1.

### homogeneous / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft | 36.95 | 22.12 | 32.40 | 17.04 |
| heft-ra-cap | 37.87 | 21.82 | 32.40 | 17.04 |
| eft | 38.62 | 22.52 | 32.40 | 16.81 |
| adaptive-no-preempt | 38.70 | 23.34 | 32.40 | 17.04 |
| adaptive | 39.62 | 23.67 | 39.40 | 18.37 |
| legacy-v1 | 40.20 | 24.12 | 32.40 | 16.70 |
| legacy-tuned | 40.48 | 24.81 | 32.40 | 16.70 |
| kube-default | 40.80 | 23.04 | 32.40 | 16.77 |
| heft-ra | 40.87 | 23.39 | 32.40 | 17.04 |

makespan_seconds: Spearman 0.00, MAPE 15.6%, live best heft, twin best adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1.

mean_response_seconds: Spearman -0.29, MAPE 26.4%, live best heft, heft-ra-cap, twin best adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1.

## Twin agreement (original twin)

| Profile | Batch | Metric | Spearman | MAPE | Mean bias | Live best | Twin best |
|---|---|---|---:|---:|---:|---|---|
| balanced | dag | makespan_seconds | 0.82 | 15.6% | -15.6% | heft-ra | heft-ra |
| balanced | dag | mean_response_seconds | 0.90 | 18.8% | -18.8% | heft, heft-ra | heft-ra |
| balanced | priority | makespan_seconds | 0.45 | 19.9% | -19.9% | adaptive-no-preempt, eft, heft-ra, heft-ra-cap, legacy-v1 | heft, heft-ra, heft-ra-cap |
| balanced | priority | mean_response_seconds | 0.67 | 22.5% | -22.5% | adaptive, adaptive-no-preempt, legacy-tuned | adaptive |
| edge | dag | makespan_seconds | -0.28 | 13.0% | -13.0% | heft, heft-ra | eft, legacy-tuned, legacy-v1 |
| edge | dag | mean_response_seconds | -0.49 | 15.6% | -15.6% | heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| edge | priority | makespan_seconds | -0.14 | 23.9% | -16.7% | adaptive, heft | adaptive-no-preempt, heft, heft-ra, heft-ra-cap |
| edge | priority | mean_response_seconds | 0.52 | 22.9% | -22.9% | eft | adaptive-no-preempt, eft, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | dag | makespan_seconds | 0.04 | 27.3% | -27.3% | heft-ra | legacy-tuned |
| homogeneous | dag | mean_response_seconds | 0.17 | 23.7% | -23.7% | heft-ra | kube-default, legacy-tuned, legacy-v1 |
| homogeneous | priority | makespan_seconds | 0.00 | 15.6% | -15.6% | heft | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | priority | mean_response_seconds | -0.29 | 26.4% | -26.4% | heft, heft-ra-cap | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |

## Twin agreement (twin with measured slot-release latency)

| Profile | Batch | Metric | Spearman | MAPE | Mean bias | Live best | Twin best |
|---|---|---|---:|---:|---:|---|---|
| balanced | dag | makespan_seconds | 0.68 | 12.5% | -12.5% | heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra-cap |
| balanced | dag | mean_response_seconds | 0.94 | 15.9% | -15.9% | heft, heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap |
| balanced | priority | makespan_seconds | 0.22 | 4.8% | -4.8% | adaptive-no-preempt, eft, heft-ra, heft-ra-cap, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| balanced | priority | mean_response_seconds | 0.60 | 8.6% | -8.6% | adaptive, adaptive-no-preempt, legacy-tuned | adaptive |
| edge | dag | makespan_seconds | -0.28 | 9.3% | -5.9% | heft, heft-ra | eft, legacy-tuned, legacy-v1 |
| edge | dag | mean_response_seconds | 0.58 | 8.5% | -8.5% | heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default |
| edge | priority | makespan_seconds | 0.18 | 8.4% | +0.9% | adaptive, heft | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| edge | priority | mean_response_seconds | 0.78 | 7.3% | -7.3% | eft | adaptive |
| homogeneous | dag | makespan_seconds | 0.62 | 20.6% | -20.6% | heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default |
| homogeneous | dag | mean_response_seconds | 0.48 | 16.7% | -16.7% | heft-ra | adaptive, adaptive-no-preempt, heft, heft-ra, kube-default |
| homogeneous | priority | makespan_seconds | -0.21 | 10.8% | -9.5% | heft | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | priority | mean_response_seconds | -0.36 | 15.0% | -15.0% | heft, heft-ra-cap | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |

## Paired contrasts (ratio policy / baseline, matched trials)

| Batch | Policy vs baseline | Metric | Pairs | Mean ratio | 95% bootstrap CI | Wins / losses |
|---|---|---|---:|---:|---|---:|
| dag | heft vs kube-default | makespan_seconds | 9 | 0.884 | 0.791-0.975 | 5 / 3 |
| dag | heft vs kube-default | mean_response_seconds | 9 | 0.950 | 0.897-1.003 | 6 / 2 |
| dag | heft vs kube-default | p95_response_seconds | 9 | 0.884 | 0.791-0.975 | 5 / 3 |
| dag | heft-ra vs heft | makespan_seconds | 9 | 0.974 | 0.911-1.033 | 5 / 3 |
| dag | heft-ra vs heft | mean_response_seconds | 9 | 0.992 | 0.925-1.078 | 6 / 3 |
| dag | heft-ra vs heft | p95_response_seconds | 9 | 0.974 | 0.911-1.033 | 5 / 3 |
| dag | heft-ra-cap vs heft | makespan_seconds | 9 | 1.059 | 0.995-1.133 | 3 / 5 |
| dag | heft-ra-cap vs heft | mean_response_seconds | 9 | 1.035 | 0.962-1.118 | 4 / 5 |
| dag | heft-ra-cap vs heft | p95_response_seconds | 9 | 1.059 | 0.995-1.133 | 3 / 5 |
| dag | heft-ra-cap vs heft-ra | makespan_seconds | 9 | 1.093 | 1.025-1.173 | 1 / 7 |
| dag | heft-ra-cap vs heft-ra | mean_response_seconds | 9 | 1.051 | 0.977-1.134 | 3 / 6 |
| dag | heft-ra-cap vs heft-ra | p95_response_seconds | 9 | 1.093 | 1.024-1.173 | 1 / 7 |
| dag | heft vs eft | makespan_seconds | 9 | 0.816 | 0.761-0.878 | 9 / 0 |
| dag | heft vs eft | mean_response_seconds | 9 | 0.912 | 0.881-0.942 | 9 / 0 |
| dag | heft vs eft | p95_response_seconds | 9 | 0.816 | 0.761-0.878 | 9 / 0 |
| dag | legacy-tuned vs legacy-v1 | makespan_seconds | 9 | 0.992 | 0.912-1.070 | 4 / 3 |
| dag | legacy-tuned vs legacy-v1 | mean_response_seconds | 9 | 0.973 | 0.915-1.021 | 4 / 2 |
| dag | legacy-tuned vs legacy-v1 | p95_response_seconds | 9 | 0.992 | 0.912-1.070 | 4 / 3 |
| dag | adaptive vs heft | makespan_seconds | 9 | 1.128 | 1.066-1.189 | 0 / 8 |
| dag | adaptive vs heft | mean_response_seconds | 9 | 1.038 | 0.994-1.082 | 3 / 5 |
| dag | adaptive vs heft | p95_response_seconds | 9 | 1.128 | 1.066-1.189 | 0 / 8 |
| priority | adaptive vs adaptive-no-preempt | makespan_seconds | 9 | 1.142 | 1.015-1.290 | 3 / 6 |
| priority | adaptive vs adaptive-no-preempt | mean_response_seconds | 9 | 1.020 | 0.964-1.082 | 3 / 3 |
| priority | adaptive vs adaptive-no-preempt | p95_response_seconds | 9 | 1.272 | 1.101-1.469 | 1 / 8 |
| priority | adaptive vs adaptive-no-preempt | critical_response_seconds | 9 | 0.927 | 0.881-0.982 | 8 / 1 |
| priority | heft vs kube-default | makespan_seconds | 9 | 0.968 | 0.926-1.007 | 7 / 2 |
| priority | heft vs kube-default | mean_response_seconds | 9 | 1.035 | 0.982-1.084 | 2 / 6 |
| priority | heft vs kube-default | p95_response_seconds | 9 | 0.962 | 0.911-1.009 | 7 / 2 |
| priority | heft vs kube-default | critical_response_seconds | 9 | 1.490 | 1.192-1.798 | 3 / 6 |
| priority | heft-ra vs heft | makespan_seconds | 9 | 1.026 | 0.984-1.073 | 3 / 5 |
| priority | heft-ra vs heft | mean_response_seconds | 9 | 1.018 | 0.982-1.055 | 3 / 5 |
| priority | heft-ra vs heft | p95_response_seconds | 9 | 1.032 | 0.982-1.090 | 3 / 5 |
| priority | heft-ra vs heft | critical_response_seconds | 9 | 1.036 | 0.964-1.102 | 3 / 6 |
| priority | heft-ra-cap vs heft | makespan_seconds | 9 | 1.013 | 0.956-1.087 | 5 / 3 |
| priority | heft-ra-cap vs heft | mean_response_seconds | 9 | 1.012 | 0.973-1.054 | 4 / 3 |
| priority | heft-ra-cap vs heft | p95_response_seconds | 9 | 1.019 | 0.953-1.110 | 5 / 4 |
| priority | heft-ra-cap vs heft | critical_response_seconds | 9 | 1.031 | 0.989-1.075 | 2 / 5 |
| priority | heft-ra-cap vs heft-ra | makespan_seconds | 9 | 0.989 | 0.935-1.041 | 5 / 4 |
| priority | heft-ra-cap vs heft-ra | mean_response_seconds | 9 | 0.996 | 0.951-1.043 | 4 / 5 |
| priority | heft-ra-cap vs heft-ra | p95_response_seconds | 9 | 0.989 | 0.929-1.050 | 5 / 4 |
| priority | heft-ra-cap vs heft-ra | critical_response_seconds | 9 | 1.005 | 0.940-1.082 | 5 / 3 |
| priority | legacy-tuned vs legacy-v1 | makespan_seconds | 9 | 1.003 | 0.971-1.027 | 2 / 5 |
| priority | legacy-tuned vs legacy-v1 | mean_response_seconds | 9 | 1.016 | 0.962-1.066 | 2 / 6 |
| priority | legacy-tuned vs legacy-v1 | p95_response_seconds | 9 | 1.003 | 0.966-1.032 | 2 / 5 |
| priority | legacy-tuned vs legacy-v1 | critical_response_seconds | 9 | 1.053 | 0.942-1.150 | 2 / 7 |
