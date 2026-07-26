"""Structured Calendar outputs exposed through MCP."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from personal_activity_mcp.common import TargetRef, ToolWarning


class TimedEventRange(BaseModel):
    """A Calendar event with exact instants."""

    kind: Literal["timed"] = "timed"
    start: AwareDatetime
    end: AwareDatetime


class AllDayEventRange(BaseModel):
    """A Calendar all-day event expressed in local calendar dates."""

    kind: Literal["all_day"] = "all_day"
    start_date: date
    end_date: date


CalendarTimeRange = Annotated[
    TimedEventRange | AllDayEventRange,
    Field(discriminator="kind"),
]


class DescriptionUpdate(BaseModel):
    """Explicit set-or-clear patch for Apple Calendar notes."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["set", "clear"]
    value: str | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> DescriptionUpdate:
        if self.operation == "set" and (self.value is None or not self.value.strip()):
            raise ValueError("description set requires a non-empty value")
        if self.operation == "clear" and self.value is not None:
            raise ValueError("description clear does not accept a value")
        return self


class CalendarEventRecord(BaseModel):
    """Raw event record returned by a Calendar backend."""

    event_id: str
    calendar_id: str
    title: str
    start: AwareDatetime
    end: AwareDatetime
    is_all_day: bool
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    notes: str | None = None


class CalendarEventEvidence(BaseModel):
    """Calendar event evidence returned through an MCP Tool."""

    evidence_id: str
    source_type: Literal["calendar"] = "calendar"
    source_id: str
    time_range: CalendarTimeRange
    target_ref: TargetRef
    state_token: str
    title: str
    metadata: dict[str, object] = Field(default_factory=dict)
    event_id: str
    calendar_id: str
    start: AwareDatetime
    end: AwareDatetime
    is_all_day: bool
    location: str | None
    notes: str | None
    created_by_mcp: bool
    status_semantics: Literal["planned", "probable", "confirmed"]
    completion_status: Literal["unknown", "incomplete", "completed"]
    source_refs: list[str]


class CalendarListResult(BaseModel):
    """Calendar query result."""

    events: list[CalendarEventEvidence]
    warnings: list[ToolWarning]
    next_cursor: str | None = None


class CalendarCreateResult(BaseModel):
    """Calendar create-event result."""

    event_id: str
    calendar_id: str
    stable_id: str
    created: bool
    deduplicated: bool
    status_semantics: Literal["planned", "probable"]
    source_refs: list[str]


class CalendarUpdateResult(BaseModel):
    """Calendar update-event result."""

    event_id: str
    calendar_id: str
    stable_id: str
    updated: bool
    deduplicated: bool
    updated_fields: list[str]
    status_semantics: Literal["planned", "probable", "confirmed"]
    completion_status: Literal["unknown", "incomplete", "completed"]
    source_refs: list[str]
    audit_id: str | None
