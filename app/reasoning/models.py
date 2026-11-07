"""
models.py

Structured reasoning models and result types for LifeOS.
"""
from __future__ import annotations

from typing import Any


class ReasoningResult(str):
    """
    Grounded structured reasoning result.
    Inherits from str to maintain 100% backward compatibility with string-expecting consumers.
    """

    def __new__(
        cls,
        answer: str,
        confidence: float = 0.0,
        evidence_used: list[dict[str, Any]] | None = None,
        facts: list[str] | None = None,
        inferences: list[str] | None = None,
        conflicts_detected: list[dict[str, Any]] | None = None,
    ):
        instance = super().__new__(cls, answer)
        instance.answer = answer
        instance.confidence = confidence
        instance.evidence_used = evidence_used or []
        instance.facts = facts or []
        instance.inferences = inferences or []
        instance.conflicts_detected = conflicts_detected or []
        return instance

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "confidence": self.confidence,
            "evidence_used": self.evidence_used,
            "facts": self.facts,
            "inferences": self.inferences,
            "conflicts_detected": self.conflicts_detected,
        }

    def __repr__(self) -> str:
        return f"ReasoningResult(answer='{self.answer}', confidence={self.confidence}, facts={len(self.facts)}, inferences={len(self.inferences)}, conflicts={len(self.conflicts_detected)})"
