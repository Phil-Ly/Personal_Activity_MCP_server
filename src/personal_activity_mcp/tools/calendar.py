"""Register Calendar MCP Tools."""

from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.calendar import (
    CalendarContainerCreateResult,
    CalendarContainerListResult,
    CalendarContainerRepository,
    CalendarContainerUpdateResult,
    CalendarCreateResult,
    CalendarListResult,
    CalendarRepository,
    CalendarUpdateResult,
    DescriptionUpdate,
)
from personal_activity_mcp.common import TargetRef, ToolOutcome, call_safely


def register_calendar_tools(
    server: FastMCP,
    repository: CalendarRepository,
    container_repository: CalendarContainerRepository | None = None,
) -> None:
    """Register Calendar Tools on the provided server."""

    if container_repository is not None:

        @server.tool(
            name="calendar.list_calendars",
            description="List Calendar containers in authorized EventKit Sources.",
            structured_output=True,
        )
        def list_calendars(
            source_ids: list[str] | None = None,
            title_query: str | None = None,
            modifiable_only: bool = False,
            limit: int = 100,
            cursor: str | None = None,
        ) -> ToolOutcome[CalendarContainerListResult]:
            return call_safely(
                lambda: container_repository.list_calendars(
                    source_ids=source_ids,
                    title_query=title_query,
                    modifiable_only=modifiable_only,
                    limit=limit,
                    cursor=cursor,
                )
            )

        @server.tool(
            name="calendar.create_calendar",
            description="Create a Calendar container in an authorized EventKit Source.",
            structured_output=True,
        )
        def create_calendar(
            title: str,
            idempotency_key: str,
            source_id: str | None = None,
            color: str | None = None,
        ) -> ToolOutcome[CalendarContainerCreateResult]:
            return call_safely(
                lambda: container_repository.create_calendar(
                    title=title,
                    source_id=source_id,
                    color=color,
                    idempotency_key=idempotency_key,
                )
            )

        @server.tool(
            name="calendar.update_calendar",
            description="Update a Calendar container title or color without moving its Source.",
            structured_output=True,
        )
        def update_calendar(
            calendar_id: str,
            idempotency_key: str,
            title: str | None = None,
            color: str | None = None,
            expected_state_token: str | None = None,
        ) -> ToolOutcome[CalendarContainerUpdateResult]:
            return call_safely(
                lambda: container_repository.update_calendar(
                    calendar_id=calendar_id,
                    title=title,
                    color=color,
                    expected_state_token=expected_state_token,
                    idempotency_key=idempotency_key,
                )
            )

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
        target_ref: TargetRef,
        description: DescriptionUpdate | None,
        completion_status: Literal["unknown", "incomplete", "completed"] | None,
        expected_state_token: str | None,
        source_refs: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ToolOutcome[CalendarUpdateResult]:
        return call_safely(
            lambda: repository.update_event(
                target_ref=target_ref,
                description=description,
                completion_status=completion_status,
                expected_state_token=expected_state_token,
                source_refs=source_refs,
                confirmed_by_user=confirmed_by_user,
                idempotency_key=idempotency_key,
            )
        )
