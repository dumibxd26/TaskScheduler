"""Deterministic virtual-time primitives used by the simulator."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True, order=True)
class SimulationEvent:
    """An event ordered by virtual timestamp and insertion sequence."""

    time: float
    sequence: int
    kind: str = field(compare=False)
    payload: Any = field(default=None, compare=False)


class SimulationClock:
    """Monotonic clock that advances without waiting in real time."""

    def __init__(self, start: float = 0.0):
        self._now = float(start)

    @property
    def now(self) -> float:
        return self._now

    def advance_to(self, timestamp: float) -> float:
        timestamp = float(timestamp)
        if timestamp < self._now:
            raise ValueError(
                f"simulation time cannot move backwards: {timestamp} < {self._now}"
            )
        self._now = timestamp
        return self._now


class EventQueue:
    """Stable min-priority queue for discrete simulation events."""

    def __init__(self):
        self._events: list[SimulationEvent] = []
        self._next_sequence = 0

    def __len__(self) -> int:
        return len(self._events)

    def schedule(self, time: float, kind: str, payload: Any = None) -> SimulationEvent:
        event = SimulationEvent(
            time=float(time),
            sequence=self._next_sequence,
            kind=kind,
            payload=payload,
        )
        self._next_sequence += 1
        heapq.heappush(self._events, event)
        return event

    def pop(self) -> SimulationEvent:
        if not self._events:
            raise IndexError("pop from empty event queue")
        return heapq.heappop(self._events)

    def peek(self) -> SimulationEvent:
        if not self._events:
            raise IndexError("peek from empty event queue")
        return self._events[0]

    def drain_at(self, timestamp: float) -> Iterator[SimulationEvent]:
        """Yield every event scheduled exactly at ``timestamp`` in stable order."""
        while self._events and self._events[0].time == timestamp:
            yield heapq.heappop(self._events)