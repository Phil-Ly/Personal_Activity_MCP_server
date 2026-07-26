"""Structured Reminder outputs exposed through MCP."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from personal_activity_mcp.common import TargetRef, ToolWarning


class ReminderTimeRange(BaseModel):
    """Reminder evidence date range."""

    start: AwareDatetime | None
    end: AwareDatetime | None


class ReminderRecord(BaseModel):
    """Raw reminder record returned by a Reminder backend."""

    reminder_id: str
    list_id: str
    title: str
    notes: str | None
    due_date: AwareDatetime | date | None
    priority: int | None
    is_completed: bool
    completion_date: AwareDatetime | None


class ReminderEvidence(BaseModel):
    """Reminder evidence returned through an MCP Tool."""

    evidence_id: str
    source_type: Literal["reminder"] = "reminder"
    source_id: str
    time_range: ReminderTimeRange
    target_ref: TargetRef
    state_token: str
    title: str
    metadata: dict[str, object] = Field(default_factory=dict)
    reminder_id: str
    list_id: str
    notes: str | None
    due_date: AwareDatetime | None
    priority: int | None
    is_completed: bool
    completion_date: AwareDatetime | None
    created_by_mcp: bool
    status_semantics: Literal["planned", "confirmed"]
    source_refs: list[str]


class ReminderListResult(BaseModel):
    """Reminder query result."""

    reminders: list[ReminderEvidence]
    warnings: list[ToolWarning]
    next_cursor: str | None = None


class ReminderCreateResult(BaseModel):
    """Reminder create result."""

    reminder_id: str
    list_id: str
    stable_id: str
    created: bool
    deduplicated: bool
    status_semantics: Literal["planned"]
    source_refs: list[str]


class ReminderCompleteResult(BaseModel):
    """Reminder completion result."""

    reminder_id: str
    is_completed: bool
    completion_date: AwareDatetime
    status_semantics: Literal["confirmed"]
    audit_id: str
