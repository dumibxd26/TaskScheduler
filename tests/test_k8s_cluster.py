from types import SimpleNamespace

from models.enums import CoolingClass
from services import k8s_cluster


def k8s_node(name, *, temperature=None, bandwidth_from=None, bandwidth_to=None,
             cooling="STANDARD", throttle="100"):
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
                "ts.capacity/cpu": "4",
                "ts.capacity/memory": "8192",
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


def test_poll_cluster_consumes_thermal_and_bandwidth_annotations(monkeypatch):
    nodes = [
        k8s_node("node-a", temperature=71.5,
                 bandwidth_from={"node-b": 82.25},
                 cooling="PASSIVE", throttle="90"),
        k8s_node("node-b", temperature=43.0,
                 bandwidth_from={"node-a": 95.5}),
    ]
    api = SimpleNamespace(list_node=lambda: SimpleNamespace(items=nodes))
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: api)
    monkeypatch.setattr(
        k8s_cluster,
        "_try_get_node_metrics",
        lambda: {"node-a": (1.0, 512.0), "node-b": (0.5, 256.0)},
    )

    cluster = k8s_cluster.poll_cluster_state()
    by_id = {node.node_id: node for node in cluster.nodes}

    assert by_id["node-a"].cpu_temperature == 71.5
    assert by_id["node-a"].cooling_class == CoolingClass.PASSIVE
    assert by_id["node-a"].thermal_throttle_temp_c == 90
    assert by_id["node-a"].thermal_headroom == 18.5
    assert cluster.get_bandwidth("node-a", "node-b") == 95.5 * 1024 * 1024
    assert cluster.get_bandwidth("node-b", "node-a") == 82.25 * 1024 * 1024


def test_poll_cluster_accepts_explicit_legacy_bandwidth_to(monkeypatch):
    nodes = [
        k8s_node("node-a", bandwidth_to={"node-b": 60.0}),
        k8s_node("node-b"),
    ]
    api = SimpleNamespace(list_node=lambda: SimpleNamespace(items=nodes))
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: api)
    monkeypatch.setattr(
        k8s_cluster,
        "_try_get_node_metrics",
        lambda: {"node-a": (0, 0), "node-b": (0, 0)},
    )

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.get_bandwidth("node-a", "node-b") == 60.0 * 1024 * 1024


def test_poll_cluster_ignores_invalid_telemetry(monkeypatch):
    node = k8s_node("node-a", temperature="invalid",
                    bandwidth_from={"missing": "bad"},
                    cooling="unknown", throttle="invalid")
    api = SimpleNamespace(list_node=lambda: SimpleNamespace(items=[node]))
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: api)
    monkeypatch.setattr(
        k8s_cluster, "_try_get_node_metrics", lambda: {"node-a": (0, 0)})

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
    api = SimpleNamespace(list_node=lambda: SimpleNamespace(items=[node]))
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: api)
    monkeypatch.setattr(
        k8s_cluster, "_try_get_node_metrics", lambda: {"node-a": (0, 0)})

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.nodes[0].cooling_class == CoolingClass.PASSIVE
    assert cluster.nodes[0].thermal_throttle_temp_c == 91


def test_poll_cluster_caps_allocatable_at_declared_capacity(monkeypatch):
    node = k8s_node("node-a")
    node.status.allocatable = {"cpu": "16", "memory": "32768Mi"}
    api = SimpleNamespace(list_node=lambda: SimpleNamespace(items=[node]))
    monkeypatch.setattr(k8s_cluster, "_get_v1", lambda: api)
    monkeypatch.setattr(
        k8s_cluster, "_try_get_node_metrics", lambda: {"node-a": (1, 512)})

    cluster = k8s_cluster.poll_cluster_state()

    assert cluster.nodes[0].free_cpu == 3
    assert cluster.nodes[0].free_memory == 7680