"""macOS Calendar backend."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.calendar.models import CalendarEventRecord, DescriptionUpdate


class CalendarBackendError(RuntimeError):
    """Raised when Calendar.app automation fails."""

    def __init__(
        self,
        message: str,
        *,
        external_state_changed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.external_state_changed = external_state_changed


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
        description: DescriptionUpdate,
    ) -> CalendarEventRecord:
        payload = self._run_jxa(
            _UPDATE_EVENT_JXA,
            [
                calendar_id,
                event_id,
                description.operation,
                description.value or "",
            ],
        )
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise CalendarBackendError("Calendar update_event returned a non-object payload")
        return _record_from_payload(row)

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEventRecord:
        payload = self._run_jxa(
            _GET_EVENT_JXA,
            [calendar_id, event_id],
        )
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise CalendarBackendError("Calendar get_event returned a non-object payload")
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
            raise CalendarBackendError(
                f"Unable to run osascript: {error}",
                external_state_changed=False,
            ) from error
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
        start_date=date.fromisoformat(str(row["start_date"])) if row.get("start_date") else None,
        end_date=date.fromisoformat(str(row["end_date"])) if row.get("end_date") else None,
        location=str(row["location"]) if row.get("location") else None,
        notes=str(row["notes"]) if row.get("notes") else None,
    )


_LIST_EVENTS_JXA = r"""
function formatLocalDate(value) {
  const year = value.getFullYear();
  const month = ("0" + (value.getMonth() + 1)).slice(-2);
  const day = ("0" + value.getDate()).slice(-2);
  return year + "-" + month + "-" + day;
}

function run(argv) {
  const calendarIds = argv[0].split("\n").filter(Boolean);
  const rangeStart = new Date(argv[1]);
  const rangeEnd = new Date(argv[2]);
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
        const isAllDay = event.alldayEvent();
        rows.push({
          event_id: event.uid(),
          calendar_id: calendarName,
          title: event.summary(),
          start: eventStart.toISOString(),
          end: eventEnd.toISOString(),
          is_all_day: isAllDay,
          start_date: isAllDay ? formatLocalDate(eventStart) : null,
          end_date: isAllDay ? formatLocalDate(eventEnd) : null,
          location: event.location(),
          notes: event.description()
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
  const operation = argv[2];
  const description = argv[3];
  const Calendar = Application("Calendar");
  const calendar = Calendar.calendars.byName(calendarId);
  const matches = calendar.events.whose({uid: eventId})();
  if (matches.length === 0) {
    throw new Error("Calendar event not found: " + eventId);
  }
  const event = matches[0];
  if (operation === "set") {
    event.description = description;
  } else if (operation === "clear") {
    event.description = "";
  } else {
    throw new Error("Unsupported description operation");
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


_GET_EVENT_JXA = r"""
function run(argv) {
  const calendarId = argv[0];
  const eventId = argv[1];
  const Calendar = Application("Calendar");
  const calendar = Calendar.calendars.byName(calendarId);
  const matches = calendar.events.whose({uid: eventId})();
  if (matches.length === 0) {
    throw new Error("Calendar event not found: " + eventId);
  }
  const event = matches[0];
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
