"""
ts-thermal — every TS_THERMAL_INTERVAL_S, read the node's CPU temperature
from ``/sys/class/thermal/thermal_zone0/temp`` (millicelsius) and publish
it as a Node annotation ``ts.io/cpu-temp-c``.

On systems without that sysfs file (e.g. macOS hosts running kind), we
emit a synthetic value derived from load average so the rest of the
pipeline can still be exercised end-to-end.

The scheduler's ThermalCollector (services/thermal.py) consumes these
annotations to determine the thermal bucket (cold/warm/hot/throttle).
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


NAMESPACE = os.environ.get("TS_NAMESPACE", "ts-system")
SELF_NODE = os.environ.get("TS_NODE_NAME", socket.gethostname())
INTERVAL_S = float(os.environ.get("TS_THERMAL_INTERVAL_S", "30"))

# Sysfs paths to try in order; first one that exists wins.
SYSFS_CANDIDATES = [
    "/host/sys/class/thermal/thermal_zone0/temp",
    "/sys/class/thermal/thermal_zone0/temp",
    "/host/sys/class/thermal/thermal_zone1/temp",
]


def _read_temp_c() -> Optional[float]:
    """Return °C from sysfs, or None if no thermal zone is readable."""
    for path in SYSFS_CANDIDATES:
        try:
            raw = Path(path).read_text().strip()
            milli = int(raw)
            return milli / 1000.0
        except (FileNotFoundError, ValueError, PermissionError):
            continue
    return None


def _synthetic_temp_c() -> float:
    """Fallback for hosts without sysfs thermals (Mac kind). Mock from loadavg."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = 0.0
    # Map load 0..8 -> 40°C..85°C.
    return max(40.0, min(85.0, 40.0 + load1 * 5.6))


def _load_k8s():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _annotate_self(core: client.CoreV1Api, temp_c: float):
    body = {
        "metadata": {
            "annotations": {
                "ts.io/cpu-temp-c": f"{temp_c:.1f}",
                "ts.io/cpu-temp-updated-at": str(int(time.time())),
            }
        }
    }
    try:
        core.patch_node(name=SELF_NODE, body=body)
    except ApiException as e:
        print(f"[THERM] node patch failed: {e.reason}")


def main():
    print(f"[THERM] ts-thermal started on node={SELF_NODE} interval={INTERVAL_S}s")
    _load_k8s()
    core = client.CoreV1Api()
    while True:
        try:
            t = _read_temp_c()
            source = "sysfs"
            if t is None:
                t = _synthetic_temp_c()
                source = "synthetic"
            print(f"[THERM] {SELF_NODE}: {t:.1f}°C ({source})")
            _annotate_self(core, t)
        except Exception as e:
            print(f"[THERM] iteration failed: {e}")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
