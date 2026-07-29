from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from personal_activity_mcp.calendar import (
    CalendarBackendError,
    DescriptionUpdate,
    MacOSCalendarBackend,
)
from personal_activity_mcp.common.eventkit import (
    EventKitClientError,
    EventKitEventData,
)


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

    def list_events(self, **arguments: object) -> list[EventKitEventData]:
        return self._respond("list_events", arguments)

    def create_event(self, **arguments: object) -> EventKitEventData:
        return self._respond("create_event", arguments)

    def update_event_notes(self, **arguments: object) -> EventKitEventData:
        return self._respond("update_event_notes", arguments)

    def get_event(self, **arguments: object) -> EventKitEventData:
        return self._respond("get_event", arguments)


def event_data(*, notes: str | None = "Existing notes") -> EventKitEventData:
    return EventKitEventData(
        event_id="event-1",
        calendar_id="Personal",
        title="Existing event",
        start=datetime(2026, 7, 8, 10, tzinfo=UTC),
        end=datetime(2026, 7, 8, 11, tzinfo=UTC),
        is_all_day=False,
        start_date=None,
        end_date=None,
        location="Office",
        notes=notes,
    )


def test_list_events_preserves_backend_contract_and_record_shape() -> None:
    client = RecordingEventKitClient([[event_data()]])
    backend = MacOSCalendarBackend(client=client)
    start = datetime(2026, 7, 8, tzinfo=UTC)
    end = datetime(2026, 7, 9, tzinfo=UTC)

    records = backend.list_events(
        calendar_ids=["Personal"],
        start=start,
        end=end,
        include_notes=True,
        include_location=False,
    )

    assert client.calls == [
        (
            "list_events",
            {
                "calendar_ids": ["Personal"],
                "start": start,
                "end": end,
                "include_notes": True,
                "include_location": False,
            },
        )
    ]
    assert records[0].event_id == "event-1"
    assert records[0].calendar_id == "Personal"
    assert records[0].notes == "Existing notes"


def test_create_event_passes_all_native_write_fields() -> None:
    client = RecordingEventKitClient([event_data()])
    backend = MacOSCalendarBackend(client=client)
    start = datetime(2026, 7, 8, 10, tzinfo=UTC)
    end = datetime(2026, 7, 8, 11, tzinfo=UTC)

    record = backend.create_event(
        calendar_id="Personal",
        title="Existing event",
        start=start,
        end=end,
        is_all_day=False,
        notes="Existing notes",
        location="Office",
        timezone="UTC",
    )

    assert client.calls == [
        (
            "create_event",
            {
                "calendar_id": "Personal",
                "title": "Existing event",
                "start": start,
                "end": end,
                "is_all_day": False,
                "notes": "Existing notes",
                "location": "Office",
                "timezone": "UTC",
            },
        )
    ]
    assert record.event_id == "event-1"


def test_update_event_translates_explicit_clear_to_none() -> None:
    client = RecordingEventKitClient([event_data(notes=None)])
    backend = MacOSCalendarBackend(client=client)

    record = backend.update_event(
        event_id="event-1",
        calendar_id="Personal",
        description=DescriptionUpdate(operation="clear"),
    )

    assert client.calls == [
        (
            "update_event_notes",
            {
                "event_id": "event-1",
                "calendar_id": "Personal",
                "notes": None,
            },
        )
    ]
    assert record.notes is None


def test_get_event_reads_one_exact_calendar_target() -> None:
    client = RecordingEventKitClient([event_data()])
    backend = MacOSCalendarBackend(client=client)

    record = backend.get_event(event_id="event-1", calendar_id="Personal")

    assert client.calls == [
        (
            "get_event",
            {
                "event_id": "event-1",
                "calendar_id": "Personal",
            },
        )
    ]
    assert record.event_id == "event-1"


def test_eventkit_error_becomes_calendar_backend_error_with_write_state() -> None:
    client = RecordingEventKitClient(
        [
            EventKitClientError(
                "EventKit permission denied",
                external_state_changed=False,
            )
        ]
    )
    backend = MacOSCalendarBackend(client=client)

    with pytest.raises(CalendarBackendError, match="permission denied") as captured:
        backend.get_event(event_id="event-1", calendar_id="Personal")

    assert captured.value.external_state_changed is False
