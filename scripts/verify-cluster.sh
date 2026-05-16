#!/usr/bin/env bash
# scripts/verify-cluster.sh — sanity check the two-host k3s deployment.
# Verifies: node readiness, labels, control-plane rollout, DaemonSet coverage,
# bandwidth annotations, thermal annotations, and (optionally) submits a
# workflow and watches it run to completion.
#
# Usage:  ./scripts/verify-cluster.sh           # checks only
#         ./scripts/verify-cluster.sh --run     # also submits linear-pipeline-1
set -euo pipefail

RUN_WF=0
[[ "${1:-}" == "--run" ]] && RUN_WF=1

pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; FAILED=1; }
info() { printf "  · %s\n" "$1"; }
hdr()  { printf "\n\033[1m=== %s ===\033[0m\n" "$1"; }
FAILED=0

hdr "1. kubectl reachable"
if kubectl version --request-timeout=5s >/dev/null 2>&1; then
    pass "apiserver reachable ($(kubectl config current-context))"
else
    fail "kubectl cannot reach apiserver — is kubeconfig set?"
    exit 1
fi

hdr "2. Nodes Ready and labelled"
NODES=$(kubectl get nodes -o json)
TOTAL=$(echo "$NODES" | jq '.items | length')
READY=$(echo "$NODES" | jq '[.items[] | select(.status.conditions[] | select(.type=="Ready" and .status=="True"))] | length')
info "nodes total=${TOTAL}  ready=${READY}"
[[ "$READY" -ge 2 ]] && pass "≥2 nodes Ready" || fail "need at least 2 Ready nodes for a real cross-host test"

LABELLED=$(echo "$NODES" | jq '[.items[] | select(.metadata.labels["node-type"] != null)] | length')
[[ "$LABELLED" -eq "$TOTAL" ]] \
    && pass "all ${TOTAL} nodes have node-type label" \
    || fail "${LABELLED}/${TOTAL} nodes labelled with node-type (set with: kubectl label node <n> node-type=...)"

# Architectural diversity check — interesting for thesis.
ARCHES=$(echo "$NODES" | jq -r '[.items[].status.nodeInfo.architecture] | unique | join(",")')
info "architectures: ${ARCHES}"
[[ "$ARCHES" == *","* ]] && pass "heterogeneous architectures (good for cross-arch test)" \
                          || info "single architecture — fine, but no arm64+amd64 mix"

hdr "3. Control-plane rolled out"
for d in ts-controller ts-scheduler; do
    if kubectl -n ts-system rollout status deploy/$d --timeout=10s >/dev/null 2>&1; then
        pass "$d Ready"
    else
        fail "$d not Ready (kubectl -n ts-system describe deploy/$d)"
    fi
done

hdr "4. DaemonSets cover every node"
for ds in ts-fileserver ts-bw-probe ts-thermal; do
    DESIRED=$(kubectl -n ts-system get ds/$ds -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)
    READY=$(kubectl -n ts-system get ds/$ds -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)
    if [[ "$DESIRED" -gt 0 && "$READY" -eq "$DESIRED" ]]; then
        pass "$ds  ${READY}/${DESIRED}"
    else
        fail "$ds  ${READY}/${DESIRED}"
    fi
done

hdr "5. Bandwidth annotations present"
BW_NODES=$(kubectl get nodes -o json | jq '[.items[] | select(.metadata.annotations | to_entries[]? | .key | startswith("ts.io/bw-to-"))] | length')
if [[ "$BW_NODES" -ge 1 ]]; then
    pass "${BW_NODES} node(s) have ts.io/bw-to-* annotations"
    kubectl get nodes -o json | jq -r '
        .items[] | .metadata.name as $n
        | .metadata.annotations | to_entries[]?
        | select(.key | startswith("ts.io/bw-to-"))
        | "    \($n) → \(.key | sub("ts.io/bw-to-"; "")):  \(.value) MB/s"'
else
    info "no bandwidth annotations yet — bw-probe runs every 10 min, wait and re-check"
fi

hdr "6. Thermal annotations present"
TH_NODES=$(kubectl get nodes -o json | jq '[.items[] | select(.metadata.annotations["ts.io/cpu-temp-c"] != null)] | length')
if [[ "$TH_NODES" -ge 1 ]]; then
    pass "${TH_NODES} node(s) report ts.io/cpu-temp-c"
    kubectl get nodes -o json | jq -r '.items[] | select(.metadata.annotations["ts.io/cpu-temp-c"] != null) | "    \(.metadata.name):  \(.metadata.annotations["ts.io/cpu-temp-c"]) °C"'
else
    info "no thermal annotations yet — collector runs every 30 s, wait and re-check"
fi

if [[ "$RUN_WF" -eq 1 ]]; then
    hdr "7. Submitting workflow linear-pipeline-1"
    kubectl apply -f examples/tasktemplates.yaml
    kubectl delete workflow linear-pipeline-1 --ignore-not-found
    kubectl apply -f examples/linear-3task.yaml
    info "watching status (timeout 5 min)..."
    for _ in $(seq 1 60); do
        PHASE=$(kubectl get workflow linear-pipeline-1 -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        printf "    phase=%s\r" "${PHASE:-Pending}"
        [[ "$PHASE" == "FINISHED" ]] && break
        [[ "$PHASE" == "FAILED" ]]   && break
        sleep 5
    done
    echo
    if [[ "$PHASE" == "FINISHED" ]]; then
        pass "workflow FINISHED"
        info "pod placement across nodes:"
        kubectl get pods -l ts.io/workflow=linear-pipeline-1 -o wide --no-headers \
            | awk '{printf "    %-30s %s\n", $1, $7}'
    else
        fail "workflow ended in phase=${PHASE:-Pending} (kubectl describe workflow linear-pipeline-1)"
    fi
fi

hdr "Result"
if [[ "$FAILED" -eq 0 ]]; then
    printf "  \033[32mAll checks passed.\033[0m\n"
    exit 0
else
    printf "  \033[31mSome checks failed (see above).\033[0m\n"
    exit 1
fi
