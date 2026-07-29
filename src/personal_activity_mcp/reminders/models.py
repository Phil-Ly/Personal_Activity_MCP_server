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


class ReminderListContainerRecord(BaseModel):
    """Raw Reminder List container returned by a backend."""

    list_id: str
    source_id: str
    source_title: str
    title: str
    color: str | None
    calendar_type: Literal[
        "local",
        "caldav",
        "exchange",
        "subscription",
        "birthday",
        "unknown",
    ]
    allows_content_modifications: bool
    is_immutable: bool
    is_subscribed: bool


class ReminderListContainer(ReminderListContainerRecord):
    """Reminder List container returned through an MCP Tool."""

    state_token: str
    created_by_mcp: bool


class ReminderListContainerListResult(BaseModel):
    """Paginated Reminder List container query result."""

    lists: list[ReminderListContainer]
    next_cursor: str | None = None


class ReminderListContainerCreateResult(BaseModel):
    """Reminder List container create result."""

    list: ReminderListContainer
    created: bool
    deduplicated: bool
    audit_id: str


class ReminderListContainerUpdateResult(BaseModel):
    """Reminder List container update result."""

    list: ReminderListContainer
    updated: bool
    deduplicated: bool
    updated_fields: list[Literal["title", "color"]]
    audit_id: str


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
    list_id: str
    stable_id: str
    is_completed: bool
    completion_date: AwareDatetime
    status_semantics: Literal["confirmed"]
    deduplicated: bool
    audit_id: str
