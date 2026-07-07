"""Register Activity Log MCP Tools."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.activity import (
    ActivityLogCalendarResult,
    ActivityRecordResult,
    ActivityRepository,
)


def register_activity_tools(server: FastMCP, repository: ActivityRepository) -> None:
    """Register Activity Log Tools on the provided server."""

    @server.tool(
        name="activity.ensure_log_calendar",
        description="Ensure the configured Personal Activity Log Calendar exists.",
        structured_output=True,
    )
    def ensure_log_calendar(
        calendar_title: str | None = None,
        create_if_missing: bool = True,
    ) -> ActivityLogCalendarResult:
        return repository.ensure_log_calendar(
            calendar_title=calendar_title,
            create_if_missing=create_if_missing,
        )

    @server.tool(
        name="activity.record_completed_action",
        description="Record a user-confirmed completed action in the Activity Log Calendar.",
        structured_output=True,
    )
    def record_completed_action(
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        is_all_day: bool,
        category: str | None,
        project: str | None,
        notes: str | None,
        location: str | None,
        timezone: str,
        provenance_ids: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ActivityRecordResult:
        return repository.record_completed_action(
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            category=category,
            project=project,
            notes=notes,
            location=location,
            timezone=timezone,
            provenance_ids=provenance_ids,
            confirmed_by_user=confirmed_by_user,
            idempotency_key=idempotency_key,
        )
