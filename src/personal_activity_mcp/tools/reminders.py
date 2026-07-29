"""Register Reminders MCP Tools."""

from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.common import TargetRef, ToolOutcome, call_safely
from personal_activity_mcp.reminders import (
    ReminderCompleteResult,
    ReminderCreateResult,
    ReminderListContainerCreateResult,
    ReminderListContainerListResult,
    ReminderListContainerUpdateResult,
    ReminderListRepository,
    ReminderListResult,
    ReminderRepository,
)


def register_reminder_tools(
    server: FastMCP,
    repository: ReminderRepository,
    list_repository: ReminderListRepository | None = None,
) -> None:
    """Register Reminders Tools on the provided server."""

    if list_repository is not None:

        @server.tool(
            name="reminders.list_lists",
            description="List Reminder List containers in authorized EventKit Sources.",
            structured_output=True,
        )
        def list_lists(
            source_ids: list[str] | None = None,
            title_query: str | None = None,
            modifiable_only: bool = False,
            limit: int = 100,
            cursor: str | None = None,
        ) -> ToolOutcome[ReminderListContainerListResult]:
            return call_safely(
                lambda: list_repository.list_lists(
                    source_ids=source_ids,
                    title_query=title_query,
                    modifiable_only=modifiable_only,
                    limit=limit,
                    cursor=cursor,
                )
            )

        @server.tool(
            name="reminders.create_list",
            description="Create a Reminder List in an authorized EventKit Source.",
            structured_output=True,
        )
        def create_list(
            title: str,
            idempotency_key: str,
            source_id: str | None = None,
            color: str | None = None,
        ) -> ToolOutcome[ReminderListContainerCreateResult]:
            return call_safely(
                lambda: list_repository.create_list(
                    title=title,
                    source_id=source_id,
                    color=color,
                    idempotency_key=idempotency_key,
                )
            )

        @server.tool(
            name="reminders.update_list",
            description="Update a Reminder List title or color without moving its Source.",
            structured_output=True,
        )
        def update_list(
            list_id: str,
            idempotency_key: str,
            title: str | None = None,
            color: str | None = None,
            expected_state_token: str | None = None,
        ) -> ToolOutcome[ReminderListContainerUpdateResult]:
            return call_safely(
                lambda: list_repository.update_list(
                    list_id=list_id,
                    title=title,
                    color=color,
                    expected_state_token=expected_state_token,
                    idempotency_key=idempotency_key,
                )
            )

    @server.tool(
        name="reminders.list_reminders",
        description="List Apple Reminders evidence from lists in authorized EventKit Sources.",
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
        description="Create an Apple Reminder in a write-enabled Reminder List.",
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
        target_ref: TargetRef,
        completion_date: datetime,
        expected_state_token: str | None,
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ToolOutcome[ReminderCompleteResult]:
        return call_safely(
            lambda: repository.complete_reminder(
                target_ref=target_ref,
                completion_date=completion_date,
                expected_state_token=expected_state_token,
                confirmed_by_user=confirmed_by_user,
                idempotency_key=idempotency_key,
            )
        )
