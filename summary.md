# TaskScheduler Status Summary

TaskScheduler studies how to place the tasks of DAG workflows on
heterogeneous clusters. It has two execution surfaces over one set of
scenario files:

- a deterministic discrete-event simulator (`simulation/`) that models SMT
  CPUs, caches and DRAM, storage, routed networks with max-min sharing and
  TCP limits, heating and throttling, energy, failures, checkpoints, gangs,
  priorities and preemption;
- a Kubernetes control plane (Workflow and TaskTemplate CRDs, DAG
  controller, custom scheduler, producer-local data plane, telemetry), run
  on same-host Kind clusters that emulate heterogeneous nodes, plus a
  calibrated simulator twin of each emulated cluster.

## What was done in the correction round (2026-10-03/04)

- An audit found 53 problems in the earlier version; all were resolved,
  almost all with code fixes and regression tests (210 tests pass).
- HEFT, the standard baseline, was found to be contention-blind (40.4 s vs
  6.6 s for FCFS on a fan-out). HEFT-RA fixes its estimates and adds a
  lookahead; `heft-ra-cap` makes the lookahead capacity-aware.
- Seeds were found to repeat the same experiment (1.4-4.3 distinct outcomes
  out of 20); later phases perturb every seed by up to +/-10%.
- The live emulation was rebuilt (slots, speed factors with CPU-time
  budgets, `tc netem` links); a scheduler refresh race that froze nodes for
  30 s was found by the live runs, fixed, and the whole live matrix rerun.

## Evidence

| Surface | Scale | Result |
|---|---|---|
| Closed DAGs, fresh holdout | 3,750 cases | HEFT-RA 0.37% mean / 0.00% P95 regret; HEFT 12.42% / 49.02%; k8s-default 160.69% |
| Overlapping workloads, fresh holdout | 1,000 cases | `heft-ra-cap` best median (0.00%) and P95 (63.76%) response regret, deadline hit 71.47% |
| Cold-start study | 750 runs per variant | Image awareness -1.2% makespan; pre-pulling would give -7.8% |
| Priority study | 750 runs per policy | Urgent response: HEFT-RA median 7.6 s vs Adaptive 9.7 s, but P95 126.6 s vs 21.0 s; preemption costs others 0.06% |
| Live Kind matrix | 3 profiles, 9 policies, 162 trials | A HEFT-RA variant fastest on DAGs in 8 of 9 runs (0.917 of HEFT); Adaptive 12% faster critical response; HEFT family 1.48x slower than kube-default for urgent work; refresh fix removed 30+ s stalls |
| Twin | 6 profile/batch pairs | 3-8% mean error on heterogeneous profiles once the measured slot-release latency is modelled |

## Recommendation

Use HEFT-RA (`heft-ra-cap`) for DAG workflows on heterogeneous clusters,
especially with data movement or uneven networks; it also serves urgent
work well in the typical case. Use Adaptive when the worst-case latency of
urgent workflows arriving into a busy cluster matters (without preemption
if restarts are costly);
prefer risk-averse placement where the fastest nodes are the least
reliable; add image pre-pulling where start-up latency matters; do not use
the stock Kubernetes scoring to place DAGs.

Simulated regrets are best-case figures: every simulated scheduler knows
the exact cluster parameters, and HEFT-RA's estimator uses the simulator's
own models. The live trials, with learned estimates, are the real test,
and their 9 trials per policy give wide intervals. Physical multi-machine
validation has not been run. Details:
`thesis/thesis.pdf`, `ENGINEERING_JOURNAL.md`, `results/`.
