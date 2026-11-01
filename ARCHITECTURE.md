# TaskScheduler Architecture

TaskScheduler has two execution surfaces over one set of scenario
descriptions: a deterministic discrete-event simulator (`simulation/`) and a
Kubernetes control plane (`controlplane/`, `services/`, `models/`,
`engine.py`). The policies exist in both: the simulator implementations work
on modelled cluster state, the live implementations on Kubernetes
observations, learned profiles and measured bandwidths. A calibrated
simulator twin (`simulation/kind_twin.py`) models each emulated Kind cluster so
that simulated predictions can be compared with live trials.

> **Validation boundary.** Live experiments run on one Windows/Docker host.
> The `balanced`, `homogeneous` and `edge` profiles emulate heterogeneity with
> task slots, per-node CPU/memory/I/O speed factors, Docker CPU and memory
> caps, and `tc netem` delay and rate on each worker's egress. They are not
> independent machines, links, thermal domains or failure domains. The
> two-host k3s procedure below has not been used for the retained evidence.

## Components

```
 kubectl apply -f workflow.yaml (Workflow CRD, TaskTemplate CRDs)
                 |
     +-----------v------------+
     |  ts-controller (1x)    |  controlplane/controller/main.py
     |  - ready tasks -> Pods |  - schedulerName: ts-scheduler
     |  - DAG, priority, gang,|  - hashed DNS-1123 Pod names
     |    input provenance as |  - retries, failure propagation
     |    Pod annotations     |  - per-task timestamps, workflow status
     +-----------+------------+
                 | Pending Pods
     +-----------v------------+
     |  ts-scheduler (1x)     |  controlplane/scheduler/main.py
     |  - upward ranks from   |  services/policy.py (policies)
     |    the published DAG   |  engine.py (tick: admission, readiness,
     |  - policy every tick   |    preemption, placement, binding)
     |  - bind with rollback  |  services/k8s_cluster.py (inventory merge)
     |  - victim deletion     |  models/profile_store.py (learned profiles)
     +-----+------------+-----+
           |            |
  +--------v---+  +-----v--------+  +--------------+
  | ts-        |  | ts-bw-probe  |  | ts-thermal   |   DaemonSets
  | fileserver |  | bw-from-<peer|  | cpu-temp-c   |
  | :8080      |  | annotations  |  | (real sensors|
  | /var/lib/  |  | (staggered   |  |  only unless |
  | ts-data    |  |  60 s slots) |  |  fallback on)|
  +------------+  +--------------+  +--------------+
```

### Controller

Watches Workflows and Pods. A task's Pod is created only when all its parents
have succeeded. Pod annotations (`ts.scheduler/...`) carry the task template,
class, compatible node types, logical CPU and memory requests, priority,
workflow class, preemptability, the compact workflow DAG (tasks, templates,
typed edges with expected bytes), gang group and gang size, checkpointability,
and the producer node and byte count of every DATA input. Environment
variables give the task its identity, input and output directories, its node
name (downward API) and the emulated speed table (`ts-emulation` ConfigMap,
optional). Task output (`__TS_OUTPUT__`) is parsed strictly; malformed output
fails the task instead of wedging it. The workflow status records
`admittedAt`, per-task `readyAt`, `podCreatedAt`, `startedAt`, `finishedAt`,
`attempts`, and the final state.

### Scheduler

Watches Pending Pods that name it. Each tick (default 1 s) the engine admits
queued workflows (priority, then arrival), resolves readiness, asks the
policy for placements, holds and preemptions, and binds. A bind returns OK,
RETRY or DROP; anything but OK rolls the reservation back. A preempted task
enters `PREEMPTED` and is re-queued only when the controller has created its
replacement Pod. Cluster inventory is polled outside the scheduling lock and
merged with the scheduler's logical reservations, so a refresh cannot erase
decisions the API has not yet reflected. Pods whose completion or
preemption the scheduler has already processed are remembered for ten
minutes and never charged as untracked, because a Pod that finished
between the poll and the merge still looks running in the polled
inventory (this race once froze nodes for 30 s). One re-entrant lock
protects all shared state. Completions are logged as `[DONE] ... at=<epoch>`
and binds as `[BIND] ... at=<epoch>`; the harness parses both, together
with any scheduler-wide error lines. Runtimes are learned from the task's own metrics (runtime minus
input staging time), not from kubelet timestamps.

### Policies (live)

