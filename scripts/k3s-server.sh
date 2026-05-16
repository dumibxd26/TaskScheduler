#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# scripts/k3s-server.sh — bring up the k3s control plane on pc1 (Mac mini).
#
# k3s does not run natively on macOS, so this script:
#   1. Launches a Multipass Linux VM (`ts-master`) bridged onto the LAN.
#   2. Installs k3s server inside it.
#   3. Starts an insecure Docker registry inside it on :5000 (LAN only).
#   4. Labels the node ts-master / MEM_OPT / PASSIVE cooling.
#   5. Writes the VM's LAN IP to cluster.env (SERVER_IP) and copies the
#      kubeconfig to ~/.kube/config (rewritten to use SERVER_IP).
#
# Idempotent: re-running re-labels and re-applies registry trust without
# wiping the cluster. Use `make k3s-uninstall` for a clean slate.
#
# Run on pc1 (Mac mini):
#     ./scripts/k3s-server.sh
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

require() { command -v "$1" >/dev/null 2>&1 || { echo "✗ missing required command: $1" >&2; exit 1; }; }
require multipass
require curl

VM=ts-master

# ── 1. Launch the master VM ──────────────────────────────────────────
echo "[1/5] Launching Multipass VM '${VM}' (${MASTER_VM_CPUS} CPUs, ${MASTER_VM_MEM_GB} GB)..."
if multipass info "${VM}" >/dev/null 2>&1; then
    echo "      VM already exists — reusing."
else
    # --bridged uses the default bridged interface configured via
    # `multipass set local.bridged-network=<iface>`. We try bridged
    # first (so the VM gets a LAN IP reachable from pc2); if that
    # fails (no bridged interface set), fall back to default NAT and
    # warn the user.
    if ! multipass launch --name "${VM}" \
            --cpus "${MASTER_VM_CPUS}" \
            --memory "${MASTER_VM_MEM_GB}G" \
            --disk "${MASTER_VM_DISK_GB}G" \
            --network name=bridged,mode=auto 22.04 2>/dev/null; then
        echo "      ⚠  bridged network not configured; falling back to NAT."
        echo "         pc2 will NOT be able to reach the apiserver."
        echo "         Fix with:  multipass set local.bridged-network=<en0|en1>"
        echo "         then run:  make k3s-uninstall && make k3s-server"
        multipass launch --name "${VM}" \
            --cpus "${MASTER_VM_CPUS}" \
            --memory "${MASTER_VM_MEM_GB}G" \
            --disk "${MASTER_VM_DISK_GB}G" 22.04
    fi
fi

# Pick the bridged IP (i.e. the one NOT in 10.x — Multipass NAT range).
VM_IP=$(multipass info "${VM}" --format json \
    | python3 -c "import json,sys; d=json.load(sys.stdin)['info']['${VM}']
ips=d.get('ipv4', [])
bridged=[ip for ip in ips if not ip.startswith('10.')]
print(bridged[0] if bridged else (ips[0] if ips else ''))")

if [[ -z "${VM_IP}" ]]; then
    echo "✗ Could not determine VM IP." >&2
    exit 1
fi
echo "      VM IP: ${VM_IP}"

# Update cluster.env so the user (and pc2) sees the correct SERVER_IP.
if [[ "${SERVER_IP}" != "${VM_IP}" ]]; then
    echo "      Updating SERVER_IP in cluster.env: ${SERVER_IP} → ${VM_IP}"
    sed -i.bak -E "s|^SERVER_IP=.*|SERVER_IP=${VM_IP}|" "${ENV_FILE}"
    SERVER_IP="${VM_IP}"
fi

# ── 2. Install k3s server inside the VM ──────────────────────────────
echo "[2/5] Installing k3s server inside ${VM}..."
multipass exec "${VM}" -- bash -c "command -v k3s >/dev/null 2>&1 || \
    curl -sfL https://get.k3s.io | K3S_TOKEN='${CLUSTER_TOKEN}' \
        sudo sh -s - server \
            --node-name=ts-master \
            --tls-san=${SERVER_IP} \
            --bind-address=0.0.0.0 \
            --advertise-address=${SERVER_IP} \
            --disable=traefik \
            --disable=servicelb \
            --write-kubeconfig-mode=644"

# Wait for the apiserver.
echo "      Waiting for apiserver..."
for _ in $(seq 1 30); do
    if multipass exec "${VM}" -- sudo k3s kubectl get nodes >/dev/null 2>&1; then break; fi
    sleep 2
done

# ── 3. Insecure registry inside the VM ───────────────────────────────
echo "[3/5] Starting local registry on ${REGISTRY} (inside ${VM})..."
multipass exec "${VM}" -- bash -c "command -v docker >/dev/null 2>&1 || \
    (curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker ubuntu)"
multipass exec "${VM}" -- bash -c "sudo docker ps --format '{{.Names}}' | grep -q '^ts-registry\$' || \
    sudo docker run -d --restart=always --name ts-registry -p ${REGISTRY_PORT}:5000 registry:2 >/dev/null"

multipass exec "${VM}" -- sudo bash -c "cat > /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  \"${REGISTRY}\":
    endpoint:
      - \"http://${REGISTRY}\"
configs:
  \"${REGISTRY}\":
    tls:
      insecure_skip_verify: true
EOF
systemctl restart k3s"

# ── 4. Copy kubeconfig to the macOS host ─────────────────────────────
echo "[4/5] Copying kubeconfig to ~/.kube/config..."
mkdir -p "${HOME}/.kube"
multipass exec "${VM}" -- sudo cat /etc/rancher/k3s/k3s.yaml \
    | sed "s|https://127.0.0.1:6443|https://${SERVER_IP}:6443|" \
    > "${HOME}/.kube/config"
chmod 600 "${HOME}/.kube/config"

# Make sure kubectl exists on the host.
require kubectl

# ── 5. Label the master node ─────────────────────────────────────────
echo "[5/5] Labelling ts-master..."
read -r CAP_CPU CAP_MEM CAP_COOL <<< "${NODE_CAP_MASTER}"
for _ in $(seq 1 30); do
    if kubectl get node ts-master >/dev/null 2>&1; then break; fi
    sleep 2
done
kubectl label --overwrite node ts-master \
    node-type=MEM_OPT \
    ts.capacity/cpu="${CAP_CPU}" \
    ts.capacity/memory="${CAP_MEM}" \
    ts.cooling-class="${CAP_COOL}"

echo ""
echo "=== pc1 (Mac mini) control plane ready ==="
kubectl get nodes -L node-type -L ts.cooling-class
echo ""
echo "SERVER_IP=${SERVER_IP}    (already written to ${ENV_FILE})"
echo ""
echo "Next steps:"
echo "  1. Copy cluster.env to pc2 (Windows laptop)."
echo "  2. On pc2, in Git Bash or WSL, run:  ./scripts/k3s-agent.sh"
echo "  3. After all workers join, on pc1:   make images-multiarch && make install-remote"
