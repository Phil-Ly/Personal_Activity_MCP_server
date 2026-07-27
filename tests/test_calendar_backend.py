from pathlib import Path

import pytest

from personal_activity_mcp.calendar import (
    CalendarBackendError,
    DescriptionUpdate,
    MacOSCalendarBackend,
)


class RecordingCalendarBackend(MacOSCalendarBackend):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(Path("/usr/bin/false"))
        self.responses = responses
        self.calls: list[list[str]] = []

    def _run_jxa(self, script: str, args: list[str]) -> str:
        self.calls.append(args)
        return self.responses.pop(0)


def event_payload(*, notes: str | None) -> str:
    description = "null" if notes is None else f'"{notes}"'
    return (
        "{"
        '"event_id":"event-1",'
        '"calendar_id":"Personal",'
        '"title":"Existing event",'
        '"start":"2026-07-08T10:00:00+00:00",'
        '"end":"2026-07-08T11:00:00+00:00",'
        '"is_all_day":false,'
        '"location":null,'
        f'"notes":{description}'
        "}"
    )


def test_get_event_reads_one_exact_calendar_target() -> None:
    backend = RecordingCalendarBackend([event_payload(notes="Existing notes")])

    record = backend.get_event(
        event_id="event-1",
        calendar_id="Personal",
    )

    assert backend.calls == [["Personal", "event-1"]]
    assert record.event_id == "event-1"
    assert record.calendar_id == "Personal"
    assert record.notes == "Existing notes"


def test_update_event_passes_explicit_clear_operation() -> None:
    backend = RecordingCalendarBackend([event_payload(notes=None)])

    record = backend.update_event(
        event_id="event-1",
        calendar_id="Personal",
        description=DescriptionUpdate(operation="clear"),
    )

    assert backend.calls == [["Personal", "event-1", "clear", ""]]
    assert record.notes is None


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"event_id":"event-1"}',
        event_payload(notes=None).replace(
            '"is_all_day":false',
            '"is_all_day":"false"',
        ),
        event_payload(notes=None).replace(
            '"title":"Existing event"',
            '"title":123',
        ),
    ],
)
def test_get_event_translates_malformed_payload_to_backend_error(payload: str) -> None:
    backend = RecordingCalendarBackend([payload])

    with pytest.raises(CalendarBackendError, match="invalid payload"):
        backend.get_event(event_id="event-1", calendar_id="Personal")
