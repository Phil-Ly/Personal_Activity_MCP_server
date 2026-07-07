"""Structured Reminder outputs exposed through MCP."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReminderTimeRange(BaseModel):
    """Reminder evidence date range."""

    start: date | None
    end: date | None


class ReminderRecord(BaseModel):
    """Raw reminder record returned by a Reminder backend."""

    reminder_id: str
    list_id: str
    title: str
    notes: str | None
    due_date: date | None
    priority: int | None
    is_completed: bool
    completion_date: datetime | None


class ReminderEvidence(BaseModel):
    """Reminder evidence returned through an MCP Tool."""

    evidence_id: str
    source_type: Literal["reminder"] = "reminder"
    source_id: str
    time_range: ReminderTimeRange
    title: str
    metadata: dict[str, object] = Field(default_factory=dict)
    reminder_id: str
    list_id: str
    notes: str | None
    due_date: date | None
    priority: int | None
    is_completed: bool
    completion_date: datetime | None
    created_by_mcp: bool
    status_semantics: Literal["planned", "confirmed"]
    provenance_ids: list[str]


class ReminderListResult(BaseModel):
    """Reminder query result."""

    reminders: list[ReminderEvidence]
    warnings: list[str]


class ReminderCreateResult(BaseModel):
    """Reminder create result."""

    reminder_id: str
    list_id: str
    stable_id: str
    created: bool
    deduplicated: bool
    status_semantics: Literal["planned"]
    provenance_ids: list[str]


class ReminderCompleteResult(BaseModel):
    """Reminder completion result."""

    reminder_id: str
    is_completed: bool
    completion_date: datetime
    status_semantics: Literal["confirmed"]
    audit_id: str
