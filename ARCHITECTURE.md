# TaskScheduler Architecture

TaskScheduler has two execution surfaces: an independent deterministic
discrete-event simulator under `simulation/`, and a Kubernetes control plane
under `controlplane/`. They share selected models but not a policy engine or
identical algorithms. Simulation is the controlled comparative surface; Kind
is the end-to-end integration and descriptive live surface.

> **Validation boundary:** live experiments use one physical Windows/Docker
> host with three Kind worker containers. Balanced, homogeneous, and skewed
> profiles are logical capacity configurations on that host, not three
> physical clusters. The two-host k3s procedure below remains a pending
> deployment target, not completed evidence.

## Components

```
┌──────────────────────────────────────────────────────────────────────┐
│  User: kubectl apply -f my-pipeline.yaml   (a Workflow CRD)          │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
                  ┌──────────────▼──────────────┐
                  │     ts-controller (1x)      │  Deployment
                  │  Watches Workflow CRDs.     │  controlplane/controller
                  │  DAG → Pods (schedulerName  │
                  │  = ts-scheduler). Scrapes   │
                  │  __TS_OUTPUT__= from logs   │
                  │  and injects fields as env  │
                  │  on child Pods.             │
                  └──────────────┬──────────────┘
                                 │ (creates Pods)
                  ┌──────────────▼──────────────┐
                  │     ts-scheduler (1x)       │  Deployment
                  │  Watches Pending Pods,      │  controlplane/scheduler
                  │  invokes Legacy v1/tuned    │
                  │  or Adaptive policy,        │
                  │  policy, applies victim     │
                  │  deletion, binds Pod->node. │
                  └──────┬───────────────┬──────┘
                         │               │
        ┌────────────────┘               └─────────────────┐
        ▼                                                  ▼
┌────────────────────┐                          ┌────────────────────┐
│   ts-fileserver    │  DaemonSet               │ ts-bw-probe (DS)   │
│   nginx → 8080     │  exposes per-node        │ measures inter-    │
│   /var/lib/ts-data │  outputs to peers        │ node bandwidth     │
└────────────────────┘                          │ → Node annotations │
                                                └────────────────────┘
┌────────────────────┐
│   ts-thermal (DS)  │  reads /sys/class/thermal → Node annotation
│  ts.io/cpu-temp-c  │  consumed by ThermalCollector / AdaptivePolicy
└────────────────────┘
```

The container entry modules are `controlplane/controller/main.py` and
`controlplane/scheduler/main.py`. The controller owns DAG progression,
retry, output metadata, and terminal status. The scheduler owns ready-Pod
ordering, placement, binding, profile updates, and preemption actions.

The controller sees the complete Workflow CRD. The current scheduler adapter
constructs scheduler-side workflow templates with no future edges, so live
Adaptive does not execute full-DAG HEFT upward ranking.

## Install — single-host (kind)

```sh
make images   # builds all 7 images
make smoke    # creates kind cluster, loads images, installs, runs linear-3task
```

Or step-by-step:

```sh
make cluster              # ./setup_cluster.sh small  (kind, 3 workers)
make images load install  # build, kind-load, kubectl apply -k .
make examples             # tasktemplates + linear-pipeline-1
kubectl get workflows -w
```

`setup_cluster.sh` accepts `small`, `homogeneous`, `skewed`, and `big`.
The reproducible low-footprint validation matrix is:

```powershell
& .\scripts\run-kind-topology-matrix.ps1 `
  -Profiles small,homogeneous,skewed `
  -Repetitions 3 `
  -Seed 2026
python analyze_kind_topologies.py
```

The retained matrix passed the strict analyzer: 27 trials, 135 completed
workflows, and 540 successful final task records, with no final task failures,
scheduler errors, collection errors, or container restarts. Tuned Legacy has a
lower makespan than v1 in all three logical profiles and by 3.50% in aggregate.
Adaptive lowers median workflow response by 19.40% relative to tuned Legacy,
but raises makespan by 14.80% with 44 real kill/restart preemptions. These are
same-host integration results, not physical distributed-cluster evidence.

The live scorer names are `legacy-v1`, `legacy-tuned`, and `adaptive`;
`legacy` is a compatibility alias for v1. Exact Legacy variants share the
same scorer, profile learning, coverage, and exploration and differ only in
their eight weights.

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

Producer-local:
- Each node mounts `/var/lib/ts-data` (hostPath).
- The controller mounts that into every worker pod at `/data/outputs`,
  with `TS_OUTPUTS_DIR=/data/outputs/<wf>/<pod>`.
- The `ts-fileserver` DaemonSet (`hostNetwork`, port 8080) exposes that
  directory to other nodes.
- The controller injects `TS_PARENT_<NAME>_FILESERVER_URL` env vars so
  task scripts can `curl` parent outputs when they run on a different
  node. Same-node parents are simply readable through the shared host
  mount (no fetch needed).

## Telemetry

- `ts-bw-probe` curls the 100 MB `_probe.bin` from every peer's
  fileserver and writes `ts.io/bw-from-<peer>=<MB/s>` onto
  its own Node. `services/bandwidth.py` reads these to refine ECT.
- `ts-thermal` reads `/sys/class/thermal/thermal_zone0/temp` (with a
  load-avg fallback for hosts without sysfs thermals) every 30 s and
  writes `ts.io/cpu-temp-c` onto its Node.

## Simulation and legacy entry points

The definitive simulator and test entry points are:

```sh
python run_experiment_lab.py --help
python tune_legacy_weights.py --help
python analyze_final_evidence.py
python -m pytest
```

Simulation has nine policies. Historical simulator `legacy` is an EFT-based
approximation retained for comparison; only `legacy-v1` and `legacy-tuned`
run the exact live eight-factor scorer in deterministic virtual time.

`run_simulation.py`, `k8s_scheduler.py`, `k8s_main.py`, and
`submit_workflows.py` are older development/demo paths retained for
compatibility. The validated live path is the controller/scheduler split
installed by the root `kustomization.yaml`.
