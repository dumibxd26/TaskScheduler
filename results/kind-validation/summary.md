# Kind validation summary

Integrity: passed. same-host Kind worker containers with emulated speeds, slot capacities, and tc-netem links; descriptive, three repetitions per profile, batch, and policy.

## Pooled `priority` batch (median over trials)

| Policy | Trials | Makespan (s) | Mean response (s) | P95 response (s) | Critical (s) | Preemptions | Remote reads |
|---|---:|---:|---:|---:|---:|---:|---:|
| eft | 9 | 47.27 | 31.08 | 40.22 | 16.56 | 0 | 3.0 |
| heft-ra | 9 | 47.52 | 34.09 | 40.48 | 23.60 | 0 | 2.0 |
| heft | 9 | 47.61 | 34.93 | 40.58 | 23.73 | 0 | 4.0 |
| heft-ra-cap | 9 | 47.81 | 35.04 | 40.78 | 23.89 | 0 | 3.0 |
| legacy-v1 | 9 | 48.05 | 31.52 | 41.02 | 16.59 | 0 | 3.0 |
| legacy-tuned | 9 | 48.05 | 31.46 | 41.03 | 16.64 | 0 | 3.0 |
| kube-default | 9 | 48.37 | 31.83 | 41.34 | 17.33 | 0 | 4.0 |
| adaptive-no-preempt | 9 | 49.02 | 32.12 | 41.99 | 16.65 | 0 | 3.0 |
| adaptive | 9 | 51.81 | 33.45 | 51.81 | 15.31 | 31 | 3.0 |

## Pooled `dag` batch (median over trials)

| Policy | Trials | Makespan (s) | Mean response (s) | P95 response (s) | Critical (s) | Preemptions | Remote reads |
|---|---:|---:|---:|---:|---:|---:|---:|
| heft-ra | 9 | 34.04 | 28.36 | 34.04 | n/a | 0 | 5.0 |
| heft-ra-cap | 9 | 34.74 | 27.47 | 34.73 | n/a | 0 | 3.0 |
| heft | 9 | 36.67 | 28.06 | 36.66 | n/a | 0 | 4.0 |
| adaptive | 9 | 36.94 | 27.87 | 36.93 | n/a | 0 | 3.0 |
| legacy-tuned | 9 | 38.11 | 28.66 | 38.09 | n/a | 0 | 4.0 |
| adaptive-no-preempt | 9 | 38.93 | 28.46 | 38.91 | n/a | 0 | 5.0 |
| kube-default | 9 | 38.99 | 28.58 | 38.97 | n/a | 0 | 5.0 |
| legacy-v1 | 9 | 39.30 | 28.45 | 39.28 | n/a | 0 | 4.0 |
| eft | 9 | 41.79 | 29.23 | 41.78 | n/a | 0 | 5.0 |

## Per-profile medians

### balanced / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra-cap | 35.88 | 30.29 | 36.39 | 29.19 |
| adaptive-no-preempt | 39.21 | 31.20 | 38.09 | 30.08 |
| heft-ra | 40.02 | 32.90 | 36.39 | 29.19 |
| legacy-v1 | 42.54 | 32.32 | 43.50 | 31.21 |
| adaptive | 43.46 | 33.00 | 38.09 | 30.08 |
| kube-default | 45.87 | 34.61 | 45.13 | 33.98 |
| heft | 45.97 | 37.25 | 38.09 | 30.08 |
| eft | 46.51 | 34.53 | 43.56 | 31.23 |
| legacy-tuned | 47.57 | 35.86 | 43.50 | 31.21 |

makespan_seconds: Spearman 0.68, MAPE 6.8%, live best heft-ra-cap, twin best heft-ra, heft-ra-cap.

mean_response_seconds: Spearman 0.50, MAPE 8.3%, live best heft-ra-cap, twin best heft-ra, heft-ra-cap.

