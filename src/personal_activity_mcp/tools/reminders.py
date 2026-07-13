"""Register Reminders MCP Tools."""

from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

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
        start_due_date: date | None = None,
        end_due_date: date | None = None,
        include_completed: bool = False,
        include_notes: bool = False,
    ) -> ReminderListResult:
        return repository.list_reminders(
            list_ids=list_ids,
            start_due_date=start_due_date,
            end_due_date=end_due_date,
            include_completed=include_completed,
            include_notes=include_notes,
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
        provenance_ids: list[str],
        idempotency_key: str,
    ) -> ReminderCreateResult:
        return repository.create_reminder(
            list_id=list_id,
            title=title,
            notes=notes,
            due_date=due_date,
            priority=priority,
            provenance_ids=provenance_ids,
            idempotency_key=idempotency_key,
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
    ) -> ReminderCompleteResult:
        return repository.complete_reminder(
            reminder_id=reminder_id,
            completion_date=completion_date,
            confirmed_by_user=confirmed_by_user,
            idempotency_key=idempotency_key,
            list_id=list_id,
        )
