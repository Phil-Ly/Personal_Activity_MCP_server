"""macOS Calendar backend."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.calendar.models import CalendarEnsureRecord, CalendarEventRecord


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

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        is_all_day: bool | None,
        notes: str | None,
        location: str | None,
        timezone: str,
    ) -> CalendarEventRecord:
        payload = self._run_jxa(
            _UPDATE_EVENT_JXA,
            [
                calendar_id,
                event_id,
                title or "",
                start.isoformat() if start is not None else "",
                end.isoformat() if end is not None else "",
                "" if is_all_day is None else ("true" if is_all_day else "false"),
                notes or "",
                location or "",
                timezone,
            ],
        )
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise CalendarBackendError("Calendar update_event returned a non-object payload")
        return _record_from_payload(row)

    def ensure_calendar(
        self, *, calendar_title: str, create_if_missing: bool
    ) -> CalendarEnsureRecord:
        payload = self._run_jxa(
            _ENSURE_CALENDAR_JXA,
            [
                calendar_title,
                "true" if create_if_missing else "false",
            ],
        )
        row = json.loads(payload)
        if not isinstance(row, dict) or not row.get("calendar_id"):
            raise CalendarBackendError("Calendar ensure_calendar returned an invalid payload")
        return CalendarEnsureRecord(
            calendar_id=str(row["calendar_id"]),
            calendar_title=str(row.get("calendar_title") or row["calendar_id"]),
            created=bool(row.get("created")),
        )

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


_UPDATE_EVENT_JXA = r"""
function run(argv) {
  const calendarId = argv[0];
  const eventId = argv[1];
  const title = argv[2] || null;
  const start = argv[3] ? new Date(argv[3]) : null;
  const end = argv[4] ? new Date(argv[4]) : null;
  const allDayRaw = argv[5];
  const notes = argv[6] || null;
  const location = argv[7] || null;
  const Calendar = Application("Calendar");
  const calendar = Calendar.calendars.byName(calendarId);
  const matches = calendar.events.whose({uid: eventId})();
  if (matches.length === 0) {
    throw new Error("Calendar event not found: " + eventId);
  }
  const event = matches[0];
  if (title !== null) {
    event.summary = title;
  }
  if (start !== null) {
    event.startDate = start;
  }
  if (end !== null) {
    event.endDate = end;
  }
  if (allDayRaw !== "") {
    event.alldayEvent = allDayRaw === "true";
  }
  if (notes !== null) {
    event.description = notes;
  }
  if (location !== null) {
    event.location = location;
  }
  return JSON.stringify({
    event_id: event.uid(),
    calendar_id: calendarId,
    title: event.summary(),
    start: event.startDate().toISOString(),
    end: event.endDate().toISOString(),
    is_all_day: event.alldayEvent(),
    location: event.location(),
    notes: event.description()
  });
}
"""


_ENSURE_CALENDAR_JXA = r"""
function run(argv) {
  const calendarTitle = argv[0];
  const createIfMissing = argv[1] === "true";
  const Calendar = Application("Calendar");
  const existing = Calendar.calendars.whose({name: calendarTitle})();
  if (existing.length > 0) {
    return JSON.stringify({
      calendar_id: existing[0].name(),
      calendar_title: existing[0].name(),
      created: false
    });
  }
  if (!createIfMissing) {
    throw new Error("Calendar not found: " + calendarTitle);
  }
  const calendar = Calendar.Calendar({name: calendarTitle});
  Calendar.calendars.push(calendar);
  return JSON.stringify({
    calendar_id: calendar.name(),
    calendar_title: calendar.name(),
    created: true
  });
}
"""
