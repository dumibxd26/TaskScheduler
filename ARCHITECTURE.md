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
# Tasks should be distributed across ts-master (pc1) and the
# ts-node-* workers (pc2).

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
