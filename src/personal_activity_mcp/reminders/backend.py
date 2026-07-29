"""macOS Reminders backend implemented through EventKit."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from personal_activity_mcp.common.eventkit import (
    EventKitClient,
    EventKitClientError,
    EventKitReminderData,
)
from personal_activity_mcp.reminders.models import ReminderRecord


class ReminderBackendError(RuntimeError):
    """Raised when EventKit Reminders access fails."""

    def __init__(
        self,
        message: str,
        *,
        external_state_changed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.external_state_changed = external_state_changed


class ReminderEventKitClient(Protocol):
    """EventKit operations consumed by the Reminders backend."""

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_at: datetime | None,
        end_due_at: datetime | None,
        start_completed_at: datetime | None,
        end_completed_at: datetime | None,
        include_completed: bool,
        include_notes: bool,
    ) -> list[EventKitReminderData]: ...

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> EventKitReminderData: ...

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date: datetime,
    ) -> EventKitReminderData: ...

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> EventKitReminderData: ...


class MacOSReminderBackend:
    """Read and write Apple Reminders through a shared EventKit client."""

    def __init__(self, client: ReminderEventKitClient | None = None) -> None:
        self._client = client or EventKitClient()

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_at: datetime | None,
        end_due_at: datetime | None,
        start_completed_at: datetime | None,
        end_completed_at: datetime | None,
        include_completed: bool,
        include_notes: bool,
    ) -> list[ReminderRecord]:
        try:
            records = self._client.list_reminders(
                list_ids=list_ids,
                start_due_at=start_due_at,
                end_due_at=end_due_at,
                start_completed_at=start_completed_at,
                end_completed_at=end_completed_at,
                include_completed=include_completed,
                include_notes=include_notes,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return [_to_reminder_record(record) for record in records]

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> ReminderRecord:
        try:
            record = self._client.create_reminder(
                list_id=list_id,
                title=title,
                notes=notes,
                due_date=due_date,
                priority=priority,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_reminder_record(record)

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date: datetime,
    ) -> ReminderRecord:
        try:
            record = self._client.complete_reminder(
                reminder_id=reminder_id,
                list_id=list_id,
                completion_date=completion_date,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_reminder_record(record)

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> ReminderRecord:
        try:
            record = self._client.get_reminder(
                reminder_id=reminder_id,
                list_id=list_id,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_reminder_record(record)


def _to_reminder_record(record: EventKitReminderData) -> ReminderRecord:
    return ReminderRecord(
        reminder_id=record.reminder_id,
        list_id=record.list_id,
        title=record.title,
        notes=record.notes,
        due_date=record.due_date,
        priority=record.priority,
        is_completed=record.is_completed,
        completion_date=record.completion_date,
    )


def _backend_error(error: EventKitClientError) -> ReminderBackendError:
    return ReminderBackendError(
        str(error),
        external_state_changed=error.external_state_changed,
    )
