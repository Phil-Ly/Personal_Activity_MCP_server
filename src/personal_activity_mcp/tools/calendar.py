"""Register Calendar MCP Tools."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.calendar import (
    CalendarCreateResult,
    CalendarListResult,
    CalendarRepository,
)


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
    ) -> CalendarListResult:
        return repository.list_events(
            calendar_ids=calendar_ids,
            start=start,
            end=end,
            include_notes=include_notes,
            include_location=include_location,
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
        provenance_ids: list[str],
        idempotency_key: str,
    ) -> CalendarCreateResult:
        return repository.create_event(
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
            timezone=timezone,
            provenance_ids=provenance_ids,
            idempotency_key=idempotency_key,
        )
