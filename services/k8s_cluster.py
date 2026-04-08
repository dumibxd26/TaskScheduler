from __future__ import annotations

"""
Reliable K8s cluster state polling.

Computes actual free resources per node using two strategies:

  1. PREFERRED — Metrics API (metrics-server):
     Reads real CPU/memory usage from cgroups via the K8s Metrics API.
     free = allocatable − actual_usage

  2. FALLBACK — Pod request summation:
     If metrics-server is unavailable, sums the resource requests of all
     non-terminal pods on each node.
     free = allocatable − sum(pod_requests)

Strategy 1 reflects what the node is actually consuming right now.
Strategy 2 reflects what the node has promised to pods (may overestimate usage
if pods request more than they use, or underestimate if pods exceed requests).

Usage:
    from services.k8s_cluster import poll_cluster_state
    cluster = poll_cluster_state()
"""
from kubernetes import client, config
from models.enums import NodeType
from models.cluster import Node, ClusterScenario

_NODE_TYPE_MAP = {e.name: e for e in NodeType}

_v1: client.CoreV1Api | None = None
_custom: client.CustomObjectsApi | None = None


def _get_v1() -> client.CoreV1Api:
    global _v1
    if _v1 is None:
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        _v1 = client.CoreV1Api()
    return _v1


def _get_custom() -> client.CustomObjectsApi:
    global _custom
    if _custom is None:
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        _custom = client.CustomObjectsApi()
    return _custom


def _parse_cpu(raw) -> float:
    """Parse a K8s CPU value ('500m', '2', '100n', etc.) to float cores."""
    if raw is None:
        return 0.0
    s = str(raw)
    if s.endswith("n"):
        return float(s[:-1]) / 1_000_000_000.0
    if s.endswith("u"):
        return float(s[:-1]) / 1_000_000.0
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    return float(s)


def _parse_memory_mi(raw) -> float:
    """Parse a K8s memory value ('128Mi', '2Gi', '3906252Ki', etc.) to MiB."""
    if raw is None:
        return 0.0
    s = str(raw)
    if s.endswith("Ki"):
        return float(s[:-2]) / 1024.0
    if s.endswith("Mi"):
        return float(s[:-2])
    if s.endswith("Gi"):
        return float(s[:-2]) * 1024.0
    # bare bytes
    try:
        return float(s) / (1024.0 * 1024.0)
    except ValueError:
        return 0.0


def _try_get_node_metrics() -> dict[str, tuple[float, float]] | None:
    """
    Query the Metrics API for actual per-node CPU and memory usage.
    Returns {node_name: (used_cpu_cores, used_memory_mib)} or None if
    metrics-server is not available.
    """
    try:
        api = _get_custom()
        result = api.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes",
        )
        usage = {}
        for item in result.get("items", []):
            name = item["metadata"]["name"]
            u = item.get("usage", {})
            usage[name] = (
                _parse_cpu(u.get("cpu")),
                _parse_memory_mi(u.get("memory")),
            )
        return usage
    except Exception:
        return None


def _get_pod_request_sums(v1, node_names: set) -> dict[str, tuple[float, float]]:
    """
    Fallback: sum pod resource requests per node.
    Returns {node_name: (total_requested_cpu, total_requested_mem_mib)}.
    """
    sums: dict[str, list[float]] = {n: [0.0, 0.0] for n in node_names}
    all_pods = v1.list_pod_for_all_namespaces().items
    for pod in all_pods:
        phase = pod.status.phase if pod.status else None
        if phase in ("Succeeded", "Failed", None):
            continue
        node_name = pod.spec.node_name
        if node_name not in sums:
            continue
        for container in (pod.spec.containers or []):
            requests = {}
            if container.resources and container.resources.requests:
                requests = container.resources.requests
            sums[node_name][0] += _parse_cpu(requests.get("cpu"))
            sums[node_name][1] += _parse_memory_mi(requests.get("memory"))
    return {n: (v[0], v[1]) for n, v in sums.items()}