### balanced / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| eft | 55.95 | 38.10 | 53.90 | 35.76 |
| heft | 57.70 | 45.43 | 51.95 | 38.84 |
| kube-default | 59.37 | 40.58 | 56.15 | 36.21 |
| heft-ra | 59.39 | 43.87 | 51.95 | 38.84 |
| legacy-tuned | 59.59 | 40.13 | 53.90 | 35.76 |
| adaptive-no-preempt | 61.19 | 42.62 | 53.90 | 35.76 |
| legacy-v1 | 61.56 | 42.28 | 53.90 | 35.76 |
| heft-ra-cap | 62.78 | 47.05 | 51.95 | 38.84 |
| adaptive | 81.63 | 40.84 | 56.79 | 30.39 |

makespan_seconds: Spearman 0.22, MAPE 12.6%, live best eft, twin best heft, heft-ra, heft-ra-cap.

mean_response_seconds: Spearman 0.67, MAPE 14.3%, live best eft, twin best adaptive.

### edge / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra | 34.46 | 28.36 | 35.17 | 24.25 |
| heft-ra-cap | 35.29 | 27.47 | 35.17 | 24.25 |
| adaptive | 36.59 | 27.64 | 35.17 | 24.25 |
| heft | 36.67 | 28.06 | 35.17 | 24.25 |
| legacy-tuned | 38.11 | 28.66 | 35.60 | 25.53 |
| kube-default | 38.99 | 28.58 | 33.36 | 24.47 |
| adaptive-no-preempt | 39.01 | 28.46 | 35.17 | 24.25 |
| legacy-v1 | 39.30 | 28.45 | 38.21 | 26.43 |
| eft | 41.79 | 29.23 | 38.21 | 26.17 |

makespan_seconds: Spearman 0.51, MAPE 5.8%, live best heft-ra, twin best kube-default.

mean_response_seconds: Spearman 0.69, MAPE 12.2%, live best adaptive, heft-ra-cap, twin best adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default.

### edge / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| eft | 47.27 | 31.08 | 38.85 | 24.68 |
| heft-ra | 47.52 | 34.09 | 38.09 | 25.71 |
| heft | 47.61 | 34.93 | 38.09 | 25.71 |
| heft-ra-cap | 47.81 | 35.04 | 38.09 | 25.71 |
| legacy-v1 | 48.05 | 31.52 | 38.85 | 24.68 |
| legacy-tuned | 48.05 | 31.46 | 38.85 | 24.68 |
| kube-default | 48.37 | 31.83 | 38.95 | 24.70 |
| adaptive-no-preempt | 49.02 | 32.12 | 38.09 | 24.69 |
| adaptive | 51.81 | 33.45 | 61.35 | 26.90 |

makespan_seconds: Spearman 0.59, MAPE 19.6%, live best eft, heft, heft-ra, heft-ra-cap, legacy-tuned, legacy-v1, twin best adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy-tuned, legacy-v1.

mean_response_seconds: Spearman 0.85, MAPE 22.9%, live best eft, legacy-tuned, legacy-v1, twin best adaptive-no-preempt, eft, kube-default, legacy-tuned, legacy-v1.

### homogeneous / dag

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft-ra | 28.32 | 24.62 | 27.46 | 22.18 |
| heft | 28.86 | 24.94 | 27.46 | 22.18 |
| heft-ra-cap | 29.73 | 25.16 | 25.02 | 21.58 |
| eft | 34.19 | 25.49 | 31.17 | 23.77 |
| legacy-v1 | 34.35 | 25.01 | 25.26 | 21.73 |
| legacy-tuned | 34.61 | 25.62 | 25.26 | 21.73 |
| kube-default | 35.16 | 25.57 | 26.38 | 22.29 |
| adaptive | 36.42 | 26.56 | 27.46 | 22.18 |
| adaptive-no-preempt | 36.59 | 27.44 | 27.46 | 22.18 |

makespan_seconds: Spearman -0.01, MAPE 17.8%, live best heft, heft-ra, twin best heft-ra-cap, legacy-tuned, legacy-v1.

