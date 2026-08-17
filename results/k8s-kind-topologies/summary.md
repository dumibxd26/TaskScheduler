# Multi-Topology Kind Summary

Single-PC Kind integration and descriptive performance evidence; not independent physical-node validation.

## Per Topology

| Topology | Policy | Completion | Median workflow (s) | P95 workflow (s) | Critical (s) | Makespan (s) | Preemptions | Restarts |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| homogeneous | adaptive | 100.00% | 34.203 | 67.047 | 27.454 | 65.701 | 15 | 0 |
| homogeneous | legacy-tuned | 100.00% | 47.527 | 54.558 | 28.187 | 57.083 | 0 | 0 |
| homogeneous | legacy-v1 | 100.00% | 47.527 | 53.517 | 28.690 | 59.189 | 0 | 0 |
| skewed | adaptive | 100.00% | 33.978 | 67.579 | 32.115 | 65.734 | 15 | 0 |
| skewed | legacy-tuned | 100.00% | 42.992 | 51.401 | 23.558 | 57.382 | 0 | 0 |
| skewed | legacy-v1 | 100.00% | 47.443 | 60.388 | 32.254 | 61.560 | 0 | 0 |
| small | adaptive | 100.00% | 44.372 | 90.339 | 29.243 | 88.361 | 14 | 0 |
| small | legacy-tuned | 100.00% | 50.427 | 71.121 | 31.558 | 73.933 | 0 | 0 |
| small | legacy-v1 | 100.00% | 52.863 | 73.859 | 30.618 | 77.948 | 0 | 0 |

## Per-Topology Workflow Shapes

| Topology | Policy | Shape | Median response (s) |
|---|---|---|---:|
| homogeneous | adaptive | critical | 27.454 |
| homogeneous | adaptive | fanout | 46.782 |
| homogeneous | adaptive | parallel | 21.946 |
| homogeneous | adaptive | pipeline | 34.203 |
| homogeneous | adaptive | pressure | 65.701 |
| homogeneous | legacy-tuned | critical | 28.187 |
| homogeneous | legacy-tuned | fanout | 48.040 |
| homogeneous | legacy-tuned | parallel | 44.412 |
| homogeneous | legacy-tuned | pipeline | 50.083 |
| homogeneous | legacy-tuned | pressure | 47.527 |
| homogeneous | legacy-v1 | critical | 28.690 |
| homogeneous | legacy-v1 | fanout | 50.166 |
| homogeneous | legacy-v1 | parallel | 41.984 |
| homogeneous | legacy-v1 | pipeline | 52.189 |
| homogeneous | legacy-v1 | pressure | 47.527 |
| skewed | adaptive | critical | 32.115 |
| skewed | adaptive | fanout | 33.978 |
| skewed | adaptive | parallel | 29.871 |
| skewed | adaptive | pipeline | 38.173 |
| skewed | adaptive | pressure | 65.734 |
| skewed | legacy-tuned | critical | 23.558 |
| skewed | legacy-tuned | fanout | 49.361 |
| skewed | legacy-tuned | parallel | 42.253 |
| skewed | legacy-tuned | pipeline | 50.374 |
| skewed | legacy-tuned | pressure | 37.946 |
| skewed | legacy-v1 | critical | 32.254 |
| skewed | legacy-v1 | fanout | 50.516 |
| skewed | legacy-v1 | parallel | 38.217 |
| skewed | legacy-v1 | pipeline | 52.693 |
| skewed | legacy-v1 | pressure | 47.443 |
| small | adaptive | critical | 29.243 |
| small | adaptive | fanout | 44.372 |
| small | adaptive | parallel | 43.652 |
| small | adaptive | pipeline | 47.713 |
| small | adaptive | pressure | 88.361 |
| small | legacy-tuned | critical | 31.558 |
| small | legacy-tuned | fanout | 60.794 |
| small | legacy-tuned | parallel | 46.344 |
| small | legacy-tuned | pipeline | 66.933 |
| small | legacy-tuned | pressure | 69.812 |
| small | legacy-v1 | critical | 30.618 |
| small | legacy-v1 | fanout | 61.583 |
| small | legacy-v1 | parallel | 41.121 |
| small | legacy-v1 | pipeline | 70.948 |
| small | legacy-v1 | pressure | 62.418 |

## Aggregate

| Policy | Trials | Completion | Median workflow (s) | P95 workflow (s) | Critical (s) | Makespan (s) | Preemptions | Restarts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| adaptive | 9 | 100.00% | 38.307 | 70.993 | 28.245 | 67.047 | 44 | 0 |
| legacy-tuned | 9 | 100.00% | 47.527 | 69.812 | 27.821 | 58.401 | 0 | 0 |
| legacy-v1 | 9 | 100.00% | 47.665 | 70.948 | 29.431 | 60.517 | 0 | 0 |

## Aggregate Workflow Shapes

