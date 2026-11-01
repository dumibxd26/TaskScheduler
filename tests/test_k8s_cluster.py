from types import SimpleNamespace

from models.cluster import ClusterScenario, Node
from models.enums import CoolingClass, NodeType
from services import k8s_cluster


def k8s_node(name, *, temperature=None, bandwidth_from=None, bandwidth_to=None,
             cooling="STANDARD", throttle="100", cpu="4", memory="8192"):
    annotations = {}
    if temperature is not None:
        annotations["ts.io/cpu-temp-c"] = str(temperature)
    for peer, mib_s in (bandwidth_from or {}).items():
        annotations[f"ts.io/bw-from-{peer}"] = str(mib_s)
    for peer, mib_s in (bandwidth_to or {}).items():
        annotations[f"ts.io/bw-to-{peer}"] = str(mib_s)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels={
                "node-type": "GENERAL",
                "ts.capacity/cpu": cpu,
                "ts.capacity/memory": memory,
                "ts.io/cooling-class": cooling,
                "ts.io/thermal-throttle-c": throttle,
            },
            annotations=annotations,
        ),
        status=SimpleNamespace(
            allocatable={"cpu": "4", "memory": "8192Mi"},
            images=[],
        ),
    )


def k8s_pod(name, node, *, scheduler="ts-scheduler", namespace="default",
            phase="Running", physical=("250m", "128Mi"), logical=None):
    annotations = {}
    if logical is not None:
        annotations["ts.scheduler/logical_cpu_request"] = logical[0]
        annotations["ts.scheduler/logical_memory_request"] = logical[1]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, namespace=namespace, annotations=annotations),
        spec=SimpleNamespace(
            node_name=node,
            scheduler_name=scheduler,
            containers=[SimpleNamespace(resources=SimpleNamespace(
                requests={"cpu": physical[0], "memory": physical[1]}))],
        ),
        status=SimpleNamespace(phase=phase),
    )


def fake_api(nodes, pods=()):
    return SimpleNamespace(
        list_node=lambda: SimpleNamespace(items=list(nodes)),
        list_pod_for_all_namespaces=lambda: SimpleNamespace(items=list(pods)),
    )


def test_poll_cluster_consumes_thermal_and_bandwidth_annotations(monkeypatch):
    nodes = [
        k8s_node("node-a", temperature=71.5,
                 bandwidth_from={"node-b": 82.25},
                 cooling="PASSIVE", throttle="90"),
        k8s_node("node-b", temperature=43.0,
                 bandwidth_from={"node-a": 95.5}),
    ]
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api(nodes))

    cluster = k8s_cluster.poll_cluster_state()
    by_id = {node.node_id: node for node in cluster.nodes}

    assert by_id["node-a"].cpu_temperature == 71.5
    assert by_id["node-a"].cooling_class == CoolingClass.PASSIVE
    assert by_id["node-a"].thermal_throttle_temp_c == 90
    assert by_id["node-a"].thermal_headroom == 18.5
    assert cluster.get_bandwidth("node-a", "node-b") == 95.5 * 1024 * 1024
    assert cluster.get_bandwidth("node-b", "node-a") == 82.25 * 1024 * 1024


def test_poll_cluster_accepts_explicit_legacy_bandwidth_to(monkeypatch):
    nodes = [k8s_node("node-a", bandwidth_to={"node-b": 60.0}), k8s_node("node-b")]
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api(nodes))

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.get_bandwidth("node-a", "node-b") == 60.0 * 1024 * 1024


def test_poll_cluster_ignores_invalid_telemetry(monkeypatch):
    node = k8s_node("node-a", temperature="invalid",
                    bandwidth_from={"missing": "bad"},
                    cooling="unknown", throttle="invalid")
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api([node]))

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.nodes[0].cpu_temperature is None
    assert cluster.nodes[0].cooling_class == CoolingClass.STANDARD
    assert cluster.nodes[0].thermal_throttle_temp_c == 100
    assert cluster.bandwidth_matrix == {}


def test_poll_cluster_accepts_legacy_thermal_labels(monkeypatch):
    node = k8s_node("node-a")
    node.metadata.labels.pop("ts.io/cooling-class")
    node.metadata.labels.pop("ts.io/thermal-throttle-c")
    node.metadata.labels["ts.cooling-class"] = "PASSIVE"
    node.metadata.labels["ts.thermal-throttle-temp-c"] = "91"
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api([node]))

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.nodes[0].cooling_class == CoolingClass.PASSIVE
    assert cluster.nodes[0].thermal_throttle_temp_c == 91