mean_response_seconds: Spearman 0.12, MAPE 13.2%, live best heft, heft-ra, legacy-v1, twin best heft-ra-cap, legacy-tuned, legacy-v1.

### homogeneous / priority

| Policy | Makespan (s) | Mean response (s) | Twin makespan (s) | Twin mean response (s) |
|---|---:|---:|---:|---:|
| heft | 40.14 | 24.74 | 36.57 | 19.61 |
| kube-default | 40.24 | 23.88 | 36.57 | 19.53 |
| legacy-v1 | 40.46 | 24.32 | 36.57 | 19.44 |
| adaptive | 40.75 | 24.08 | 43.57 | 21.58 |
| heft-ra | 41.44 | 23.89 | 36.57 | 19.61 |
| legacy-tuned | 41.51 | 24.03 | 36.57 | 19.44 |
| heft-ra-cap | 41.88 | 24.02 | 36.57 | 19.49 |
| eft | 42.27 | 24.55 | 36.57 | 19.56 |
| adaptive-no-preempt | 43.26 | 25.01 | 36.57 | 19.61 |

makespan_seconds: Spearman -0.14, MAPE 11.1%, live best adaptive, heft, kube-default, legacy-v1, twin best adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1.

mean_response_seconds: Spearman 0.20, MAPE 18.6%, live best adaptive, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1, twin best adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1.

## Twin agreement (original twin)

| Profile | Batch | Metric | Spearman | MAPE | Mean bias | Live best | Twin best |
|---|---|---|---:|---:|---:|---|---|
| balanced | dag | makespan_seconds | 0.68 | 6.8% | -6.0% | heft-ra-cap | heft-ra, heft-ra-cap |
| balanced | dag | mean_response_seconds | 0.50 | 8.3% | -8.3% | heft-ra-cap | heft-ra, heft-ra-cap |
| balanced | priority | makespan_seconds | 0.22 | 12.6% | -12.6% | eft | heft, heft-ra, heft-ra-cap |
| balanced | priority | mean_response_seconds | 0.67 | 14.3% | -14.3% | eft | adaptive |
| edge | dag | makespan_seconds | 0.51 | 5.8% | -5.4% | heft-ra | kube-default |
| edge | dag | mean_response_seconds | 0.69 | 12.2% | -12.2% | adaptive, heft-ra-cap | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default |
| edge | priority | makespan_seconds | 0.59 | 19.6% | -15.5% | eft, heft, heft-ra, heft-ra-cap, legacy-tuned, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, legacy-tuned, legacy-v1 |
| edge | priority | mean_response_seconds | 0.85 | 22.9% | -22.9% | eft, legacy-tuned, legacy-v1 | adaptive-no-preempt, eft, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | dag | makespan_seconds | -0.01 | 17.8% | -17.8% | heft, heft-ra | heft-ra-cap, legacy-tuned, legacy-v1 |
| homogeneous | dag | mean_response_seconds | 0.12 | 13.2% | -13.2% | heft, heft-ra, legacy-v1 | heft-ra-cap, legacy-tuned, legacy-v1 |
| homogeneous | priority | makespan_seconds | -0.14 | 11.1% | -9.6% | adaptive, heft, kube-default, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | priority | mean_response_seconds | 0.20 | 18.6% | -18.6% | adaptive, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |

## Twin agreement (twin with measured slot-release latency)

