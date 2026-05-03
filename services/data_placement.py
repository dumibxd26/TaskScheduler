"""
DataPlacement registry — tracks which node holds each output artifact.

Used by the ECT calculator (services/ect.py) to compute transfer costs
and by the K8sBinder to decide whether to emit an initContainer (Channel B)
or a read-only sub-path mount (Channel C).

See ProblemSpecification.md §4.10 and ImplementationArchitecture.md Part V.2.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple


@dataclass
class DataLocation:
    """Where a single (workflow, task, field) output lives."""
    node_id: str
    size_bytes: int
    written_at: float
    replicas: Set[str] = field(default_factory=set)


class DataPlacement:
    """Tracks which node holds each (workflow_id, task_id, field_name)."""

    def __init__(self, ttl_seconds: float = 3600.0):
        self._records: Dict[Tuple[str, str, str], DataLocation] = {}
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def record_output(self, wfid: str, task_id: str, field_name: str,
                      node_id: str, size_bytes: int):
        self._records[(wfid, task_id, field_name)] = DataLocation(
            node_id=node_id, size_bytes=size_bytes, written_at=_time.time(),
        )

    def add_replica(self, wfid: str, task_id: str, field_name: str,
                    node_id: str):
        rec = self._records.get((wfid, task_id, field_name))
        if rec:
            rec.replicas.add(node_id)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------
    def get_producer(self, wfid: str, task_id: str,
                     field_name: str) -> Optional[DataLocation]:
        return self._records.get((wfid, task_id, field_name))

    def is_resident_on(self, wfid: str, task_id: str, field_name: str,
                       node_id: str) -> bool:
        rec = self._records.get((wfid, task_id, field_name))
        if rec is None:
            return False
        return rec.node_id == node_id or node_id in rec.replicas

    def get_output_size(self, wfid: str, task_id: str,
                        field_name: str) -> int:
        """Return recorded size in bytes, or 0 if unknown."""
        rec = self._records.get((wfid, task_id, field_name))
        return rec.size_bytes if rec else 0

    def get_producer_node(self, wfid: str, task_id: str,
                          field_name: str) -> Optional[str]:
        """Return the node_id of the original producer, or None."""
        rec = self._records.get((wfid, task_id, field_name))
        return rec.node_id if rec else None

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------
    def gc_workflow(self, wfid: str):
        """Remove all entries for a finished/failed workflow."""
        self._records = {k: v for k, v in self._records.items()
                         if k[0] != wfid}

    def gc_expired(self, now: Optional[float] = None):
        now = now or _time.time()
        self._records = {
            k: v for k, v in self._records.items()
            if (now - v.written_at) < self._ttl
        }
