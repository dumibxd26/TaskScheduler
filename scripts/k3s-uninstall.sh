#!/usr/bin/env bash
# scripts/k3s-uninstall.sh — tear down the cluster on this host.
#
# pc1 (Mac mini): deletes the ts-master Multipass VM (which carries the
#   k3s server, registry, and all cluster state).
# pc2 (Windows / WSL): deletes the four worker Multipass VMs.
# Linux native: runs the k3s-(agent-)uninstall.sh script.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
[[ -f "${ROOT}/cluster.env" ]] && source "${ROOT}/cluster.env" || true

ALL_VMS=(ts-master ts-node-cpu-1 ts-node-cpu-2 ts-node-io-1 ts-node-mem-1)

if command -v multipass >/dev/null 2>&1; then
    echo "Removing any TaskScheduler Multipass VMs on this host..."
    for vm in "${ALL_VMS[@]}"; do
        if multipass info "$vm" >/dev/null 2>&1; then
            echo "  - deleting $vm"
            multipass delete --purge "$vm" || true
        fi
    done
fi

# Native Linux k3s uninstallers (no-op on macOS / Windows).
if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
    sudo /usr/local/bin/k3s-uninstall.sh || true
fi
if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
    sudo /usr/local/bin/k3s-agent-uninstall.sh || true
fi

echo "Done."