def poll_cluster_state(preserve_warm: ClusterScenario | None = None) -> ClusterScenario:
    """
    Build a ClusterScenario from live K8s state.

    For each worker node (those with a `node-type` label):
      1. Read total capacity from our custom labels.
      2. Read allocatable from node.status.allocatable.
      3. Try Metrics API for actual usage; fall back to pod request sums.
      4. free = allocatable − usage.

    If `preserve_warm` is provided, warm_images from matching node IDs are
    carried over (K8s doesn't track execution-level warmth).
    """
    v1 = _get_v1()

    # --- Step 1: Discover worker nodes ---
    k8s_nodes = v1.list_node().items
    node_info: dict[str, dict] = {}

    for n in k8s_nodes:
        labels = n.metadata.labels or {}
        nt_str = labels.get("node-type")
        if nt_str is None:
            continue

        node_type = _NODE_TYPE_MAP.get(nt_str, NodeType.GENERAL)
        total_cpu = float(labels.get("ts.capacity/cpu", "1"))
        total_mem = float(labels.get("ts.capacity/memory", "1024"))

        alloc = n.status.allocatable or {}
        alloc_cpu = _parse_cpu(alloc.get("cpu", str(total_cpu)))
        alloc_mem = _parse_memory_mi(alloc.get("memory", f"{int(total_mem)}Mi"))

        # Read cached container images directly from the node status
        cached_images: set[str] = set()
        for img in (n.status.images or []):
            for name in (img.names or []):
                # Skip the sha256 digest-only entries
                if "@sha256:" not in name:
                    cached_images.add(name)

        node_info[n.metadata.name] = {
            "node_type": node_type,
            "total_cpu": total_cpu,
            "total_memory": total_mem,
            "alloc_cpu": alloc_cpu,
            "alloc_mem": alloc_mem,
            "cached_images": cached_images,
        }

    # --- Step 2: Get actual usage (prefer metrics-server, fall back to pod requests) ---
    metrics = _try_get_node_metrics()
    if metrics is not None:
        source = "metrics-server (actual usage)"
        usage_by_node = metrics
    else:
        source = "pod-request-sums (fallback)"
        usage_by_node = _get_pod_request_sums(v1, set(node_info.keys()))

    # --- Step 3: Build Node objects ---
    old_by_id = {}
    if preserve_warm is not None:
        old_by_id = {n.node_id: n for n in preserve_warm.nodes}

    nodes = []
    for name, info in node_info.items():
        used_cpu, used_mem = usage_by_node.get(name, (0.0, 0.0))
        free_cpu = max(0.0, info["alloc_cpu"] - used_cpu)
        free_mem = max(0.0, info["alloc_mem"] - used_mem)

        node = Node(
            node_id=name,
            node_type=info["node_type"],
            total_cpu=info["total_cpu"],
            total_memory=info["total_memory"],
            free_cpu=free_cpu,
            free_memory=free_mem,
            warm_images=info["cached_images"],
        )

        # Carry over active_tasks from previous poll if available
        prev = old_by_id.get(name)
        if prev:
            node.active_tasks = prev.active_tasks

        nodes.append(node)

    print(f"[CLUSTER] Polled {len(nodes)} worker node(s) via {source}:")
    for nd in nodes:
        used_c, used_m = usage_by_node.get(nd.node_id, (0.0, 0.0))
        print(f"  {nd.node_id:20s} ({nd.node_type.name:7s})  "
              f"cpu={nd.free_cpu:.2f}/{nd.total_cpu:.1f} (used={used_c:.2f})  "
              f"mem={nd.free_memory:.0f}/{nd.total_memory:.0f} MiB (used={used_m:.0f})")

    return ClusterScenario(
        scenario_id="live-k8s", name="Live Cluster",
        description="Polled from K8s API", nodes=nodes,
    )
