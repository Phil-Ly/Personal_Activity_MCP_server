"""Structured Activity Log outputs exposed through MCP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ActivityLogCalendarResult(BaseModel):
    """Result for ensuring the dedicated Activity Log Calendar exists."""

    calendar_id: str
    calendar_title: str
    created: bool
    is_default_activity_log: bool


class ActivityRecordResult(BaseModel):
    """Result for recording a confirmed completed action."""

    action_record_id: str
    event_id: str
    stable_id: str
    status_semantics: Literal["confirmed"]
    created: bool
    deduplicated: bool
    source_refs: list[str]
    audit_id: str | None