| Profile | Batch | Metric | Spearman | MAPE | Mean bias | Live best | Twin best |
|---|---|---|---:|---:|---:|---|---|
| balanced | dag | makespan_seconds | 0.62 | 6.0% | -0.8% | heft-ra-cap | heft-ra, heft-ra-cap |
| balanced | dag | mean_response_seconds | 0.50 | 5.3% | +2.9% | heft-ra-cap | heft-ra, heft-ra-cap |
| balanced | priority | makespan_seconds | 0.30 | 3.1% | +0.0% | eft | heft, heft-ra, heft-ra-cap |
| balanced | priority | mean_response_seconds | 0.60 | 5.2% | -2.5% | eft | adaptive, kube-default |
| edge | dag | makespan_seconds | 0.50 | 4.4% | +0.0% | heft-ra | kube-default |
| edge | dag | mean_response_seconds | 0.80 | 7.0% | -7.0% | adaptive, heft-ra-cap | adaptive, adaptive-no-preempt, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| edge | priority | makespan_seconds | 0.47 | 6.4% | -0.3% | eft, heft, heft-ra, heft-ra-cap, legacy-tuned, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| edge | priority | mean_response_seconds | 0.66 | 8.3% | -8.3% | eft, legacy-tuned, legacy-v1 | adaptive |
| homogeneous | dag | makespan_seconds | 0.15 | 12.4% | -12.4% | heft, heft-ra | heft-ra-cap |
| homogeneous | dag | mean_response_seconds | 0.09 | 6.7% | -6.7% | heft, heft-ra, legacy-v1 | heft-ra-cap |
| homogeneous | priority | makespan_seconds | -0.19 | 6.2% | -3.2% | adaptive, heft, kube-default, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |
| homogeneous | priority | mean_response_seconds | 0.17 | 5.8% | -5.8% | adaptive, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 | adaptive-no-preempt, eft, heft, heft-ra, heft-ra-cap, kube-default, legacy-tuned, legacy-v1 |

## Paired contrasts (ratio policy / baseline, matched trials)

