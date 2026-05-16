"""
ts-bw-probe — every TS_PROBE_INTERVAL_S, curl the 100 MB _probe.bin file
from every other node's ts-fileserver and publish the measured bandwidth as
a Kubernetes Event + a node annotation ``ts.io/bw-from-<peer>``.

The scheduler's BandwidthCollector (services/bandwidth.py) consumes these
annotations to refine its bandwidth EMAs, which feed back into ECT.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from typing import Dict, List

from kubernetes import client, config
from kubernetes.client.rest import ApiException


NAMESPACE = os.environ.get("TS_NAMESPACE", "ts-system")
SELF_NODE = os.environ.get("TS_NODE_NAME", socket.gethostname())
INTERVAL_S = float(os.environ.get("TS_PROBE_INTERVAL_S", "600"))
PROBE_PORT = int(os.environ.get("TS_FILESERVER_PORT", "8080"))
PROBE_PATH = os.environ.get("TS_PROBE_PATH", "/_probe.bin")
PROBE_BYTES = int(os.environ.get("TS_PROBE_BYTES", str(100 * 1024 * 1024)))
TIMEOUT_S = float(os.environ.get("TS_PROBE_TIMEOUT_S", "60"))


def _load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _list_peer_nodes(core: client.CoreV1Api) -> List[Dict[str, str]]:
    """Return [{name, internal_ip}, ...] for every node except self."""
    nodes = core.list_node().items
    out: List[Dict[str, str]] = []
    for n in nodes:
        if n.metadata.name == SELF_NODE:
            continue
        ip = None
        for addr in (n.status.addresses or []):
            if addr.type == "InternalIP":
                ip = addr.address
                break
        if ip is None:
            continue
        out.append({"name": n.metadata.name, "ip": ip})
    return out


def _measure_bw(peer_ip: str) -> float:
    """Curl the probe file from a peer and return MB/s, or 0.0 on error."""
    url = f"http://{peer_ip}:{PROBE_PORT}{PROBE_PATH}"
    t0 = time.time()
    bytes_read = 0
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            while True:
                chunk = resp.read(1 << 20)  # 1 MiB
                if not chunk:
                    break
                bytes_read += len(chunk)
    except Exception as e:
        print(f"[BW] {peer_ip}: probe failed: {e}")
        return 0.0
    dt = time.time() - t0
    if dt <= 0 or bytes_read <= 0:
        return 0.0
    mbps = (bytes_read / (1024 * 1024)) / dt
    return mbps


def _annotate_self(core: client.CoreV1Api, results: Dict[str, float]):
    """Patch annotations ts.io/bw-to-<peer>=<MB/s> onto the self Node."""
    annotations = {
        f"ts.io/bw-to-{peer}": f"{mbps:.2f}"
        for peer, mbps in results.items()
    }
    annotations["ts.io/bw-last-probed-at"] = str(int(time.time()))
    body = {"metadata": {"annotations": annotations}}
    try:
        core.patch_node(name=SELF_NODE, body=body)
    except ApiException as e:
        print(f"[BW] node annotation patch failed: {e.reason}")


def main():
    print(f"[BW] ts-bw-probe started on node={SELF_NODE} interval={INTERVAL_S}s")
    _load_k8s()
    core = client.CoreV1Api()
    while True:
        try:
            peers = _list_peer_nodes(core)
            results: Dict[str, float] = {}
            for p in peers:
                mbps = _measure_bw(p["ip"])
                results[p["name"]] = mbps
                print(f"[BW] {SELF_NODE} -> {p['name']}: {mbps:.2f} MB/s")
            if results:
                _annotate_self(core, results)
        except Exception as e:
            print(f"[BW] probe iteration failed: {e}")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
