"""
vector_clock.py

Vector clock implementation for tracking causality and distributed conflict detection
across offline and peer-to-peer LifeOS nodes.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from app.sync.models import ClockRelationship


class VectorClock:
    """
    Tracks causal histories across multiple peer devices.
    Enables deterministic detection of ancestor, descendant, and concurrent states.
    """

    def __init__(self, clock_dict: Mapping[str, int] | None = None):
        self._clock: Dict[str, int] = {k: int(v) for k, v in (clock_dict or {}).items()}

    def get(self, device_id: str, default: int = 0) -> int:
        return self._clock.get(device_id, default)

    def increment(self, device_id: str, amount: int = 1) -> VectorClock:
        """Increment the logical sequence number for a given device."""
        self._clock[device_id] = self._clock.get(device_id, 0) + amount
        return self

    def set(self, device_id: str, value: int) -> VectorClock:
        self._clock[device_id] = int(value)
        return self

    def compare(self, other: VectorClock | Mapping[str, int]) -> ClockRelationship:
        """
        Compare this vector clock with another.
        - EQUAL: Clocks are identical.
        - ANCESTOR: This clock strictly preceded the other clock (this <= other and this != other).
        - DESCENDANT: This clock strictly succeeded the other clock (other <= this and this != other).
        - CONCURRENT: Neither clock dominates the other (concurrent offline divergence).
        """
        other_dict = other._clock if isinstance(other, VectorClock) else dict(other)
        all_keys = set(self._clock.keys()) | set(other_dict.keys())

        if not all_keys:
            return ClockRelationship.EQUAL

        self_leq_other = True
        other_leq_self = True

        for k in all_keys:
            v_self = self._clock.get(k, 0)
            v_other = other_dict.get(k, 0)

            if v_self > v_other:
                self_leq_other = False
            if v_other > v_self:
                other_leq_self = False

        if self_leq_other and other_leq_self:
            return ClockRelationship.EQUAL
        if self_leq_other:
            return ClockRelationship.ANCESTOR
        if other_leq_self:
            return ClockRelationship.DESCENDANT
        return ClockRelationship.CONCURRENT

    def merge(self, other: VectorClock | Mapping[str, int]) -> VectorClock:
        """Merge with another vector clock by taking the component-wise maximum."""
        other_dict = other._clock if isinstance(other, VectorClock) else dict(other)
        merged = dict(self._clock)
        for k, v in other_dict.items():
            merged[k] = max(merged.get(k, 0), int(v))
        return VectorClock(merged)

    def to_dict(self) -> Dict[str, int]:
        return dict(self._clock)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> VectorClock:
        if not data:
            return cls()
        return cls({str(k): int(v) for k, v in data.items()})

    def copy(self) -> VectorClock:
        return VectorClock(dict(self._clock))

    def __repr__(self) -> str:
        return f"<VectorClock {self._clock}>"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, VectorClock):
            return self.compare(other) == ClockRelationship.EQUAL
        if isinstance(other, dict):
            return self.compare(other) == ClockRelationship.EQUAL
        return False
