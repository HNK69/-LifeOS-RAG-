"""
temporal.py

Deterministic relative-time resolution and temporal window calculations for LifeOS.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _get_reference_time(reference_time: datetime | None = None) -> datetime:
    if reference_time is None:
        return datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        return reference_time.replace(tzinfo=timezone.utc)
    return reference_time


def resolve_relative_time(
    expression: str,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Deterministically resolve relative time expressions into concrete ISO time windows.

    Supported expressions:
    - today, yesterday, tomorrow
    - this morning, this afternoon, this evening, tonight
    - next Monday, next Tuesday, etc.
    - explicit hour windows (e.g., 'between 9 AM and 11 AM', 'from 10:00 to 12:00')
    """
    ref = _get_reference_time(reference_time)
    exp = expression.strip().lower()

    # 1. Today
    if exp in {"today", "now", "current"}:
        start = datetime.combine(ref.date(), time.min, tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time.max, tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # 2. Yesterday
    if exp == "yesterday":
        target = ref.date() - timedelta(days=1)
        start = datetime.combine(target, time.min, tzinfo=ref.tzinfo)
        end = datetime.combine(target, time.max, tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": target.isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # 3. Tomorrow
    if exp == "tomorrow":
        target = ref.date() + timedelta(days=1)
        start = datetime.combine(target, time.min, tzinfo=ref.tzinfo)
        end = datetime.combine(target, time.max, tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": target.isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # 4. Day periods: this morning, afternoon, evening, tonight
    if "morning" in exp:
        start = datetime.combine(ref.date(), time(6, 0), tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time(12, 0), tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }
    if "afternoon" in exp:
        start = datetime.combine(ref.date(), time(12, 0), tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time(17, 0), tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }
    if "evening" in exp:
        start = datetime.combine(ref.date(), time(17, 0), tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time(22, 0), tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }
    if "tonight" in exp:
        start = datetime.combine(ref.date(), time(19, 0), tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time(23, 59, 59), tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # 5. Next <weekday>
    next_match = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", exp)
    if next_match:
        target_weekday = _WEEKDAYS[next_match.group(1)]
        current_weekday = ref.weekday()
        days_ahead = (target_weekday - current_weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        target = ref.date() + timedelta(days=days_ahead)
        start = datetime.combine(target, time.min, tzinfo=ref.tzinfo)
        end = datetime.combine(target, time.max, tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": target.isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # 6. Explicit time windows: "between 9 AM and 11 AM", "from 10:00 to 12:00"
    window_match = re.search(
        r"(?:between|from)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:and|to|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        exp,
    )
    if window_match:
        h1, m1, ap1, h2, m2, ap2 = window_match.groups()
        hour1 = int(h1)
        min1 = int(m1 or 0)
        hour2 = int(h2)
        min2 = int(m2 or 0)

        # Infer AM/PM if only specified for the end time
        if not ap1 and ap2:
            ap1 = ap2

        if ap1 and ap1.lower() == "pm" and hour1 < 12:
            hour1 += 12
        elif ap1 and ap1.lower() == "am" and hour1 == 12:
            hour1 = 0

        if ap2 and ap2.lower() == "pm" and hour2 < 12:
            hour2 += 12
        elif ap2 and ap2.lower() == "am" and hour2 == 12:
            hour2 = 0

        start = datetime.combine(ref.date(), time(hour1, min1), tzinfo=ref.tzinfo)
        end = datetime.combine(ref.date(), time(hour2, min2), tzinfo=ref.tzinfo)
        return {
            "expression": expression,
            "target_date": ref.date().isoformat(),
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "is_relative": True,
        }

    # Fallback to current full day
    start = datetime.combine(ref.date(), time.min, tzinfo=ref.tzinfo)
    end = datetime.combine(ref.date(), time.max, tzinfo=ref.tzinfo)
    return {
        "expression": expression,
        "target_date": ref.date().isoformat(),
        "valid_from": start.isoformat(),
        "valid_until": end.isoformat(),
        "is_relative": False,
    }


def extract_temporal_expression(text: str) -> str | None:
    """Extract recognized relative time expression from user query text."""
    patterns = [
        r"\b(?:between|from)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:and|to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
        r"\bnext\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:today|tomorrow|yesterday|this morning|this afternoon|this evening|tonight)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None
