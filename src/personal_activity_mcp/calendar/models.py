"""Structured Calendar outputs exposed through MCP."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CalendarTimeRange(BaseModel):
    """Calendar event time range."""

    start: datetime
    end: datetime


class CalendarEventRecord(BaseModel):
    """Raw event record returned by a Calendar backend."""

    event_id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    is_all_day: bool
    location: str | None = None
    notes: str | None = None


class CalendarEnsureRecord(BaseModel):
    """Raw Calendar ensure result returned by a Calendar backend."""

    calendar_id: str
    calendar_title: str
    created: bool


class CalendarEventEvidence(BaseModel):
    """Calendar event evidence returned through an MCP Tool."""

    evidence_id: str
    source_type: Literal["calendar"] = "calendar"
    source_id: str
    time_range: CalendarTimeRange
    title: str
    metadata: dict[str, object] = Field(default_factory=dict)
    event_id: str
    calendar_id: str
    start: datetime
    end: datetime
    is_all_day: bool
    location: str | None
    notes: str | None
    created_by_mcp: bool
    status_semantics: Literal["planned", "probable", "confirmed"]
    source_refs: list[str]


class CalendarListResult(BaseModel):
    """Calendar query result."""

    events: list[CalendarEventEvidence]
    warnings: list[str]


class CalendarCreateResult(BaseModel):
    """Calendar create-event result."""

    event_id: str
    calendar_id: str
    stable_id: str
    created: bool
    deduplicated: bool
    status_semantics: Literal["planned"]
    source_refs: list[str]


class CalendarUpdateResult(BaseModel):
    """Calendar update-event result."""

    event_id: str
    calendar_id: str
    stable_id: str
    updated: bool
    deduplicated: bool
    updated_fields: list[str]
    requires_user_confirmation: bool
    status_semantics: Literal["planned", "probable", "confirmed"]
    source_refs: list[str]
    audit_id: str | None
