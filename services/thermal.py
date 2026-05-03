"""
ThermalCollector — per-node CPU temperature reader (scheduler-side).

In k8s, temperatures are read from node annotations (populated by a
DaemonSet that reads /sys/class/thermal/thermal_zone0/temp). In simulation
mode, temperatures are injected directly.

See ProblemSpecification.md §2.9 and ImplementationArchitecture.md Part V.7.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from models.cluster import Node, ClusterScenario
from models.enums import CoolingClass


class ThermalCollector:
    """Thread-safe aggregator for per-node temperature readings."""

    def __init__(self):
        self._temps: Dict[str, float] = {}   # node_id -> celsius
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def update_node_temp(self, node_id: str, temp_celsius: float):
        """Record a temperature reading for a node."""
        with self._lock:
            self._temps[node_id] = temp_celsius

    def update_from_annotations(self, annotations: Dict[str, Dict]):
        """
        Bulk-update from k8s node annotations. Expected format per node:
        { "ts-cpu-temp": "72.5" }
        """
        with self._lock:
            for node_id, annot in annotations.items():
                raw = annot.get("ts-cpu-temp")
                if raw is not None:
                    try:
                        self._temps[node_id] = float(raw)
                    except (ValueError, TypeError):
                        pass

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def get_temperature(self, node_id: str) -> Optional[float]:
        with self._lock:
            return self._temps.get(node_id)

    def get_all(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._temps)

    # ------------------------------------------------------------------
    # Push to cluster model
    # ------------------------------------------------------------------
    def apply_to_cluster(self, cluster: ClusterScenario):
        """Stamp current temperatures onto Node objects."""
        with self._lock:
            for node in cluster.nodes:
                temp = self._temps.get(node.node_id)
                if temp is not None:
                    node.cpu_temperature = temp

    # ------------------------------------------------------------------
    # Thermal bucket for contextual profiling (Phase 5)
    # ------------------------------------------------------------------
    @staticmethod
    def thermal_bucket(temp_celsius: Optional[float],
                       cooling: CoolingClass = CoolingClass.STANDARD) -> str:
        """
        Map temperature + cooling class to a discrete bucket label.

        Returns one of: "cold", "warm", "hot", "throttle".
        Used by ProfileStore for bucketed predictions.
        """
        if temp_celsius is None:
            return "warm"  # neutral default

        # Thresholds shift based on cooling capability.
        if cooling in (CoolingClass.HIGH, CoolingClass.EXTREME):
            warm, hot, throttle = 60.0, 80.0, 95.0
        elif cooling == CoolingClass.PASSIVE:
            warm, hot, throttle = 45.0, 60.0, 75.0
        else:  # STANDARD
            warm, hot, throttle = 55.0, 72.0, 85.0

        if temp_celsius >= throttle:
            return "throttle"
        if temp_celsius >= hot:
            return "hot"
        if temp_celsius >= warm:
            return "warm"
        return "cold"
