#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/k3s-agent.sh — join an existing k3s cluster as a worker.
#
# On pc2 (Mac mini): macOS can't run k3s directly, so this script spawns a
# Linux VM via Multipass (or OrbStack if Multipass is unavailable) and runs
# the k3s agent inside that VM. The VM joins the cluster as node
# `ts-node-mem-1` and is labelled MEM_OPT / PASSIVE cooling.
#
# On any other Linux host: it installs k3s agent natively.
#
# Run after pc1's k3s-server.sh is up and you've copied cluster.env to this
# machine:
#     ./scripts/k3s-agent.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
ENV_FILE="${ROOT}/cluster.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "✗ ${ENV_FILE} not found. Copy cluster.env.example to cluster.env and edit it." >&2
    exit 2
fi
# shellcheck disable=SC1090
source "${ENV_FILE}"

NODE_NAME="${1:-ts-node-mem-1}"
NODE_TYPE="${2:-MEM_OPT}"
read -r CAP_CPU CAP_MEM CAP_COOL <<< "${NODE_CAP_MEM_1}"
MEM_GB=$(( (CAP_MEM + 1023) / 1024 ))

echo "=== Joining ${NODE_NAME} (${NODE_TYPE}) to cluster at ${SERVER_IP} ==="

case "$(uname -s)" in
    Darwin)
        # ── macOS path: agent runs in a Linux VM ─────────────────────
        if ! command -v multipass >/dev/null 2>&1; then
            echo "✗ Multipass not installed. Install with: brew install multipass" >&2
            exit 1
        fi
        if multipass info "${NODE_NAME}" >/dev/null 2>&1; then
            echo "    VM ${NODE_NAME} already exists — reusing."
        else
            echo "    Launching VM ${NODE_NAME} (${CAP_CPU} CPUs, ${MEM_GB} GB)..."
            # Mac mini has only 8 GB total — keep VM modest.
            multipass launch --name "${NODE_NAME}" \
                --cpus "${CAP_CPU}" --memory "${MEM_GB}G" --disk 20G 22.04
        fi

        echo "    Installing k3s agent inside VM..."
        multipass exec "${NODE_NAME}" -- bash -c "command -v k3s >/dev/null 2>&1 || \
            curl -sfL https://get.k3s.io | K3S_URL=https://${SERVER_IP}:6443 \
                K3S_TOKEN=${CLUSTER_TOKEN} \
                sudo sh -s - agent --node-name=${NODE_NAME}"

        echo "    Trusting insecure registry ${REGISTRY} inside VM..."
        multipass exec "${NODE_NAME}" -- sudo bash -c "mkdir -p /etc/rancher/k3s && \
cat > /etc/rancher/k3s/registries.yaml <<EOF2
mirrors:
  \"${REGISTRY}\":
    endpoint:
      - \"http://${REGISTRY}\"
configs:
  \"${REGISTRY}\":
    tls:
      insecure_skip_verify: true
EOF2
systemctl restart k3s-agent"
        ;;

    Linux)
        # ── Linux path: agent runs natively ──────────────────────────
        if ! command -v k3s >/dev/null 2>&1; then
            echo "    Installing k3s agent natively..."
            curl -sfL https://get.k3s.io | K3S_URL="https://${SERVER_IP}:6443" \
                K3S_TOKEN="${CLUSTER_TOKEN}" \
                sudo sh -s - agent --node-name="${NODE_NAME}"
        else
            echo "    k3s already installed — restarting agent."
            sudo systemctl restart k3s-agent
        fi

        sudo mkdir -p /etc/rancher/k3s
        sudo tee /etc/rancher/k3s/registries.yaml > /dev/null <<EOF
mirrors:
  "${REGISTRY}":
    endpoint:
      - "http://${REGISTRY}"
configs:
  "${REGISTRY}":
    tls:
      insecure_skip_verify: true
EOF
        sudo systemctl restart k3s-agent
        ;;

    *)
        echo "✗ Unsupported host OS: $(uname -s)" >&2
        exit 1
        ;;
esac

echo ""
echo "=== Agent ${NODE_NAME} joined ==="
echo "On pc1, verify with:  kubectl get nodes -L node-type"
echo ""
echo "Apply node labels from pc1:"
echo "  kubectl label --overwrite node ${NODE_NAME} \\"
echo "    node-type=${NODE_TYPE} \\"
echo "    ts.capacity/cpu=${CAP_CPU} \\"
echo "    ts.capacity/memory=${CAP_MEM} \\"
echo "    ts.cooling-class=${CAP_COOL}"
