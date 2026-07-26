"""Register Reminders MCP Tools."""

from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.common import ToolOutcome, call_safely
from personal_activity_mcp.reminders import (
    ReminderCompleteResult,
    ReminderCreateResult,
    ReminderListResult,
    ReminderRepository,
)


def register_reminder_tools(server: FastMCP, repository: ReminderRepository) -> None:
    """Register Reminders Tools on the provided server."""

    @server.tool(
        name="reminders.list_reminders",
        description="List Apple Reminders evidence from explicitly configured lists.",
        structured_output=True,
    )
    def list_reminders(
        list_ids: list[str] | None = None,
        start_due_at: datetime | None = None,
        end_due_at: datetime | None = None,
        start_completed_at: datetime | None = None,
        end_completed_at: datetime | None = None,
        include_completed: bool = False,
        include_notes: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ToolOutcome[ReminderListResult]:
        return call_safely(
            lambda: repository.list_reminders(
                list_ids=list_ids,
                start_due_at=start_due_at,
                end_due_at=end_due_at,
                start_completed_at=start_completed_at,
                end_completed_at=end_completed_at,
                include_completed=include_completed,
                include_notes=include_notes,
                limit=limit,
                cursor=cursor,
            )
        )

    @server.tool(
        name="reminders.create_reminder",
        description="Create an Apple Reminder in an explicitly write-enabled list.",
        structured_output=True,
    )
    def create_reminder(
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
        source_refs: list[str],
        idempotency_key: str,
    ) -> ToolOutcome[ReminderCreateResult]:
        return call_safely(
            lambda: repository.create_reminder(
                list_id=list_id,
                title=title,
                notes=notes,
                due_date=due_date,
                priority=priority,
                source_refs=source_refs,
                idempotency_key=idempotency_key,
            )
        )

    @server.tool(
        name="reminders.complete_reminder",
        description="Mark an Apple Reminder completed after explicit user confirmation.",
        structured_output=True,
    )
    def complete_reminder(
        reminder_id: str,
        completion_date: datetime,
        confirmed_by_user: bool,
        idempotency_key: str,
        list_id: str | None = None,
    ) -> ToolOutcome[ReminderCompleteResult]:
        return call_safely(
            lambda: repository.complete_reminder(
                reminder_id=reminder_id,
                completion_date=completion_date,
                confirmed_by_user=confirmed_by_user,
                idempotency_key=idempotency_key,
                list_id=list_id,
            )
        )
