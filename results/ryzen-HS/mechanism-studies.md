# Mechanism Studies

Seeds 400-449 with a +/-10% uncertainty floor; ratios are paired on identical cluster instances (below 1 favours the left policy).

## Cold starts (closed workflows)

| Variant | Runs | Mean makespan (s) | Mean time-to-running (s) | P95 time-to-running (s) | Mean cold starts |
|---|---:|---:|---:|---:|---:|
| heft-ra | 750 | 20.190 | 1.131 | 11.755 | 3.79 |
| heft-ra-image-blind | 750 | 20.423 | 1.174 | 13.821 | 3.79 |
| heft-ra-prewarmed | 750 | 19.540 | 0.816 | 11.755 | 0.00 |
| k8s-default | 750 | 31.874 | 2.963 | 66.101 | 3.89 |

| Comparison | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| heft-ra vs heft-ra-image-blind | makespan_s | 750 | 0.9879 | [0.9821, 0.9932] | 45 / 687 / 18 |
| heft-ra vs heft-ra-image-blind | mean_time_to_running_s | 750 | 0.9846 | [0.9753, 0.9927] | 43 / 684 / 23 |
| heft-ra-prewarmed vs heft-ra | makespan_s | 750 | 0.9223 | [0.9156, 0.9290] | 591 / 159 / 0 |
| heft-ra-prewarmed vs heft-ra | mean_time_to_running_s | 750 | 0.5103 | [0.4885, 0.5315] | 600 / 150 / 0 |
| heft-ra vs k8s-default | makespan_s | 750 | 0.7798 | [0.7577, 0.8000] | 434 / 298 / 18 |
| heft-ra vs k8s-default | mean_time_to_running_s | 750 | 0.7351 | [0.7091, 0.7601] | 396 / 295 / 59 |

| Cluster family | Pre-warmed / cold makespan | Image-aware / image-blind makespan |
|---|---:|---:|
| asymmetric-network | 0.9403 | 0.9982 |
| balanced-heterogeneous | 0.8985 | 1.0003 |
| homogeneous | 0.8926 | 1.0003 |
| local-calibration | 1.0000 | 1.0000 |
| unreliable-thermal | 0.8803 | 0.9409 |

## Priorities and preemption (workloads with an urgent CRITICAL workflow)

| Policy | Runs | Urgent response (s) | Other response (s) | Urgent deadline hit | Mean makespan (s) | Preemptions |
|---|---:|---:|---:|---:|---:|---:|
| adaptive | 750 | 10.932 | 56.934 | 67.60% | 70.815 | 0.34 |
| adaptive-no-preempt | 750 | 15.511 | 56.855 | 67.60% | 70.663 | 0.00 |
| heft-ra | 750 | 20.935 | 55.200 | 69.47% | 70.679 | 0.00 |
| heft | 750 | 21.329 | 57.361 | 66.13% | 71.508 | 0.00 |
| eft | 750 | 21.757 | 56.874 | 66.13% | 71.469 | 0.00 |
| k8s-default | 750 | 42.250 | 76.971 | 35.20% | 161.426 | 0.00 |

| Comparison | Metric | Cases | Mean ratio | 95% CI | Wins / ties / losses |
|---|---|---:|---:|---|---:|
| adaptive vs adaptive-no-preempt | urgent_response_s | 750 | 0.9379 | [0.9236, 0.9512] | 99 / 650 / 1 |
| adaptive vs adaptive-no-preempt | other_response_s | 750 | 1.0006 | [1.0000, 1.0012] | 23 / 650 / 77 |
| adaptive vs heft-ra | urgent_response_s | 750 | 0.9808 | [0.9566, 1.0047] | 162 / 212 / 376 |
| adaptive vs heft-ra | other_response_s | 750 | 1.0781 | [1.0519, 1.1097] | 265 / 286 / 199 |
| heft-ra vs k8s-default | urgent_response_s | 750 | 0.8950 | [0.8515, 0.9407] | 391 / 58 / 301 |
| heft-ra vs k8s-default | other_response_s | 750 | 0.7682 | [0.7442, 0.7925] | 550 / 75 / 125 |

### mixed-arrivals

| Policy | Urgent response (s) | Other response (s) |
|---|---:|---:|
| adaptive | 10.063 | 33.439 |
| adaptive-no-preempt | 11.022 | 33.427 |
| heft-ra | 16.816 | 33.601 |
| heft | 17.243 | 37.474 |
| eft | 18.530 | 36.011 |
| k8s-default | 18.813 | 85.360 |

### priority-burst

| Policy | Urgent response (s) | Other response (s) |
|---|---:|---:|
| adaptive | 12.523 | 58.590 |
| adaptive-no-preempt | 25.302 | 58.365 |
| heft-ra | 36.504 | 52.562 |
| heft | 37.015 | 55.781 |
| eft | 37.015 | 55.781 |
| k8s-default | 58.140 | 60.931 |

### recovery-gang-mix

| Policy | Urgent response (s) | Other response (s) |
|---|---:|---:|
| adaptive | 10.209 | 78.773 |
| adaptive-no-preempt | 10.209 | 78.773 |
| heft-ra | 9.486 | 79.436 |
| heft | 9.728 | 78.830 |
| eft | 9.728 | 78.830 |
| k8s-default | 49.797 | 84.623 |

