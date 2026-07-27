import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_activity_mcp.reminders import MacOSReminderBackend, ReminderBackendError
from personal_activity_mcp.reminders import backend as reminder_backend

OSASCRIPT = Path("/usr/bin/osascript")


class RecordingReminderBackend(MacOSReminderBackend):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(Path("/usr/bin/false"))
        self.responses = responses
        self.calls: list[list[str]] = []

    def _run_jxa(self, script: str, args: list[str]) -> str:
        self.calls.append(args)
        return self.responses.pop(0)


def reminder_payload(*, completed: bool) -> str:
    completion_date = '"2026-07-09T12:00:00+00:00"' if completed else "null"
    return (
        "{"
        '"reminder_id":"reminder-1",'
        '"list_id":"Personal",'
        '"title":"Existing reminder",'
        '"notes":null,'
        '"due_date":"2026-07-09T00:00:00+00:00",'
        '"priority":5,'
        f'"is_completed":{str(completed).lower()},'
        f'"completion_date":{completion_date}'
        "}"
    )


def test_get_reminder_reads_one_exact_list_target() -> None:
    backend = RecordingReminderBackend([reminder_payload(completed=False)])

    record = backend.get_reminder(
        reminder_id="reminder-1",
        list_id="Personal",
    )

    assert backend.calls == [["Personal", "reminder-1"]]
    assert record.reminder_id == "reminder-1"
    assert record.list_id == "Personal"
    assert record.is_completed is False


def test_complete_reminder_writes_one_exact_list_target() -> None:
    backend = RecordingReminderBackend([reminder_payload(completed=True)])

    record = backend.complete_reminder(
        reminder_id="reminder-1",
        list_id="Personal",
        completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
    )

    assert backend.calls == [["Personal", "reminder-1", "2026-07-09T12:00:00+00:00"]]
    assert record.is_completed is True


@pytest.mark.skipif(not OSASCRIPT.is_file(), reason="osascript is only available on macOS")
@pytest.mark.parametrize("timezone", ["America/Los_Angeles", "UTC", "Asia/Shanghai"])
def test_local_date_formatter_preserves_calendar_date(timezone: str) -> None:
    formatter = getattr(reminder_backend, "_LOCAL_DATE_FORMATTER_JXA", "")
    assert formatter, "Reminder JXA must define a shared local date formatter"
    script = (
        formatter
        + '\nfunction run(argv) { return formatLocalDate(new Date(argv[0] + "T00:00:00")); }'
    )

    result = subprocess.run(
        [str(OSASCRIPT), "-l", "JavaScript", "-e", script, "2026-07-09"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": timezone},
    )

    assert result.stdout.strip() == "2026-07-09"


def test_reminder_jxa_uses_local_formatter_for_due_dates() -> None:
    for script in (
        reminder_backend._LIST_REMINDERS_JXA,
        reminder_backend._CREATE_REMINDER_JXA,
        reminder_backend._GET_REMINDER_JXA,
        reminder_backend._COMPLETE_REMINDER_JXA,
    ):
        assert "formatLocalDate(" in script
        assert "toISOString().slice(0, 10)" not in script


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"reminder_id":"reminder-1"}',
        reminder_payload(completed=False).replace(
            '"is_completed":false',
            '"is_completed":"false"',
        ),
        reminder_payload(completed=False).replace(
            '"title":"Existing reminder"',
            '"title":123',
        ),
    ],
)
def test_get_reminder_translates_malformed_payload_to_backend_error(payload: str) -> None:
    backend = RecordingReminderBackend([payload])

    with pytest.raises(ReminderBackendError, match="invalid payload"):
        backend.get_reminder(reminder_id="reminder-1", list_id="Personal")
