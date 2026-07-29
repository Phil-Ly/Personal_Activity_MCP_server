from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from personal_activity_mcp.common.eventkit import (
    EventKitClientError,
    EventKitReminderData,
)
from personal_activity_mcp.reminders import MacOSReminderBackend, ReminderBackendError


class RecordingEventKitClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _respond(self, operation: str, arguments: dict[str, object]) -> Any:
        self.calls.append((operation, arguments))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def list_reminders(self, **arguments: object) -> list[EventKitReminderData]:
        return self._respond("list_reminders", arguments)

    def create_reminder(self, **arguments: object) -> EventKitReminderData:
        return self._respond("create_reminder", arguments)

    def complete_reminder(self, **arguments: object) -> EventKitReminderData:
        return self._respond("complete_reminder", arguments)

    def get_reminder(self, **arguments: object) -> EventKitReminderData:
        return self._respond("get_reminder", arguments)


def reminder_data(*, completed: bool = False) -> EventKitReminderData:
    return EventKitReminderData(
        reminder_id="reminder-1",
        list_id="Personal",
        title="Existing reminder",
        notes="Notes",
        due_date=date(2026, 7, 9),
        priority=5,
        is_completed=completed,
        completion_date=(datetime(2026, 7, 9, 12, tzinfo=UTC) if completed else None),
    )


def test_list_reminders_preserves_backend_contract_and_record_shape() -> None:
    client = RecordingEventKitClient([[reminder_data()]])
    backend = MacOSReminderBackend(client=client)
    start_due_at = datetime(2026, 7, 9, tzinfo=UTC)
    end_due_at = datetime(2026, 7, 9, 23, 59, tzinfo=UTC)

    records = backend.list_reminders(
        list_ids=["Personal"],
        start_due_at=start_due_at,
        end_due_at=end_due_at,
        start_completed_at=None,
        end_completed_at=None,
        include_completed=False,
        include_notes=True,
    )

    assert client.calls == [
        (
            "list_reminders",
            {
                "list_ids": ["Personal"],
                "start_due_at": start_due_at,
                "end_due_at": end_due_at,
                "start_completed_at": None,
                "end_completed_at": None,
                "include_completed": False,
                "include_notes": True,
            },
        )
    ]
    assert records[0].reminder_id == "reminder-1"
    assert records[0].list_id == "Personal"
    assert records[0].due_date == date(2026, 7, 9)


def test_create_reminder_passes_due_date_and_priority() -> None:
    client = RecordingEventKitClient([reminder_data()])
    backend = MacOSReminderBackend(client=client)
    due_date = date(2026, 7, 9)

    record = backend.create_reminder(
        list_id="Personal",
        title="Existing reminder",
        notes="Notes",
        due_date=due_date,
        priority=5,
    )

    assert client.calls == [
        (
            "create_reminder",
            {
                "list_id": "Personal",
                "title": "Existing reminder",
                "notes": "Notes",
                "due_date": due_date,
                "priority": 5,
            },
        )
    ]
    assert record.reminder_id == "reminder-1"


def test_complete_reminder_writes_one_exact_list_target() -> None:
    client = RecordingEventKitClient([reminder_data(completed=True)])
    backend = MacOSReminderBackend(client=client)
    completion_date = datetime(2026, 7, 9, 12, tzinfo=UTC)

    record = backend.complete_reminder(
        reminder_id="reminder-1",
        list_id="Personal",
        completion_date=completion_date,
    )

    assert client.calls == [
        (
            "complete_reminder",
            {
                "reminder_id": "reminder-1",
                "list_id": "Personal",
                "completion_date": completion_date,
            },
        )
    ]
    assert record.is_completed is True
    assert record.completion_date == completion_date


def test_get_reminder_reads_one_exact_list_target() -> None:
    client = RecordingEventKitClient([reminder_data()])
    backend = MacOSReminderBackend(client=client)

    record = backend.get_reminder(reminder_id="reminder-1", list_id="Personal")

    assert client.calls == [
        (
            "get_reminder",
            {
                "reminder_id": "reminder-1",
                "list_id": "Personal",
            },
        )
    ]
    assert record.reminder_id == "reminder-1"


def test_eventkit_error_becomes_reminder_backend_error_with_write_state() -> None:
    client = RecordingEventKitClient(
        [
            EventKitClientError(
                "EventKit permission denied",
                external_state_changed=False,
            )
        ]
    )
    backend = MacOSReminderBackend(client=client)

    with pytest.raises(ReminderBackendError, match="permission denied") as captured:
        backend.get_reminder(reminder_id="reminder-1", list_id="Personal")

    assert captured.value.external_state_changed is False
