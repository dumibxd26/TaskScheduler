#!/usr/bin/env bash
# scripts/k3s-uninstall.sh — tear down the cluster on this host.
# Run on pc1: removes k3s server, registry, and all VMs.
# Run on pc2: removes the Multipass VM (or native k3s on Linux).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
[[ -f "${ROOT}/cluster.env" ]] && source "${ROOT}/cluster.env" || true

case "$(uname -s)" in
    Darwin)
        echo "Removing Multipass VMs..."
        for vm in ts-node-mem-1 ts-node-cpu-1 ts-node-cpu-2 ts-node-io-1; do
            multipass info "$vm" >/dev/null 2>&1 && multipass delete --purge "$vm" || true
        done
        ;;
    Linux)
        # Multipass VMs (only present on pc1, harmless on pc2 Linux).
        if command -v multipass >/dev/null 2>&1; then
            for vm in ts-node-cpu-1 ts-node-cpu-2 ts-node-io-1; do
                multipass info "$vm" >/dev/null 2>&1 && multipass delete --purge "$vm" || true
            done
        fi
        # Local registry on pc1.
        if sudo docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^ts-registry$'; then
            sudo docker rm -f ts-registry
        fi
        # k3s server.
        if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
            sudo /usr/local/bin/k3s-uninstall.sh
        fi
        # k3s agent.
        if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
            sudo /usr/local/bin/k3s-agent-uninstall.sh
        fi
        ;;
esac
echo "Done."
