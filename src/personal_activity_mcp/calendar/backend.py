"""macOS Calendar backend."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.calendar.models import CalendarEventRecord


class CalendarBackendError(RuntimeError):
    """Raised when Calendar.app automation fails."""


class MacOSCalendarBackend:
    """Read and write Calendar.app through the local osascript bridge."""

    def __init__(self, osascript_path: Path = Path("/usr/bin/osascript")) -> None:
        self._osascript_path = osascript_path

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        payload = self._run_jxa(
            _LIST_EVENTS_JXA,
            [
                "\n".join(calendar_ids),
                start.isoformat(),
                end.isoformat(),
                "true" if include_notes else "false",
                "true" if include_location else "false",
            ],
        )
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise CalendarBackendError("Calendar list_events returned a non-list payload")
        return [_record_from_payload(row) for row in rows]

    def create_event(
        self,
        *,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        is_all_day: bool,
        notes: str | None,
        location: str | None,
        timezone: str,
    ) -> CalendarEventRecord:
        payload = self._run_jxa(
            _CREATE_EVENT_JXA,
            [
                calendar_id,
                title,
                start.isoformat(),
                end.isoformat(),
                "true" if is_all_day else "false",
                notes or "",
                location or "",
                timezone,
            ],
        )
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise CalendarBackendError("Calendar create_event returned a non-object payload")
        return _record_from_payload(row)

    def _run_jxa(self, script: str, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [str(self._osascript_path), "-l", "JavaScript", "-e", script, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CalendarBackendError(f"Unable to run osascript: {error}") from error
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown Calendar error"
            raise CalendarBackendError(f"Calendar automation failed: {message}")
        return result.stdout.strip()


def _record_from_payload(row: Any) -> CalendarEventRecord:
    if not isinstance(row, dict):
        raise CalendarBackendError("Calendar event row must be an object")
    return CalendarEventRecord(
        event_id=str(row["event_id"]),
        calendar_id=str(row["calendar_id"]),
        title=str(row["title"]),
        start=datetime.fromisoformat(str(row["start"])),
        end=datetime.fromisoformat(str(row["end"])),
        is_all_day=bool(row["is_all_day"]),
        location=str(row["location"]) if row.get("location") else None,
        notes=str(row["notes"]) if row.get("notes") else None,
    )


_LIST_EVENTS_JXA = r"""
function run(argv) {
  const calendarIds = argv[0].split("\n").filter(Boolean);
  const rangeStart = new Date(argv[1]);
  const rangeEnd = new Date(argv[2]);
  const includeNotes = argv[3] === "true";
  const includeLocation = argv[4] === "true";
  const Calendar = Application("Calendar");
  const rows = [];
  Calendar.calendars().forEach(function(calendar) {
    const calendarName = calendar.name();
    if (calendarIds.indexOf(calendarName) === -1) {
      return;
    }
    calendar.events().forEach(function(event) {
      const eventStart = event.startDate();
      const eventEnd = event.endDate();
      if (eventStart < rangeEnd && eventEnd > rangeStart) {
        rows.push({
          event_id: event.uid(),
          calendar_id: calendarName,
          title: event.summary(),
          start: eventStart.toISOString(),
          end: eventEnd.toISOString(),
          is_all_day: event.alldayEvent(),
          location: includeLocation ? event.location() : null,
          notes: includeNotes ? event.description() : null
        });
      }
    });
  });
  return JSON.stringify(rows);
}
"""


_CREATE_EVENT_JXA = r"""
function run(argv) {
  const calendarId = argv[0];
  const title = argv[1];
  const start = new Date(argv[2]);
  const end = new Date(argv[3]);
  const isAllDay = argv[4] === "true";
  const notes = argv[5] || null;
  const location = argv[6] || null;
  const Calendar = Application("Calendar");
  const calendar = Calendar.calendars.byName(calendarId);
  const event = Calendar.Event({
    summary: title,
    startDate: start,
    endDate: end,
    alldayEvent: isAllDay,
    description: notes,
    location: location
  });
  calendar.events.push(event);
  return JSON.stringify({
    event_id: event.uid(),
    calendar_id: calendarId,
    title: event.summary(),
    start: event.startDate().toISOString(),
    end: event.endDate().toISOString(),
    is_all_day: event.alldayEvent(),
    location: location,
    notes: notes
  });
}
"""
