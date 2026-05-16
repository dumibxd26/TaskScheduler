# TaskScheduler — productization layout

This file documents the architecture introduced by the productization
refactor. It complements the original `README.md` (simulation-focused).

## Components

```
┌──────────────────────────────────────────────────────────────────────┐
│  User: kubectl apply -f my-pipeline.yaml   (a Workflow CRD)          │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
                  ┌──────────────▼──────────────┐
                  │     ts-controller (1×)      │  Deployment
                  │  Watches Workflow CRDs.     │  cmd/controller/main.py
                  │  DAG → Pods (schedulerName  │
                  │  = ts-scheduler). Scrapes   │
                  │  __TS_OUTPUT__= from logs   │
                  │  and injects fields as env  │
                  │  on child Pods.             │
                  └──────────────┬──────────────┘
                                 │ (creates Pods)
                  ┌──────────────▼──────────────┐
                  │     ts-scheduler (1×)       │  Deployment
                  │  Watches Pending Pods,      │  cmd/scheduler/main.py
                  │  drives SchedulerEngine ▷   │
                  │  AdaptivePolicy (ECT + UCB  │
                  │  + thermal + failure).      │
                  │  Binds pod → node.          │
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

## Install — single-host (kind)

```sh
make images   # builds all 7 images
make smoke    # creates kind cluster, loads images, installs, runs linear-3task
```

Or step-by-step:

```sh
make cluster              # ./setup_cluster.sh small  (kind, 3 workers)
make images load install  # build, kind-load, kubectl apply -k manifests/
make examples             # tasktemplates + linear-pipeline-1
kubectl get workflows -w
```

## Install — two-host k3s LAN cluster (real deployment)

For testing with real cross-machine networking, thermals, and bandwidth
measurements. This is what the algorithm is designed for.

### Topology

| Role   | Host                              | OS               | Logical k8s nodes                                              |
|--------|-----------------------------------|------------------|----------------------------------------------------------------|
| Master | **pc1** — Ryzen 7 PRO 8840HS / 64 GB | Linux            | `ts-pc1` (control-plane) + `ts-node-cpu-1`, `ts-node-cpu-2`, `ts-node-io-1` (Multipass VMs) |
| Slave  | **pc2** — Mac mini M2 / 8 GB      | macOS + Multipass VM | `ts-node-mem-1` (single VM, MEM_OPT, PASSIVE cooling, arm64) |

Five logical nodes, two physical machines, two CPU architectures (amd64
+ arm64), one apiserver. The `ts-scheduler` runs on **pc1** (the master);
the Mac mini is purely a worker. There's exactly one k3s server in the
cluster — it lives on pc1.

### Prerequisites

- pc1: Linux with sudo, `curl`, `docker`, and `multipass`
  (`sudo snap install multipass` on Ubuntu).
- pc2: macOS with `multipass` (`brew install multipass`) and `docker`
  (optional — only needed if you want to build images locally on pc2).
- Both machines on the same LAN, ports `6443/tcp`, `8080/tcp`,
  `5000/tcp`, `8472/udp`, `10250/tcp` open between them.

### Step 1 — Configure IPs (both machines)

```sh
cp cluster.env.example cluster.env
# Edit SERVER_IP=<pc1 LAN IP>, PC2_HOST_IP=<pc2 LAN IP>, CLUSTER_TOKEN=<any string>
```

Both machines must share the same `cluster.env` (`scp` it from pc1 to pc2).

### Step 2 — Bring up the master (pc1)

```sh
# On pc1:
make k3s-server           # installs k3s, labels nodes, starts registry,
                          # spawns 3 logical worker VMs

kubectl get nodes -L node-type -L ts.cooling-class
# NAME             STATUS   ROLES                  NODE-TYPE   TS.COOLING-CLASS
# ts-pc1           Ready    control-plane,master   CPU_OPT     STANDARD
# ts-node-cpu-1    Ready    <none>                 CPU_OPT     HIGH
# ts-node-cpu-2    Ready    <none>                 CPU_OPT     STANDARD
# ts-node-io-1     Ready    <none>                 IO_OPT      STANDARD
```

### Step 3 — Join the slave (pc2)

```sh
# Copy cluster.env from pc1 to pc2 first:
scp cluster.env pc2:/path/to/TaskScheduler/

# On pc2 (Mac mini):
make k3s-agent            # spawns a Linux VM, installs k3s agent inside,
                          # joins https://${SERVER_IP}:6443
```

Back on pc1:

```sh
kubectl get nodes
# ts-node-mem-1 should now also be Ready.
```

### Step 4 — Build and push multi-arch images (pc1)

```sh
# One-time buildx setup:
docker buildx create --use --name ts-builder
docker buildx inspect --bootstrap

# Build + push all 7 images for amd64 AND arm64:
make images-multiarch
```

(If you don't need cross-arch — e.g. both machines are amd64 — `make
images push` is enough.)

### Step 5 — Install the platform (pc1)

```sh
make install-remote
# Rewrites image refs to ${REGISTRY}/ts-*:v1 on the fly,
# applies manifests, waits for rollouts.
```

### Step 6 — Run a workflow

```sh
kubectl apply -f examples/tasktemplates.yaml
kubectl apply -f examples/linear-3task.yaml
kubectl get workflows -w
```

### Verify it's real (not simulated)

Quick automated check (recommended):

```sh
make verify-cluster       # nodes, labels, control-plane, DaemonSets, bw + thermal annotations
make verify-cluster-run   # everything above + submits linear-pipeline-1 and watches it finish
```

Or inspect by hand:

```sh
# Cross-machine pod placement:
kubectl get pods -l ts.io/workflow=linear-pipeline-1 -o wide
# Tasks should be distributed across ts-pc1, ts-node-*-1, ts-node-mem-1.

# Real LAN bandwidth (from ts-bw-probe):
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.annotations.ts\.io/bw-to-ts-node-mem-1}{"\n"}{end}'
# Should show ~100 MB/s on gigabit LAN.

# Real CPU temperatures (from ts-thermal):
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.annotations.ts\.io/cpu-temp-c}{"\n"}{end}'
# pc1 nodes: ~40–70 °C (sysfs). pc2 Mac mini VM: load-avg fallback.
```

### Teardown

```sh
# On each machine:
make k3s-uninstall
```

## Data plane (Phase 1)

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

## Telemetry (Phases 2 & 5)

- `ts-bw-probe` curls the 100 MB `_probe.bin` from every peer's
  fileserver every 10 min and writes `ts.io/bw-to-<peer>=<MB/s>` onto
  its own Node. `services/bandwidth.py` reads these to refine ECT.
- `ts-thermal` reads `/sys/class/thermal/thermal_zone0/temp` (with a
  load-avg fallback for hosts without sysfs thermals) every 30 s and
  writes `ts.io/cpu-temp-c` onto its Node.

## Backwards compatibility

The simulation path is unchanged:

```sh
python3 run_simulation.py
python3 test_simulation.py   # 25/25 should still pass
```

`k8s_scheduler.py` and `submit_workflows.py` are kept for reference but
will be removed once the controller+scheduler split is validated
end-to-end on the kind cluster.
