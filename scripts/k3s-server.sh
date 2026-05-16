#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/k3s-server.sh — bring up the k3s control plane on pc1 (laptop).
#
# Idempotent: re-running re-applies labels and restarts agents but does not
# wipe the cluster. Use `sudo /usr/local/bin/k3s-uninstall.sh` for a clean
# slate.
#
# Steps performed:
#   1. Install k3s in server mode (apiserver + etcd + kubelet on the host).
#   2. Label the host node with node-type=CPU_OPT and capacity hints.
#   3. Start an insecure local registry on REGISTRY_HOST:REGISTRY_PORT so
#      both this host and pc2 can pull our images over the LAN.
#   4. Spawn 3 extra k3s agents in Multipass VMs to give us heterogeneous
#      worker nodes (CPU_OPT ×2 + IO_OPT ×1). The Mac mini contributes the
#      remaining MEM_OPT node.
#
# Run on pc1 only:
#     sudo ./scripts/k3s-server.sh
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

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "✗ missing required command: $1" >&2; exit 1; }
}
require curl
require sudo

# ── 1. Install k3s server ────────────────────────────────────────────
if ! command -v k3s >/dev/null 2>&1; then
    echo "[1/4] Installing k3s server..."
    curl -sfL https://get.k3s.io | sudo sh -s - server \
        --node-name=ts-pc1 \
        --token="${CLUSTER_TOKEN}" \
        --tls-san="${SERVER_IP}" \
        --bind-address="${SERVER_IP}" \
        --advertise-address="${SERVER_IP}" \
        --disable=traefik \
        --disable=servicelb \
        --write-kubeconfig-mode=644
else
    echo "[1/4] k3s already installed — skipping installer."
fi

# Wait for the apiserver.
echo "    Waiting for apiserver..."
for _ in $(seq 1 30); do
    if sudo k3s kubectl get nodes >/dev/null 2>&1; then break; fi
    sleep 2
done

# Copy kubeconfig for the invoking user.
mkdir -p "${HOME}/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "${HOME}/.kube/config"
sudo chown "$(id -u):$(id -g)" "${HOME}/.kube/config"
sed -i "s|https://127.0.0.1:6443|https://${SERVER_IP}:6443|" "${HOME}/.kube/config"

# ── 2. Label the server node ─────────────────────────────────────────
echo "[2/4] Labelling ts-pc1 (CPU_OPT, control-plane host)..."
read -r SCAP_CPU SCAP_MEM SCAP_COOL <<< "${NODE_CAP_PC1_SERVER}"
kubectl label --overwrite node ts-pc1 \
    node-type=CPU_OPT \
    ts.capacity/cpu="${SCAP_CPU}" \
    ts.capacity/memory="${SCAP_MEM}" \
    ts.cooling-class="${SCAP_COOL}"

# ── 3. Local insecure registry on pc1 ────────────────────────────────
echo "[3/4] Starting local registry on ${REGISTRY}..."
if ! sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^ts-registry$'; then
    sudo docker run -d --restart=always --name ts-registry \
        -p "${REGISTRY_PORT}:5000" registry:2 >/dev/null
fi

# Tell k3s's containerd to trust the insecure registry.
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
sudo systemctl restart k3s

# ── 4. Spawn 3 extra agents in Multipass VMs ─────────────────────────
spawn_vm_agent() {
    local NAME="$1" NODE_TYPE="$2" CPU="$3" MEM_MB="$4" COOL="$5"
    local MEM_GB=$(( (MEM_MB + 1023) / 1024 ))
    if multipass info "${NAME}" >/dev/null 2>&1; then
        echo "    VM ${NAME} already exists — skipping launch."
    else
        echo "    Launching VM ${NAME} (${CPU} CPUs, ${MEM_GB} GB)..."
        multipass launch --name "${NAME}" --cpus "${CPU}" --memory "${MEM_GB}G" --disk 20G 22.04
    fi
    multipass exec "${NAME}" -- bash -c "command -v k3s >/dev/null 2>&1 || \
        curl -sfL https://get.k3s.io | K3S_URL=https://${SERVER_IP}:6443 \
            K3S_TOKEN=${CLUSTER_TOKEN} \
            sudo sh -s - agent --node-name=${NAME}"
    # Trust the insecure registry inside the VM too.
    multipass exec "${NAME}" -- sudo bash -c "cat > /etc/rancher/k3s/registries.yaml <<EOF2
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
    # Wait for the node to register, then label it.
    for _ in $(seq 1 30); do
        if kubectl get node "${NAME}" >/dev/null 2>&1; then break; fi
        sleep 2
    done
    kubectl label --overwrite node "${NAME}" \
        node-type="${NODE_TYPE}" \
        ts.capacity/cpu="${CPU}" \
        ts.capacity/memory="${MEM_MB}" \
        ts.cooling-class="${COOL}"
}

if command -v multipass >/dev/null 2>&1; then
    echo "[4/4] Spawning logical worker VMs..."
    read -r C1_CPU C1_MEM C1_COOL <<< "${NODE_CAP_CPU_1}"
    read -r C2_CPU C2_MEM C2_COOL <<< "${NODE_CAP_CPU_2}"
    read -r I1_CPU I1_MEM I1_COOL <<< "${NODE_CAP_IO_1}"
    spawn_vm_agent ts-node-cpu-1 CPU_OPT "${C1_CPU}" "${C1_MEM}" "${C1_COOL}"
    spawn_vm_agent ts-node-cpu-2 CPU_OPT "${C2_CPU}" "${C2_MEM}" "${C2_COOL}"
    spawn_vm_agent ts-node-io-1  IO_OPT  "${I1_CPU}" "${I1_MEM}" "${I1_COOL}"
else
    echo "[4/4] multipass not found — skipping VM agents. Install with:"
    echo "       sudo snap install multipass  (Ubuntu)"
    echo "       brew install multipass        (macOS)"
fi

echo ""
echo "=== pc1 control plane ready ==="
kubectl get nodes -L node-type -L ts.cooling-class
echo ""
echo "Next: on pc2 (Mac mini), run scripts/k3s-agent.sh after copying cluster.env there."
echo "Then push images: make push REGISTRY=${REGISTRY}"
