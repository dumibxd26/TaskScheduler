"""
Bandwidth collector — scheduler-side aggregator for the bandwidth matrix.

Polls the bandwidth-probe DaemonSet's exported metrics (or reads inline
observations from initContainer transfer durations) and builds
``ClusterScenario.bandwidth_matrix``.

Until Phase 1 ships the probe DaemonSet, this module provides a no-op
collector that returns the uniform default. The matrix is progressively
refined by real transfer observations (see ``refine_from_transfer``).

See ProblemSpecification.md §2.5 and ImplementationArchitecture.md Part VII.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

from models.cluster import ClusterScenario


# EMA weight for blending a new observation into the matrix.
_REFINE_ALPHA = 0.1


class BandwidthCollector:
    """Thread-safe bandwidth matrix manager."""

    def __init__(self, default_bw: float = 100.0 * 1024 * 1024):
        self._matrix: Dict[Tuple[str, str], float] = {}
        self._default_bw = default_bw
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Bulk update from probe results (Phase 2 DaemonSet)
    # ------------------------------------------------------------------
    def update_from_probe(self, probed: Dict[Tuple[str, str], float]):
        """Replace entries with fresh probe data (called periodically)."""
        with self._lock:
            self._matrix.update(probed)

    # ------------------------------------------------------------------
    # Online refinement from a real initContainer transfer
    # ------------------------------------------------------------------
    def refine_from_transfer(self, producer_node: str, consumer_node: str,
                             bytes_transferred: int,
                             duration_s: float):
        """Blend an observed transfer into the matrix via EMA."""
        if duration_s <= 0 or bytes_transferred <= 0:
            return
        observed_bw = bytes_transferred / duration_s
        key = (producer_node, consumer_node)
        with self._lock:
            old = self._matrix.get(key, self._default_bw)
            self._matrix[key] = (
                (1.0 - _REFINE_ALPHA) * old + _REFINE_ALPHA * observed_bw
            )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------
    def get_matrix(self) -> Dict[Tuple[str, str], float]:
        """Return a snapshot of the current matrix (safe to pass around)."""
        with self._lock:
            return dict(self._matrix)

    def apply_to_cluster(self, cluster: ClusterScenario):
        """Copy the current matrix into the cluster's bandwidth_matrix."""
        with self._lock:
            cluster.bandwidth_matrix = dict(self._matrix)
