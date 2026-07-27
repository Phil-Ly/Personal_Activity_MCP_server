"""macOS Calendar backend."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.calendar.models import CalendarEventRecord, DescriptionUpdate
from personal_activity_mcp.common.jxa import (
    JXABackendError,
    optional_json_string,
    required_json_bool,
    required_json_string,
    run_jxa,
)


class CalendarBackendError(JXABackendError):
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
        return _records_from_json(payload, operation="list_events")

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
        return _record_from_json(payload, operation="create_event")

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
        return _record_from_json(payload, operation="update_event")

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
        return _record_from_json(payload, operation="get_event")

    def _run_jxa(self, script: str, args: list[str]) -> str:
        return run_jxa(
            self._osascript_path,
            script,
            args,
            application_name="Calendar",
            error_type=CalendarBackendError,
        )


def _record_from_payload(row: Any) -> CalendarEventRecord:
    if not isinstance(row, dict):
        raise CalendarBackendError("Calendar event row must be an object")
    start = required_json_string(row, "start")
    end = required_json_string(row, "end")
    start_date = optional_json_string(row, "start_date")
    end_date = optional_json_string(row, "end_date")
    return CalendarEventRecord(
        event_id=required_json_string(row, "event_id"),
        calendar_id=required_json_string(row, "calendar_id"),
        title=required_json_string(row, "title"),
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        is_all_day=required_json_bool(row, "is_all_day"),
        start_date=date.fromisoformat(start_date) if start_date is not None else None,
        end_date=date.fromisoformat(end_date) if end_date is not None else None,
        location=optional_json_string(row, "location"),
        notes=optional_json_string(row, "notes"),
    )


def _record_from_json(payload: str, *, operation: str) -> CalendarEventRecord:
    try:
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise TypeError("expected an object")
        return _record_from_payload(row)
    except (KeyError, TypeError, ValueError) as error:
        raise CalendarBackendError(f"Calendar {operation} returned an invalid payload") from error


def _records_from_json(payload: str, *, operation: str) -> list[CalendarEventRecord]:
    try:
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise TypeError("expected a list")
        return [_record_from_payload(row) for row in rows]
    except (KeyError, TypeError, ValueError) as error:
        raise CalendarBackendError(f"Calendar {operation} returned an invalid payload") from error


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