| Policy | Shape | Median response (s) |
|---|---|---:|
| adaptive | critical | 28.245 |
| adaptive | fanout | 43.634 |
| adaptive | parallel | 29.871 |
| adaptive | pipeline | 38.173 |
| adaptive | pressure | 67.047 |
| legacy-tuned | critical | 27.821 |
| legacy-tuned | fanout | 50.382 |
| legacy-tuned | parallel | 44.240 |
| legacy-tuned | pipeline | 50.419 |
| legacy-tuned | pressure | 47.527 |
| legacy-v1 | critical | 29.431 |
| legacy-v1 | fanout | 50.828 |
| legacy-v1 | parallel | 41.121 |
| legacy-v1 | pipeline | 52.863 |
| legacy-v1 | pressure | 50.994 |

## Profile-Learning Sequence

Profiles are reused only within a policy and topology. Sequential values are descriptive and also include host/order effects.

| Scope | Policy | Repetition | Trials | Makespan (s) | Mean response (s) |
|---|---|---:|---:|---:|---:|
| homogeneous | adaptive | 1 | 1 | 67.047 | 39.457 |
| homogeneous | adaptive | 2 | 1 | 63.947 | 39.530 |
| homogeneous | adaptive | 3 | 1 | 65.701 | 34.767 |
| homogeneous | legacy-tuned | 1 | 1 | 57.083 | 45.744 |
| homogeneous | legacy-tuned | 2 | 1 | 55.700 | 41.920 |
| homogeneous | legacy-tuned | 3 | 1 | 61.558 | 45.283 |
| homogeneous | legacy-v1 | 1 | 1 | 59.189 | 44.953 |
| homogeneous | legacy-v1 | 2 | 1 | 55.665 | 41.712 |
| homogeneous | legacy-v1 | 3 | 1 | 60.517 | 45.049 |
| skewed | adaptive | 1 | 1 | 67.579 | 36.599 |
| skewed | adaptive | 2 | 1 | 64.351 | 36.146 |
| skewed | adaptive | 3 | 1 | 65.734 | 46.633 |
| skewed | legacy-tuned | 1 | 1 | 58.401 | 40.931 |
| skewed | legacy-tuned | 2 | 1 | 57.292 | 39.394 |
| skewed | legacy-tuned | 3 | 1 | 57.382 | 41.912 |
| skewed | legacy-v1 | 1 | 1 | 61.560 | 42.392 |
| skewed | legacy-v1 | 2 | 1 | 64.453 | 48.645 |
| skewed | legacy-v1 | 3 | 1 | 59.693 | 43.484 |
| small | adaptive | 1 | 1 | 90.339 | 48.214 |
| small | adaptive | 2 | 1 | 88.361 | 58.960 |
| small | adaptive | 3 | 1 | 70.993 | 47.047 |
| small | legacy-tuned | 1 | 1 | 73.933 | 54.667 |
| small | legacy-tuned | 2 | 1 | 78.121 | 57.250 |
| small | legacy-tuned | 3 | 1 | 59.440 | 46.890 |
| small | legacy-v1 | 1 | 1 | 77.948 | 59.027 |
| small | legacy-v1 | 2 | 1 | 78.788 | 53.506 |
| small | legacy-v1 | 3 | 1 | 59.863 | 44.817 |
| all-topology median | adaptive | 1 | 3 | 67.579 | 39.457 |
| all-topology median | adaptive | 2 | 3 | 64.351 | 39.530 |
| all-topology median | adaptive | 3 | 3 | 65.734 | 46.633 |
| all-topology median | legacy-tuned | 1 | 3 | 58.401 | 45.744 |
| all-topology median | legacy-tuned | 2 | 3 | 57.292 | 41.920 |
| all-topology median | legacy-tuned | 3 | 3 | 59.440 | 45.283 |
| all-topology median | legacy-v1 | 1 | 3 | 61.560 | 44.953 |
| all-topology median | legacy-v1 | 2 | 3 | 64.453 | 48.645 |
| all-topology median | legacy-v1 | 3 | 3 | 59.863 | 44.817 |

## Timing Comparisons

Negative values favor the policy left of `vs`; positive values favor the policy on the right.

| Scope | Comparison | Median workflow | P95 workflow | Critical | Makespan |
|---|---|---:|---:|---:|---:|
| homogeneous | tuned vs v1 | +0.00% | +1.94% | -1.75% | -3.56% |
| homogeneous | Adaptive vs tuned | -28.04% | +22.89% | -2.60% | +15.10% |
| skewed | tuned vs v1 | -9.38% | -14.88% | -26.96% | -6.79% |
| skewed | Adaptive vs tuned | -20.97% | +31.47% | +36.32% | +14.56% |
| small | tuned vs v1 | -4.61% | -3.71% | +3.07% | -5.15% |
| small | Adaptive vs tuned | -12.01% | +27.02% | -7.33% | +19.51% |
| aggregate | tuned vs v1 | -0.29% | -1.60% | -5.47% | -3.50% |
| aggregate | Adaptive vs tuned | -19.40% | +1.69% | +1.52% | +14.80% |

## Tuned Legacy Direction Check

- `median_workflow_response_s`: tuned Legacy is lower in 2/3 topologies.
- `p95_workflow_response_s`: tuned Legacy is lower in 2/3 topologies.
- `median_critical_response_s`: tuned Legacy is lower in 2/3 topologies.
- `median_trial_makespan_s`: tuned Legacy is lower in 3/3 topologies.

