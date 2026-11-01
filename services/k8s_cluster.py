"""Kubernetes cluster-state polling for the live scheduler.

Free capacity is reservation-based, like kube-scheduler: a node's allocatable
CPU and memory minus the requests of every non-terminal Pod on it. Pods owned
by ``ts-scheduler`` are charged with their *logical* demand (the
``ts.scheduler/logical_*`` annotations used for placement), while other Pods
(node agents, system Pods) are charged with their physical requests.

Polling is split in two so the scheduler can keep API calls outside its state
lock:

* :func:`poll_cluster_inventory` performs the API reads.
* :func:`merge_cluster_state` folds an inventory into the scheduler's
  :class:`ClusterScenario`, updating existing :class:`Node` objects in place so
  reservations registered during a tick are never lost or double counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from kubernetes import client, config

from models.cluster import ClusterScenario, Node
from models.enums import CoolingClass, NodeType
from services.quantities import cpu_cores, memory_mib

SCHEDULER_NAME = "ts-scheduler"
_NODE_TYPE_MAP = {e.name: e for e in NodeType}
_COOLING_CLASS_MAP = {e.name: e for e in CoolingClass}
_BANDWIDTH_FROM_PREFIX = "ts.io/bw-from-"
_BANDWIDTH_TO_PREFIX = "ts.io/bw-to-"
_TERMINAL_PHASES = {"Succeeded", "Failed"}
# Node agents and cluster add-ons run inside each worker's reserved headroom
# (the labelled ts.capacity/* values describe user-allocatable capacity), so
# their requests are not charged against task capacity a second time.
SYSTEM_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease", "ts-system",
    "local-path-storage",
})

_v1: client.CoreV1Api | None = None


def _get_v1() -> client.CoreV1Api:
    global _v1
    if _v1 is None:
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        _v1 = client.CoreV1Api()
    return _v1


# Backward-compatible helpers used by older entry points and tests.
def _parse_cpu(raw) -> float:
    return cpu_cores(raw, 0.0)


def _parse_memory_mi(raw) -> float:
    try:
        return memory_mib(raw, 0.0)
    except ValueError:
        return 0.0


@dataclass
class NodeInventory:
    name: str
    node_type: NodeType
    total_cpu: float
    total_memory: float
    allocatable_cpu: float
    allocatable_memory: float
    cached_images: set
    cooling_class: CoolingClass
    throttle_temp_c: float
    cpu_temperature: Optional[float]
    bandwidth_from: Dict[str, float] = field(default_factory=dict)
    bandwidth_to: Dict[str, float] = field(default_factory=dict)


@dataclass
class PodReservation:
    name: str
    namespace: str
    node_name: str
    cpu: float
    memory: float
    ts_managed: bool


@dataclass
class ClusterInventory:
    nodes: List[NodeInventory]
    pods: List[PodReservation]


def _pod_reservation(pod) -> Optional[PodReservation]:
    phase = pod.status.phase if pod.status else None
    node_name = pod.spec.node_name if pod.spec else None
    if not node_name or phase in _TERMINAL_PHASES:
        return None
    if (pod.metadata.namespace or "") in SYSTEM_NAMESPACES:
        return None
    annotations = pod.metadata.annotations or {}
    ts_managed = (pod.spec.scheduler_name == SCHEDULER_NAME)
    if ts_managed and "ts.scheduler/logical_cpu_request" in annotations:
        cpu = cpu_cores(annotations.get("ts.scheduler/logical_cpu_request"), 0.0)
        memory = memory_mib(
            annotations.get("ts.scheduler/logical_memory_request"), 0.0)
    else:
        cpu = 0.0
        memory = 0.0
        for container in (pod.spec.containers or []):
            requests = {}
            if container.resources and container.resources.requests:
                requests = container.resources.requests
            cpu += cpu_cores(requests.get("cpu"), 0.0)
            memory += memory_mib(requests.get("memory"), 0.0)
    return PodReservation(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace or "",
        node_name=node_name,
        cpu=cpu,
        memory=memory,
        ts_managed=ts_managed,
    )


def poll_cluster_inventory(v1: client.CoreV1Api | None = None) -> ClusterInventory:
    """Read worker nodes (those labelled ``node-type``) and bound Pods."""
    v1 = v1 or _get_v1()
    nodes: List[NodeInventory] = []
    for item in v1.list_node().items:
        labels = item.metadata.labels or {}
        annotations = item.metadata.annotations or {}
        node_type_name = labels.get("node-type")
        if node_type_name is None:
            continue
        total_cpu = float(labels.get("ts.capacity/cpu", "1"))
        total_memory = float(labels.get("ts.capacity/memory", "1024"))
        allocatable = item.status.allocatable or {}
        cooling_label = labels.get(
            "ts.io/cooling-class", labels.get("ts.cooling-class", "STANDARD"))
        try:
            throttle_temp_c = float(labels.get(
                "ts.io/thermal-throttle-c",
                labels.get("ts.thermal-throttle-temp-c", "100")))
        except (TypeError, ValueError):
            throttle_temp_c = 100.0
        try:
            cpu_temperature = float(annotations["ts.io/cpu-temp-c"])
        except (KeyError, TypeError, ValueError):
            cpu_temperature = None
        bandwidth_from: Dict[str, float] = {}
        bandwidth_to: Dict[str, float] = {}
        for key, raw_mib_s in annotations.items():
            if key.startswith(_BANDWIDTH_FROM_PREFIX):
                peer, target = key[len(_BANDWIDTH_FROM_PREFIX):], bandwidth_from
            elif key.startswith(_BANDWIDTH_TO_PREFIX):
                peer, target = key[len(_BANDWIDTH_TO_PREFIX):], bandwidth_to
            else:
                continue
            try:
                mib_s = float(raw_mib_s)
            except (TypeError, ValueError):
                continue
            if peer and mib_s > 0:
                target[peer] = mib_s * 1024 * 1024
        cached_images = {
            name
            for image in (item.status.images or [])
            for name in (image.names or [])
            if "@sha256:" not in name
        }
        nodes.append(NodeInventory(
            name=item.metadata.name,
            node_type=_NODE_TYPE_MAP.get(node_type_name, NodeType.GENERAL),
            total_cpu=total_cpu,
            total_memory=total_memory,
            allocatable_cpu=cpu_cores(allocatable.get("cpu"), total_cpu),
            allocatable_memory=memory_mib(allocatable.get("memory"), total_memory),
            cached_images=cached_images,
            cooling_class=_COOLING_CLASS_MAP.get(
                cooling_label.upper(), CoolingClass.STANDARD),
            throttle_temp_c=throttle_temp_c,
            cpu_temperature=cpu_temperature,
            bandwidth_from=bandwidth_from,
            bandwidth_to=bandwidth_to,
        ))
    pods = [
        reservation for reservation in (
            _pod_reservation(pod) for pod in v1.list_pod_for_all_namespaces().items)
        if reservation is not None
    ]
    return ClusterInventory(nodes=nodes, pods=pods)


def _bandwidth_matrix(nodes: Iterable[NodeInventory]) -> Dict[Tuple[str, str], float]:
    known = {node.name for node in nodes}
    matrix: Dict[Tuple[str, str], float] = {}
    for node in nodes:
        for destination, bytes_per_s in node.bandwidth_to.items():
            if destination in known:
                matrix[(node.name, destination)] = bytes_per_s
    # ts-bw-probe publishes peer-to-self download rates on the receiving node.
    for node in nodes:
        for source, bytes_per_s in node.bandwidth_from.items():
            if source in known:
                matrix[(source, node.name)] = bytes_per_s
    return matrix


def merge_cluster_state(previous: Optional[ClusterScenario],
                        inventory: ClusterInventory,
                        released: Iterable[str] = ()) -> ClusterScenario:
    """Fold a fresh inventory into the scheduler's cluster model.

    Existing :class:`Node` objects are updated in place and keep their
    ``active_tasks`` reservations. Free capacity is recomputed as allocatable
    minus (a) physical requests of Pods not scheduled by ts-scheduler, (b)
    logical requests of ts-scheduler Pods the scheduler is not tracking (for
    example after a scheduler restart), and (c) the tracked reservations.

    ``released`` names Pods whose completion or preemption the scheduler has
    already processed. The inventory is polled outside the scheduler lock, so
    such a Pod can still appear as running in it; charging it as untracked
    would leave a phantom reservation until the next refresh.
    """
    released = set(released)
    previous_nodes = {node.node_id: node for node in (previous.nodes if previous else [])}
    nodes: List[Node] = []
    for info in inventory.nodes:
        node = previous_nodes.get(info.name)
        if node is None:
            node = Node(
                node_id=info.name,
                node_type=info.node_type,
                total_cpu=info.total_cpu,
                total_memory=info.total_memory,
                free_cpu=info.total_cpu,
                free_memory=info.total_memory,
            )
        node.node_type = info.node_type
        node.total_cpu = info.total_cpu
        node.total_memory = info.total_memory
        node.warm_images = set(info.cached_images) | set(node.warm_images)
        node.cooling_class = info.cooling_class
        node.thermal_throttle_temp_c = info.throttle_temp_c
        node.cpu_temperature = info.cpu_temperature

        capacity_cpu = min(info.allocatable_cpu, info.total_cpu)
        capacity_memory = min(info.allocatable_memory, info.total_memory)
        untracked_cpu = 0.0
        untracked_memory = 0.0
        untracked_ts_pods = 0
        for pod in inventory.pods:
            if pod.node_name != info.name:
                continue
            if pod.ts_managed and (
                    pod.name in node.active_tasks or pod.name in released):
                continue
            untracked_cpu += pod.cpu
            untracked_memory += pod.memory
            untracked_ts_pods += int(pod.ts_managed)
        reserved_cpu = sum(task.cpu_request for task in node.active_tasks.values())
        reserved_memory = sum(
            task.memory_request for task in node.active_tasks.values())
        node.free_cpu = capacity_cpu - untracked_cpu - reserved_cpu
        node.free_memory = capacity_memory - untracked_memory - reserved_memory
        node.running_tasks = len(node.active_tasks) + untracked_ts_pods
        nodes.append(node)

    return ClusterScenario(
        scenario_id="live-k8s",
        name="Live Cluster",
        description="Polled from the Kubernetes API",
        nodes=nodes,
        bandwidth_matrix=_bandwidth_matrix(inventory.nodes),
    )


def poll_cluster_state(preserve_warm: ClusterScenario | None = None) -> ClusterScenario:
    """Compatibility wrapper: poll and merge in one call."""
    cluster = merge_cluster_state(preserve_warm, poll_cluster_inventory())
    print(f"[CLUSTER] Polled {len(cluster.nodes)} worker node(s):")
    for node in cluster.nodes:
        print(f"  {node.node_id:20s} ({node.node_type.name:7s})  "
              f"cpu={node.free_cpu:.2f}/{node.total_cpu:.1f}  "
              f"mem={node.free_memory:.0f}/{node.total_memory:.0f} MiB  "
              f"tasks={node.running_tasks}")
    return cluster
