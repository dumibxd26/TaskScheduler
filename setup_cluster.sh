#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# TaskScheduler cluster bootstrap.
#
# Usage:
#     ./setup_cluster.sh small        # 3 demo workers (one of each type)
#     ./setup_cluster.sh big          # 6 demo workers, needs a 32+ GB host
#     ./setup_cluster.sh balanced     # emulated validation profiles defined in
#     ./setup_cluster.sh homogeneous  #   clusters/kind_profiles.yaml (speeds,
#     ./setup_cluster.sh edge         #   slots, netem links) via kind_lab.py
#     ./setup_cluster.sh              # defaults to "small"
#
# Every profile produces a cluster named ts-cluster.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROFILE="${1:-small}"
CLUSTER_NAME="ts-cluster"
PYTHON="${PYTHON:-python}"

case "${PROFILE}" in
    small)       CONFIG_FILE="clusters/small_cluster.yaml" ;;
    big)         CONFIG_FILE="clusters/big_cluster.yaml" ;;
    balanced|homogeneous|edge)
        exec "${PYTHON}" kind_lab.py create "${PROFILE}" ;;
    *)
        echo "Usage: $0 [small|big|balanced|homogeneous|edge]" >&2
        echo "  small        3 demo workers (one of each type)" >&2
        echo "  big          6 demo workers (two of each type), 32+ GB host" >&2
        echo "  balanced, homogeneous, edge" >&2
        echo "               emulated validation profiles (kind_lab.py)" >&2
        exit 2
        ;;
esac

echo "=== TaskScheduler Cluster Setup (profile=${PROFILE}) ==="

# ── 1. Delete old cluster if it exists ──────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "[1/3] Deleting existing cluster '${CLUSTER_NAME}'..."
    kind delete cluster --name "${CLUSTER_NAME}"
else
    echo "[1/3] No existing cluster to delete."
fi

# ── 2. Create the cluster from the chosen config ────────────────
echo "[2/3] Creating cluster from ${CONFIG_FILE}..."
kind create cluster --config "${CONFIG_FILE}"

echo "    Nodes:"
kubectl get nodes -o wide --no-headers

# ── 3. Apply real cgroup resource limits per worker container ────
#    Docker container names are ts-cluster-worker, -worker2, -worker3, ...
#    in the order workers appear in the config file.
echo "[3/3] Applying Docker resource limits (cgroups)..."

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

echo "    Waiting for all nodes to become Ready after cgroup updates..."
kubectl wait --for=condition=Ready nodes --all --timeout=180s

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "=== Cluster ready (profile=${PROFILE}) ==="
echo ""
kubectl get nodes --show-labels | grep -E "NAME|node-type"
echo ""
echo "Next steps:"
echo "  make images load install examples"
