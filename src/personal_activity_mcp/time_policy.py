"""Shared clock and datetime validation policies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provide the current time for time-dependent domain decisions."""

    def now(self) -> datetime: ...


class SystemClock:
    """Read the host clock as a timezone-aware UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def require_aware_datetime(value: datetime, field_name: str) -> None:
    """Reject datetimes whose offset cannot be determined."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")


def normalize_external_datetime(value: datetime) -> datetime:
    """Normalize external datetimes to millisecond precision before writes."""
    return value.replace(microsecond=(value.microsecond // 1_000) * 1_000)
