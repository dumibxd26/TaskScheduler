#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/k3s-agent.sh — join workers to the cluster from pc2 (laptop).
#
# k3s does not run natively on Windows, so on the Windows laptop this
# script spawns 4 Multipass Linux VMs and installs k3s agent in each:
#     ts-node-cpu-1   CPU_OPT, HIGH cooling
#     ts-node-cpu-2   CPU_OPT, STANDARD cooling
#     ts-node-io-1    IO_OPT,  STANDARD cooling
#     ts-node-mem-1   MEM_OPT, STANDARD cooling
# All four register with the master at https://${SERVER_IP}:6443 (which
# is the IP of the master VM on pc1 — the Mac mini).
#
# On native Linux this script falls back to running a single agent on
# the host (useful for adding extra physical machines later).
#
# Run on pc2 (Windows laptop): open Git Bash or WSL and run:
#     ./scripts/k3s-agent.sh
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
ENV_FILE="${ROOT}/cluster.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "✗ ${ENV_FILE} not found. Copy it from pc1 (Mac mini)." >&2
    exit 2
fi
# shellcheck disable=SC1090
source "${ENV_FILE}"

if [[ -z "${SERVER_IP:-}" || "${SERVER_IP}" == "192.168.1.20" ]]; then
    echo "✗ SERVER_IP in cluster.env still has the placeholder value."
    echo "  Run scripts/k3s-server.sh on pc1 first; it writes the master VM IP into cluster.env."
    exit 2
fi

require() { command -v "$1" >/dev/null 2>&1 || { echo "✗ missing required command: $1" >&2; exit 1; }; }

# Detect environment.
HOST_OS="$(uname -s)"
USE_MULTIPASS=0
case "${HOST_OS}" in
    MINGW*|MSYS*|CYGWIN*) USE_MULTIPASS=1 ;;      # Git Bash on Windows
    Linux)
        # WSL or native Linux?
        if grep -qi microsoft /proc/version 2>/dev/null; then
            USE_MULTIPASS=1                        # WSL → multipass.exe on host
        fi
        ;;
    Darwin) USE_MULTIPASS=1 ;;                     # macOS (unusual here, but supported)
esac

if [[ "${USE_MULTIPASS}" -eq 1 ]]; then
    require multipass
fi

# Connectivity check — must reach the master apiserver from this host.
echo "Pre-flight: reaching apiserver at ${SERVER_IP}:6443..."
if ! (echo > /dev/tcp/${SERVER_IP}/6443) >/dev/null 2>&1; then
    if command -v nc >/dev/null 2>&1; then
        nc -zv "${SERVER_IP}" 6443 || { echo "✗ Cannot reach ${SERVER_IP}:6443. Check LAN/firewall."; exit 1; }
    else
        echo "✗ Cannot reach ${SERVER_IP}:6443. Check LAN/firewall." >&2
        exit 1
    fi
fi
echo "      OK"

# Helper: spawn one VM + install k3s agent + write registries.yaml + label.
# Args: NAME NODE_TYPE CPU MEM_MB COOL
spawn_vm_agent() {
    local NAME="$1" NODE_TYPE="$2" CPU="$3" MEM_MB="$4" COOL="$5"
    local MEM_GB=$(( (MEM_MB + 1023) / 1024 ))

    if multipass info "${NAME}" >/dev/null 2>&1; then
        echo "    [${NAME}] VM exists — reusing."
    else
        echo "    [${NAME}] launching (${CPU} CPUs, ${MEM_GB} GB)..."
        multipass launch --name "${NAME}" \
            --cpus "${CPU}" --memory "${MEM_GB}G" --disk 20G 22.04
    fi

    echo "    [${NAME}] installing k3s agent..."
    multipass exec "${NAME}" -- bash -c "command -v k3s >/dev/null 2>&1 || \
        curl -sfL https://get.k3s.io | K3S_URL=https://${SERVER_IP}:6443 \
            K3S_TOKEN='${CLUSTER_TOKEN}' \
            sudo sh -s - agent --node-name=${NAME}"

    echo "    [${NAME}] trusting insecure registry ${REGISTRY}..."
    multipass exec "${NAME}" -- sudo bash -c "mkdir -p /etc/rancher/k3s && \
cat > /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  \"${REGISTRY}\":
    endpoint:
      - \"http://${REGISTRY}\"
configs:
  \"${REGISTRY}\":
    tls:
      insecure_skip_verify: true
EOF
systemctl restart k3s-agent"

    # Suggested label command (run on pc1 after all agents join).
    echo "    [${NAME}] label hint:"
    echo "        kubectl label --overwrite node ${NAME} node-type=${NODE_TYPE} \\"
    echo "            ts.capacity/cpu=${CPU} ts.capacity/memory=${MEM_MB} ts.cooling-class=${COOL}"
}

if [[ "${USE_MULTIPASS}" -eq 1 ]]; then
    echo "Spawning 4 worker VMs on this host..."
    read -r C1_CPU C1_MEM C1_COOL <<< "${NODE_CAP_CPU_1}"
    read -r C2_CPU C2_MEM C2_COOL <<< "${NODE_CAP_CPU_2}"
    read -r I1_CPU I1_MEM I1_COOL <<< "${NODE_CAP_IO_1}"
    read -r M1_CPU M1_MEM M1_COOL <<< "${NODE_CAP_MEM_1}"

    spawn_vm_agent ts-node-cpu-1 CPU_OPT "${C1_CPU}" "${C1_MEM}" "${C1_COOL}"
    spawn_vm_agent ts-node-cpu-2 CPU_OPT "${C2_CPU}" "${C2_MEM}" "${C2_COOL}"
    spawn_vm_agent ts-node-io-1  IO_OPT  "${I1_CPU}" "${I1_MEM}" "${I1_COOL}"
    spawn_vm_agent ts-node-mem-1 MEM_OPT "${M1_CPU}" "${M1_MEM}" "${M1_COOL}"
else
    # Native Linux fallback — one extra physical worker.
    NODE_NAME="${1:-ts-extra-1}"
    NODE_TYPE="${2:-CPU_OPT}"
    echo "Native Linux mode — joining single agent ${NODE_NAME} (${NODE_TYPE})."
    if ! command -v k3s >/dev/null 2>&1; then
        curl -sfL https://get.k3s.io | K3S_URL="https://${SERVER_IP}:6443" \
            K3S_TOKEN="${CLUSTER_TOKEN}" \
            sudo sh -s - agent --node-name="${NODE_NAME}"
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
    echo "Label hint (run on pc1):"
    echo "  kubectl label --overwrite node ${NODE_NAME} node-type=${NODE_TYPE}"
fi

echo ""
echo "=== Agents launched ==="
echo "On pc1 (Mac mini), verify and label:"
echo "  kubectl get nodes -L node-type -L ts.cooling-class"
echo ""
echo "Then paste the label hints printed above for each ts-node-* node."
