#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="ts-cluster"

echo "=== TaskScheduler Cluster Setup ==="

# ── 1. Delete old cluster if it exists ──────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "[1/5] Deleting existing cluster '${CLUSTER_NAME}'..."
    kind delete cluster --name "${CLUSTER_NAME}"
else
    echo "[1/5] No existing cluster to delete."
fi

# ── 2. Create the cluster with named nodes ──────────────────────
echo "[2/5] Creating cluster from kind-cluster.yaml..."
kind create cluster --config kind-cluster.yaml

echo "    Nodes:"
kubectl get nodes -o wide --no-headers

# ── 3. Apply real cgroup resource limits per node ────────────────
#    This makes the heterogeneity REAL, not just labels.
#    Docker container names stay ts-cluster-worker* regardless of K8s node name.
echo "[3/5] Applying Docker resource limits (cgroups)..."

# CPU-optimized:  4 CPUs, 1 GB RAM, no swap
docker update --cpus=4 --memory=1g --memory-swap=1g "${CLUSTER_NAME}-worker"
echo "    ts-node-cpu  -> 4 CPUs, 1 GB"

# Memory-optimized:  1 CPU, 4 GB RAM, no swap
docker update --cpus=1 --memory=4g --memory-swap=4g "${CLUSTER_NAME}-worker2"
echo "    ts-node-mem  -> 1 CPU,  4 GB"

# I/O-optimized:  2 CPUs, 2 GB RAM, no swap
docker update --cpus=2 --memory=2g --memory-swap=2g "${CLUSTER_NAME}-worker3"
echo "    ts-node-io   -> 2 CPUs, 2 GB"

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
echo "=== Cluster ready ==="
echo ""
kubectl get nodes --show-labels | grep -E "NAME|node-type"
echo ""
echo "Next steps:"
echo "  kubectl apply -f scheduler-deployment.yaml   # deploy the custom scheduler"
echo "  python k8s_main.py                           # run a workflow"