| Batch | Policy vs baseline | Metric | Pairs | Mean ratio | 95% bootstrap CI | Wins / losses |
|---|---|---|---:|---:|---|---:|
| dag | heft vs kube-default | makespan_seconds | 9 | 0.930 | 0.857-1.006 | 7 / 1 |
| dag | heft vs kube-default | mean_response_seconds | 9 | 1.035 | 0.957-1.135 | 4 / 4 |
| dag | heft vs kube-default | p95_response_seconds | 9 | 0.930 | 0.857-1.005 | 7 / 1 |
| dag | heft-ra vs heft | makespan_seconds | 9 | 0.917 | 0.837-0.998 | 8 / 1 |
| dag | heft-ra vs heft | mean_response_seconds | 9 | 0.931 | 0.846-1.013 | 6 / 2 |
| dag | heft-ra vs heft | p95_response_seconds | 9 | 0.917 | 0.837-0.998 | 8 / 1 |
| dag | heft-ra-cap vs heft | makespan_seconds | 9 | 0.919 | 0.816-1.021 | 6 / 2 |
| dag | heft-ra-cap vs heft | mean_response_seconds | 9 | 0.924 | 0.840-1.000 | 6 / 2 |
| dag | heft-ra-cap vs heft | p95_response_seconds | 9 | 0.919 | 0.816-1.021 | 6 / 2 |
| dag | heft-ra-cap vs heft-ra | makespan_seconds | 9 | 1.002 | 0.930-1.074 | 3 / 5 |
| dag | heft-ra-cap vs heft-ra | mean_response_seconds | 9 | 0.995 | 0.957-1.028 | 4 / 5 |
| dag | heft-ra-cap vs heft-ra | p95_response_seconds | 9 | 1.002 | 0.929-1.074 | 3 / 5 |
| dag | heft vs eft | makespan_seconds | 9 | 0.917 | 0.824-1.031 | 8 / 1 |
| dag | heft vs eft | mean_response_seconds | 9 | 1.022 | 0.933-1.140 | 4 / 3 |
| dag | heft vs eft | p95_response_seconds | 9 | 0.917 | 0.825-1.030 | 8 / 1 |
| dag | legacy-tuned vs legacy-v1 | makespan_seconds | 9 | 1.043 | 0.997-1.092 | 3 / 6 |
| dag | legacy-tuned vs legacy-v1 | mean_response_seconds | 9 | 1.053 | 1.021-1.086 | 0 / 7 |
| dag | legacy-tuned vs legacy-v1 | p95_response_seconds | 9 | 1.043 | 0.997-1.091 | 3 / 6 |
| dag | adaptive vs heft | makespan_seconds | 9 | 1.038 | 0.936-1.149 | 4 / 5 |
| dag | adaptive vs heft | mean_response_seconds | 9 | 0.960 | 0.879-1.040 | 6 / 3 |
| dag | adaptive vs heft | p95_response_seconds | 9 | 1.038 | 0.936-1.149 | 4 / 5 |
| priority | adaptive vs adaptive-no-preempt | makespan_seconds | 9 | 1.139 | 0.997-1.285 | 4 / 5 |
| priority | adaptive vs adaptive-no-preempt | mean_response_seconds | 9 | 1.007 | 0.948-1.067 | 4 / 4 |
| priority | adaptive vs adaptive-no-preempt | p95_response_seconds | 9 | 1.258 | 1.054-1.458 | 3 / 6 |
| priority | adaptive vs adaptive-no-preempt | critical_response_seconds | 9 | 0.878 | 0.754-0.969 | 8 / 0 |
| priority | heft vs kube-default | makespan_seconds | 9 | 1.006 | 0.969-1.051 | 5 / 2 |
| priority | heft vs kube-default | mean_response_seconds | 9 | 1.101 | 1.044-1.175 | 1 / 8 |
| priority | heft vs kube-default | p95_response_seconds | 9 | 1.006 | 0.965-1.058 | 5 / 2 |
| priority | heft vs kube-default | critical_response_seconds | 9 | 1.476 | 1.245-1.746 | 0 / 9 |
| priority | heft-ra vs heft | makespan_seconds | 9 | 0.997 | 0.951-1.033 | 2 / 5 |
| priority | heft-ra vs heft | mean_response_seconds | 9 | 0.975 | 0.938-1.014 | 6 / 1 |
| priority | heft-ra vs heft | p95_response_seconds | 9 | 0.997 | 0.945-1.038 | 3 / 5 |
| priority | heft-ra vs heft | critical_response_seconds | 9 | 0.999 | 0.930-1.088 | 4 / 3 |
| priority | heft-ra-cap vs heft | makespan_seconds | 9 | 1.016 | 0.969-1.068 | 3 / 4 |
| priority | heft-ra-cap vs heft | mean_response_seconds | 9 | 0.999 | 0.946-1.060 | 4 / 1 |
| priority | heft-ra-cap vs heft | p95_response_seconds | 9 | 1.020 | 0.966-1.079 | 3 / 4 |
| priority | heft-ra-cap vs heft | critical_response_seconds | 9 | 0.986 | 0.925-1.054 | 4 / 3 |
| priority | heft-ra-cap vs heft-ra | makespan_seconds | 9 | 1.019 | 0.996-1.043 | 1 / 6 |
| priority | heft-ra-cap vs heft-ra | mean_response_seconds | 9 | 1.024 | 1.001-1.046 | 2 / 7 |
| priority | heft-ra-cap vs heft-ra | p95_response_seconds | 9 | 1.023 | 0.996-1.050 | 1 / 6 |
| priority | heft-ra-cap vs heft-ra | critical_response_seconds | 9 | 0.992 | 0.951-1.040 | 5 / 4 |
| priority | legacy-tuned vs legacy-v1 | makespan_seconds | 9 | 1.011 | 0.977-1.043 | 2 / 5 |
| priority | legacy-tuned vs legacy-v1 | mean_response_seconds | 9 | 1.002 | 0.965-1.041 | 4 / 3 |
| priority | legacy-tuned vs legacy-v1 | p95_response_seconds | 9 | 1.014 | 0.974-1.051 | 2 / 6 |
| priority | legacy-tuned vs legacy-v1 | critical_response_seconds | 9 | 0.992 | 0.938-1.037 | 4 / 3 |
