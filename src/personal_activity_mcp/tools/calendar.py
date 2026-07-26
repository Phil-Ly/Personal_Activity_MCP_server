"""Register Calendar MCP Tools."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.calendar import (
    CalendarCreateResult,
    CalendarListResult,
    CalendarRepository,
    CalendarUpdateResult,
)
from personal_activity_mcp.common import ToolOutcome, call_safely


def register_calendar_tools(server: FastMCP, repository: CalendarRepository) -> None:
    """Register Calendar Tools on the provided server."""

    @server.tool(
        name="calendar.list_events",
        description=("List Apple Calendar event evidence from explicitly configured Calendars."),
        structured_output=True,
    )
    def list_calendar_events(
        start: datetime,
        end: datetime,
        calendar_ids: list[str] | None = None,
        include_notes: bool = False,
        include_location: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ToolOutcome[CalendarListResult]:
        return call_safely(
            lambda: repository.list_events(
                calendar_ids=calendar_ids,
                start=start,
                end=end,
                include_notes=include_notes,
                include_location=include_location,
                limit=limit,
                cursor=cursor,
            )
        )

    @server.tool(
        name="calendar.create_event",
        description="Create an Apple Calendar event in an explicitly write-enabled Calendar.",
        structured_output=True,
    )
    def create_calendar_event(
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        is_all_day: bool,
        notes: str | None,
        location: str | None,
        timezone: str,
        source_refs: list[str],
        idempotency_key: str,
    ) -> ToolOutcome[CalendarCreateResult]:
        return call_safely(
            lambda: repository.create_event(
                calendar_id=calendar_id,
                title=title,
                start=start,
                end=end,
                is_all_day=is_all_day,
                notes=notes,
                location=location,
                timezone=timezone,
                source_refs=source_refs,
                idempotency_key=idempotency_key,
            )
        )

    @server.tool(
        name="calendar.update_event",
        description="Update an Apple Calendar event in an explicitly write-enabled Calendar.",
        structured_output=True,
    )
    def update_calendar_event(
        calendar_id: str,
        event_id: str,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        is_all_day: bool | None,
        notes: str | None,
        location: str | None,
        timezone: str,
        source_refs: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ToolOutcome[CalendarUpdateResult]:
        return call_safely(
            lambda: repository.update_event(
                calendar_id=calendar_id,
                event_id=event_id,
                title=title,
                start=start,
                end=end,
                is_all_day=is_all_day,
                notes=notes,
                location=location,
                timezone=timezone,
                source_refs=source_refs,
                confirmed_by_user=confirmed_by_user,
                idempotency_key=idempotency_key,
            )
        )
