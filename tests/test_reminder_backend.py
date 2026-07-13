import os
import subprocess
from pathlib import Path

import pytest

from personal_activity_mcp.reminders import backend as reminder_backend

OSASCRIPT = Path("/usr/bin/osascript")


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
        reminder_backend._COMPLETE_REMINDER_JXA,
    ):
        assert "formatLocalDate(" in script
        assert "toISOString().slice(0, 10)" not in script
