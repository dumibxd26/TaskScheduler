import pytest

from simulation.core import EventQueue, SimulationClock


def test_virtual_clock_advances_without_wall_clock_waiting():
    clock = SimulationClock()

    assert clock.advance_to(31_536_000) == 31_536_000
    assert clock.now == 31_536_000


def test_virtual_clock_rejects_time_travel():
    clock = SimulationClock(start=10)

    with pytest.raises(ValueError, match="cannot move backwards"):
        clock.advance_to(9)


def test_event_queue_orders_by_time_then_insertion():
    events = EventQueue()
    events.schedule(5, "finish", "first-at-five")
    events.schedule(1, "arrival", "at-one")
    events.schedule(5, "finish", "second-at-five")

    assert events.pop().payload == "at-one"
    assert [event.payload for event in events.drain_at(5)] == [
        "first-at-five",
        "second-at-five",
    ]
    assert len(events) == 0