def test_free_capacity_charges_logical_ts_pods_and_physical_other_pods(monkeypatch):
    node = k8s_node("node-a", cpu="2", memory="2048")
    node.status.allocatable = {"cpu": "16", "memory": "32768Mi"}
    pods = [
        # ts-scheduler task: charged with its logical annotation (0.5/384Mi).
        k8s_pod("task-1", "node-a", logical=("0.5", "384Mi")),
        # A user Pod from another scheduler: charged physically.
        k8s_pod("other", "node-a", scheduler="default-scheduler",
                physical=("100m", "64Mi")),
        # Node agents live in the reserved headroom and are not charged.
        k8s_pod("kindnet", "node-a", scheduler="default-scheduler",
                namespace="kube-system", physical=("100m", "50Mi")),
        # Terminal Pods release their reservation.
        k8s_pod("done", "node-a", phase="Succeeded", logical=("0.5", "384Mi")),
    ]
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api([node], pods))

    cluster = k8s_cluster.poll_cluster_state()
    state = cluster.nodes[0]

    assert state.free_cpu == 2 - 0.5 - 0.1
    assert state.free_memory == 2048 - 384 - 64
    assert state.running_tasks == 1


def test_refresh_preserves_reservations_without_double_counting():
    previous_node = Node(
        node_id="node-a", node_type=NodeType.GENERAL, total_cpu=2,
        total_memory=2048, free_cpu=2, free_memory=2048)
    previous_node.register_task(
        "template", "task-1", cpu_request=0.5, memory_request=384)
    previous = ClusterScenario("s", "s", "", nodes=[previous_node])
    inventory = k8s_cluster.ClusterInventory(
        nodes=[k8s_cluster.NodeInventory(
            name="node-a", node_type=NodeType.GENERAL, total_cpu=2,
            total_memory=2048, allocatable_cpu=8, allocatable_memory=6000,
            cached_images=set(), cooling_class=CoolingClass.STANDARD,
            throttle_temp_c=100, cpu_temperature=None)],
        pods=[k8s_cluster.PodReservation(
            name="task-1", namespace="default", node_name="node-a",
            cpu=0.5, memory=384, ts_managed=True)],
    )

    merged = k8s_cluster.merge_cluster_state(previous, inventory)
    node = merged.nodes[0]

    # Same object, reservation kept, and the bound Pod is not charged twice.
    assert node is previous_node
    assert list(node.active_tasks) == ["task-1"]
    assert node.free_cpu == 1.5
    assert node.free_memory == 2048 - 384
    assert node.running_tasks == 1
    node.unregister_task("task-1")
    assert node.free_cpu == 2.0


def test_completion_between_poll_and_merge_leaves_no_phantom_reservation():
    node = Node(
        node_id="node-a", node_type=NodeType.GENERAL, total_cpu=2,
        total_memory=2048, free_cpu=2, free_memory=2048)
    node.register_task("template", "task-1", cpu_request=0.5, memory_request=384)
    previous = ClusterScenario("s", "s", "", nodes=[node])
    # The inventory was polled while task-1 was still running ...
    inventory = k8s_cluster.ClusterInventory(
        nodes=[k8s_cluster.NodeInventory(
            name="node-a", node_type=NodeType.GENERAL, total_cpu=2,
            total_memory=2048, allocatable_cpu=8, allocatable_memory=6000,
            cached_images=set(), cooling_class=CoolingClass.STANDARD,
            throttle_temp_c=100, cpu_temperature=None)],
        pods=[k8s_cluster.PodReservation(
            name="task-1", namespace="default", node_name="node-a",
            cpu=0.5, memory=384, ts_managed=True)],
    )
    # ... and the scheduler processed its completion before the merge.
    node.unregister_task("task-1")

    # Without knowing the Pod was released, the finished Pod is charged as
    # untracked and the node looks busy until the next refresh.
    stale = k8s_cluster.merge_cluster_state(previous, inventory)
    assert stale.nodes[0].free_cpu == 1.5

    merged = k8s_cluster.merge_cluster_state(
        previous, inventory, released={"task-1"})
    assert merged.nodes[0].free_cpu == 2.0
    assert merged.nodes[0].free_memory == 2048
    assert merged.nodes[0].running_tasks == 0


def test_poll_cluster_caps_allocatable_at_declared_capacity(monkeypatch):
    node = k8s_node("node-a", cpu="3", memory="7680")
    node.status.allocatable = {"cpu": "16", "memory": "32768Mi"}
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: fake_api([node]))

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.nodes[0].free_cpu == 3
    assert cluster.nodes[0].free_memory == 7680


def test_quantity_parser_supports_decimal_binary_and_exponent_forms():
    from services.quantities import cpu_cores, memory_mib

    assert cpu_cores("500m") == 0.5
    assert cpu_cores("2") == 2.0
    assert memory_mib("1Gi") == 1024
    assert memory_mib("1G") == 1e9 / 2 ** 20
    assert memory_mib("512M") == 512e6 / 2 ** 20
    assert memory_mib("1e9") == 1e9 / 2 ** 20
    assert memory_mib("128974848") == 128974848 / 2 ** 20
