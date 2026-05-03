#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# TaskScheduler cluster bootstrap.
#
# Usage:
#     ./setup_cluster.sh small   # 3 workers, ~3.3 GB total — Mac mini / 8 GB
#     ./setup_cluster.sh big     # 6 workers, ~32  GB total — 32+ GB workstation
#     ./setup_cluster.sh         # defaults to "small" (safest)
#
# Both profiles produce a cluster named ts-cluster, so subsequent commands
# (kubectl, kind, submit_workflows.py) do not need to know which profile
# was chosen.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROFILE="${1:-small}"
CLUSTER_NAME="ts-cluster"

case "${PROFILE}" in
    small) CONFIG_FILE="clusters/small_cluster.yaml" ;;
    big)   CONFIG_FILE="clusters/big_cluster.yaml" ;;
    *)
        echo "Usage: $0 [small|big]" >&2
        echo "  small  3 workers (one of each type) — fits 8 GB host" >&2
        echo "  big    6 workers (two of each type) — needs 32+ GB host" >&2
        exit 2
        ;;
esac

echo "=== TaskScheduler Cluster Setup (profile=${PROFILE}) ==="

# ── 1. Delete old cluster if it exists ──────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "[1/5] Deleting existing cluster '${CLUSTER_NAME}'..."
    kind delete cluster --name "${CLUSTER_NAME}"
else
    echo "[1/5] No existing cluster to delete."
fi

# ── 2. Create the cluster from the chosen config ────────────────
echo "[2/5] Creating cluster from ${CONFIG_FILE}..."
kind create cluster --config "${CONFIG_FILE}"

echo "    Nodes:"
kubectl get nodes -o wide --no-headers

# ── 3. Apply real cgroup resource limits per worker container ────
#    Docker container names are ts-cluster-worker, -worker2, -worker3, ...
#    in the order workers appear in the config file.
echo "[3/5] Applying Docker resource limits (cgroups)..."

if [[ "${PROFILE}" == "small" ]]; then
    # 3 workers — total ≈ 3.3 GB (fits an 8 GB Mac mini)
    docker update --cpus=2 --memory=1g    --memory-swap=1g    "${CLUSTER_NAME}-worker"
    echo "    ts-node-cpu-1  (CPU_OPT) -> 2 CPUs, 1.0 GB"
    docker update --cpus=1 --memory=1536m --memory-swap=1536m "${CLUSTER_NAME}-worker2"
    echo "    ts-node-mem-1  (MEM_OPT) -> 1 CPU,  1.5 GB"
    docker update --cpus=1 --memory=768m  --memory-swap=768m  "${CLUSTER_NAME}-worker3"
    echo "    ts-node-io-1   (IO_OPT)  -> 1 CPU,  0.75 GB"
else
    # 6 workers — total ≈ 32 GB (needs ≥ 32 GB host, 64 GB recommended)
    docker update --cpus=4 --memory=4g --memory-swap=4g "${CLUSTER_NAME}-worker"
    echo "    ts-node-cpu-1  (CPU_OPT) -> 4 CPUs, 4 GB"
    docker update --cpus=4 --memory=4g --memory-swap=4g "${CLUSTER_NAME}-worker2"
    echo "    ts-node-cpu-2  (CPU_OPT) -> 4 CPUs, 4 GB"
    docker update --cpus=2 --memory=8g --memory-swap=8g "${CLUSTER_NAME}-worker3"
    echo "    ts-node-mem-1  (MEM_OPT) -> 2 CPUs, 8 GB"
    docker update --cpus=2 --memory=8g --memory-swap=8g "${CLUSTER_NAME}-worker4"
    echo "    ts-node-mem-2  (MEM_OPT) -> 2 CPUs, 8 GB"
    docker update --cpus=2 --memory=4g --memory-swap=4g "${CLUSTER_NAME}-worker5"
    echo "    ts-node-io-1   (IO_OPT)  -> 2 CPUs, 4 GB"
    docker update --cpus=2 --memory=4g --memory-swap=4g "${CLUSTER_NAME}-worker6"
    echo "    ts-node-io-2   (IO_OPT)  -> 2 CPUs, 4 GB"
fi

# ── 4. Build and load task images (one per task) ────────────────
echo "[4/5] Building task images..."

docker build -t ts-task-io:v1  tasks/task_io/
echo "    Built ts-task-io:v1"
docker build -t ts-task-mem:v1 tasks/task_mem/
echo "    Built ts-task-mem:v1"
docker build -t ts-task-cpu:v1 tasks/task_cpu/
echo "    Built ts-task-cpu:v1"

echo "    Loading images into cluster..."
kind load docker-image ts-task-io:v1  --name "${CLUSTER_NAME}"
kind load docker-image ts-task-mem:v1 --name "${CLUSTER_NAME}"
kind load docker-image ts-task-cpu:v1 --name "${CLUSTER_NAME}"

# ── 5. Build and load scheduler image ───────────────────────────
echo "[5/5] Building scheduler image..."
docker build -t ts-scheduler:latest -f Dockerfile.scheduler .
echo "    Loading ts-scheduler:latest into cluster..."
kind load docker-image ts-scheduler:latest --name "${CLUSTER_NAME}"

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "=== Cluster ready (profile=${PROFILE}) ==="
echo ""
kubectl get nodes --show-labels | grep -E "NAME|node-type"
echo ""
echo "Next steps:"
echo "  kubectl apply -f scheduler-deployment.yaml   # deploy the custom scheduler"
echo "  python3 submit_workflows.py                  # submit a workflow"