`kube-default`, `legacy-v1`, `legacy-tuned`, `eft`, `heft`, `heft-ra`,
`heft-ra-cap`, `adaptive`, `adaptive-no-preempt`, `calibrate` (`legacy` =
`legacy-v1`). No
policy overcommits a node. Gangs are placed all-or-nothing. Live HEFT-RA
prices each remote input by the producer's measured egress shared with pulls
planned in the same tick and pulls started recently, and applies a one-level
lookahead over the task's DAG children; `heft-ra-cap` assigns those
children only to capacity that is free in the current tick (a child's
template is looked up in any workflow that used it, otherwise the
producer's size is assumed).

### Logical and physical task resources

`cpuRequest` and `memoryRequest` in a TaskTemplate are the scheduler's
logical demand. Optional `physicalResources` sets the Pod's real requests and
limits. Optional `emulation.workScale` and `timeScale` scale executed work.
The validation templates (`experiments/k8s/tasktemplates.yaml`) use real
sizes and no scaling: CPU and memory work is a CPU-time budget
(`TS_CPU_SECONDS`, `TS_MEMORY_CPU_SECONDS`) divided by the node's emulated
speed, and I/O work writes real bytes at a paced rate times the node's I/O
speed.

## Same-host emulation (Kind)

`kind_lab.py create <profile>` builds `ts-cluster` from
`clusters/kind_profiles.yaml`:

| Step | Effect |
|---|---|
| Kind config | One control-plane node and one worker per profile entry, with node-type, capacity and profile labels |
| `docker update` | `cpus = 0.5 x slots + 0.35`, `memory = 224 x slots + 576` MiB, `cpu-shares = 1024 x slots` per worker container |
| `tc qdisc replace ... netem delay <d> rate <r>` | Worker egress delay and rate, verified by reading the qdisc back |
| ConfigMap `ts-emulation` | Per-node CPU, memory, I/O speed factors |
| Node annotations | `ts.io/netem-delay-ms`, `ts.io/netem-rate-mbit`, `ts.io/emulated-speed`, `ts.io/kind-profile` |
| `--record <file>` | JSON evidence of everything applied |

`kind_lab.py reset-network` removes the qdiscs; `render-config` prints the
Kind config.

## Install - single host (Kind)

```sh
make cluster PROFILE=balanced   # or small / big (demo), homogeneous / edge
make images load install        # build, kind-load, kubectl apply -k .
make examples                   # sample TaskTemplates + linear-pipeline-1
kubectl get workflows -w
```

The validation matrix is `scripts/run-kind-validation.ps1` or
`make kind-validation` (see README).

## Planned two-host k3s LAN deployment

This section is the intended procedure for future physical cross-machine
network, thermal, and bandwidth validation. Scripts and Make targets exist,
but the retained evaluation did not execute this topology. Commands and
expected observations below must be revalidated on the target machines before
being cited as results.

### Topology

| Role   | Host                              | OS               | Logical k8s nodes                                              |
|--------|-----------------------------------|------------------|----------------------------------------------------------------|
| **Master** | **pc1** — Mac mini M2 / 8 GB      | macOS + Multipass VM | `ts-master` (control plane + scheduler + registry, MEM_OPT, PASSIVE, arm64) |
| Worker | **pc2** — Ryzen 7 PRO 8840HS / 64 GB | Windows + Multipass VMs | `ts-node-cpu-1` (CPU_OPT, HIGH) + `ts-node-cpu-2` (CPU_OPT) + `ts-node-io-1` (IO_OPT) + `ts-node-mem-1` (MEM_OPT) — all amd64 |

Five logical nodes, two physical machines, two CPU architectures
(arm64 master + amd64 workers), one apiserver. The `ts-scheduler` lives
on **pc1** inside the `ts-master` VM. Because neither macOS nor Windows
runs k3s natively, every k3s process — server and agents — runs inside
a Multipass Linux VM. The cross-host LAN hop between the Mac mini's
master VM and the laptop's worker VMs is what gives `ts-bw-probe` real
measurements.

### Prerequisites

- **pc1 (Mac mini, macOS)**: `multipass` (`brew install --cask multipass`)
  and `kubectl` (`brew install kubectl`). Multipass must be configured
  with a bridged network so the master VM gets a LAN IP reachable from
  pc2:

  ```sh
  multipass networks                              # list available host interfaces
  multipass set local.bridged-network=en1         # use your active wifi/ethernet iface
  ```

- **pc2 (Windows laptop)**: Multipass for Windows (installer from
  multipass.run), Hyper-V enabled (built into Windows Pro), and either
  **Git Bash** (comes with Git for Windows) or **WSL2** so you can run
  the shell scripts. From either shell, the `multipass` command must
  be on `PATH`.

- LAN firewall: ports `6443/tcp` (apiserver), `5000/tcp` (registry),
  `8472/udp` (flannel VXLAN), `10250/tcp` (kubelet), `8080/tcp`
  (fileserver) open between the two machines.

### Step 1 — Bring up the master (pc1, Mac mini)

```sh
cp cluster.env.example cluster.env
# Edit only PC1_HOST_IP and PC2_HOST_IP for documentation; leave
# SERVER_IP untouched — the script overwrites it with the master VM's
# bridged LAN IP after launch.

make k3s-server
# - launches Multipass VM ts-master (bridged, 3 CPU, 4 GB)
# - installs k3s server inside it
# - starts insecure Docker registry on :5000 inside it
# - writes SERVER_IP back into cluster.env
# - copies kubeconfig to ~/.kube/config (rewritten to use SERVER_IP)
# - labels ts-master with node-type=MEM_OPT, cooling=PASSIVE

kubectl get nodes -L node-type -L ts.cooling-class
# NAME        STATUS   ROLES                  NODE-TYPE   TS.COOLING-CLASS
# ts-master   Ready    control-plane,master   MEM_OPT     PASSIVE
```

### Step 2 — Join the workers (pc2, Windows laptop)

Open **Git Bash** (or a WSL2 shell), then:

```sh
# 1. Get the updated cluster.env onto pc2 — easiest path:
scp <mac-user>@<PC1_HOST_IP>:/Volumes/HDD/projects/TaskScheduler/cluster.env .

# 2. Spawn all 4 worker VMs and join them to the master:
./scripts/k3s-agent.sh
# - launches ts-node-cpu-1, ts-node-cpu-2, ts-node-io-1, ts-node-mem-1
# - installs k3s agent in each
# - prints kubectl label commands for you to paste on pc1
```

Back on **pc1** (Mac mini), confirm all five nodes are Ready and apply
the label commands the agent script printed:

```sh
kubectl get nodes
# ts-master, ts-node-cpu-1, ts-node-cpu-2, ts-node-io-1, ts-node-mem-1
# all Ready.

# Paste the 4 kubectl label commands that pc2's script printed
# (one per worker node).
```

### Step 3 — Build and push multi-arch images (pc1)

The master is arm64 (Mac mini) and the workers are amd64 (Windows
laptop), so images **must** be multi-arch:

```sh
# One-time buildx setup:
docker buildx create --use --name ts-builder
docker buildx inspect --bootstrap

# Build + push all 7 images for amd64 AND arm64:
make images-multiarch
```

### Step 4 — Install the platform (pc1)

```sh
make install-remote
# Rewrites image refs to ${REGISTRY}/ts-*:v1 on the fly,
# applies manifests, waits for rollouts.
```

### Step 5 — Run a workflow

```sh
kubectl apply -f examples/tasktemplates.yaml
kubectl apply -f examples/linear-3task.yaml
kubectl get workflows -w
```

### Proposed physical-validation checks

Quick automated check (recommended):

```sh
make verify-cluster       # nodes, labels, control-plane, DaemonSets, bw + thermal annotations
make verify-cluster-run   # everything above + submits linear-pipeline-1 and watches it finish
```

Or inspect by hand:

```sh
# Cross-machine pod placement:
kubectl get pods -l ts.io/workflow=linear-pipeline-1 -o wide
# Tasks should be distributed across ts-master (pc1) and the
# ts-node-* workers (pc2).

# Cross-node bandwidth signal (from ts-bw-probe):
kubectl get nodes -o yaml | grep 'ts.io/bw-from-'

# Thermal signal (real only when exposed by the host/kernel):
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.annotations.ts\.io/cpu-temp-c}{"\n"}{end}'
```

### Teardown

```sh
# On each machine:
make k3s-uninstall
```

## Producer-local data plane

- Each node mounts `/var/lib/ts-data` (hostPath); the controller mounts it into
  task Pods at `/data/outputs` with `TS_OUTPUTS_DIR=/data/outputs/<wf>/<pod>`.
- The `ts-fileserver` DaemonSet (`hostNetwork`, port 8080) serves that
  directory to other nodes.
- A task stages its parents' outputs at start: a local read when the producer
  ran on the same node, an HTTP fetch from the producer's fileserver
  otherwise. It reports the bytes read, the transfer mode and the staging
  time in its metrics.
- The validation analyzer requires every consumer to read exactly the bytes
  its producer wrote, in local mode exactly when the two were co-located.

## Telemetry

- `ts-bw-probe` downloads a 100 MiB `_probe.bin` from every peer's
  fileserver and writes `ts.io/bw-from-<peer>` (MiB/s) on its own node. Nodes
  probe in wall-clock slots (`TS_PROBE_STAGGER_S`, 60 s) so probes do not
  compete with each other. `services/k8s_cluster.py` turns the annotations
  into the bandwidth matrix used by the estimators.
- `ts-thermal` reads `/sys/class/thermal/thermal_zone*/temp` every 30 s and
  writes `ts.io/cpu-temp-c`. Where no sensor exists (Kind on Docker Desktop),
  the annotation is cleared; a load-average-based synthetic value is only
  published when `TS_THERMAL_SYNTHETIC_FALLBACK=true`, and is never evidence.

## Simulation entry points

```sh
python run_experiment_lab.py run|workload|analyze --help
python run_mechanism_studies.py --help
python analyze_final_evidence.py
python tune_legacy_weights.py --help
python tune_contextual_policy.py --help
python kind_twin_predict.py results/kind-validation/<profile>.json
python -m pytest -q
```

`run_simulation.py`, `k8s_scheduler.py`, `k8s_main.py`, `main.py`,
`server.py` and `submit_workflows.py` are older demo paths kept working for
compatibility; the validated live path is the controller/scheduler pair
installed by the root `kustomization.yaml`.
