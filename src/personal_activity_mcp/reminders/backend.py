"""macOS Reminders backend."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.common.jxa import (
    JXABackendError,
    optional_json_int,
    optional_json_string,
    required_json_bool,
    required_json_string,
    run_jxa,
)
from personal_activity_mcp.reminders.models import ReminderRecord


class ReminderBackendError(JXABackendError):
    """Raised when Reminders.app automation fails."""


class MacOSReminderBackend:
    """Read and write Reminders.app through the local osascript bridge."""

    def __init__(self, osascript_path: Path = Path("/usr/bin/osascript")) -> None:
        self._osascript_path = osascript_path

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
        payload = self._run_jxa(
            _LIST_REMINDERS_JXA,
            [
                "\n".join(list_ids),
                start_due_at.isoformat() if start_due_at else "",
                end_due_at.isoformat() if end_due_at else "",
                start_completed_at.isoformat() if start_completed_at else "",
                end_completed_at.isoformat() if end_completed_at else "",
                "true" if include_completed else "false",
                "true" if include_notes else "false",
            ],
        )
        return _records_from_json(payload, operation="list_reminders")

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> ReminderRecord:
        payload = self._run_jxa(
            _CREATE_REMINDER_JXA,
            [
                list_id,
                title,
                notes or "",
                due_date.isoformat() if due_date else "",
                "" if priority is None else str(priority),
            ],
        )
        return _record_from_json(payload, operation="create_reminder")

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date: datetime,
    ) -> ReminderRecord:
        payload = self._run_jxa(
            _COMPLETE_REMINDER_JXA,
            [
                list_id,
                reminder_id,
                completion_date.isoformat(),
            ],
        )
        return _record_from_json(payload, operation="complete_reminder")

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> ReminderRecord:
        payload = self._run_jxa(
            _GET_REMINDER_JXA,
            [list_id, reminder_id],
        )
        return _record_from_json(payload, operation="get_reminder")

    def _run_jxa(self, script: str, args: list[str]) -> str:
        return run_jxa(
            self._osascript_path,
            script,
            args,
            application_name="Reminders",
            error_type=ReminderBackendError,
        )


def _record_from_payload(row: Any) -> ReminderRecord:
    if not isinstance(row, dict):
        raise ReminderBackendError("Reminder row must be an object")
    due_date_value = optional_json_string(row, "due_date")
    completion_date_value = optional_json_string(row, "completion_date")
    return ReminderRecord(
        reminder_id=required_json_string(row, "reminder_id"),
        list_id=required_json_string(row, "list_id"),
        title=required_json_string(row, "title"),
        notes=optional_json_string(row, "notes"),
        due_date=datetime.fromisoformat(due_date_value) if due_date_value is not None else None,
        priority=optional_json_int(row, "priority"),
        is_completed=required_json_bool(row, "is_completed"),
        completion_date=(
            datetime.fromisoformat(completion_date_value)
            if completion_date_value is not None
            else None
        ),
    )


def _record_from_json(payload: str, *, operation: str) -> ReminderRecord:
    try:
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise TypeError("expected an object")
        return _record_from_payload(row)
    except (KeyError, TypeError, ValueError) as error:
        raise ReminderBackendError(f"Reminders {operation} returned an invalid payload") from error


def _records_from_json(payload: str, *, operation: str) -> list[ReminderRecord]:
    try:
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise TypeError("expected a list")
        return [_record_from_payload(row) for row in rows]
    except (KeyError, TypeError, ValueError) as error:
        raise ReminderBackendError(f"Reminders {operation} returned an invalid payload") from error


_LOCAL_DATE_FORMATTER_JXA = r"""
function formatLocalDate(value) {
  const year = value.getFullYear();
  const month = ("0" + (value.getMonth() + 1)).slice(-2);
  const day = ("0" + value.getDate()).slice(-2);
  return year + "-" + month + "-" + day;
}
"""


_LIST_REMINDERS_JXA = (
    _LOCAL_DATE_FORMATTER_JXA
    + r"""
function run(argv) {
  const listIds = argv[0].split("\n").filter(Boolean);
  const startDue = argv[1] ? new Date(argv[1]) : null;
  const endDue = argv[2] ? new Date(argv[2]) : null;
  const startCompleted = argv[3] ? new Date(argv[3]) : null;
  const endCompleted = argv[4] ? new Date(argv[4]) : null;
  const includeCompleted = argv[5] === "true";
  const Reminders = Application("Reminders");
  const rows = [];
  Reminders.lists().forEach(function(list) {
    const listName = list.name();
    if (listIds.indexOf(listName) === -1) {
      return;
    }
    list.reminders().forEach(function(reminder) {
      const completed = reminder.completed();
      if (completed && !includeCompleted) {
        return;
      }
      const due = reminder.dueDate();
      if (!due && (startDue || endDue)) {
        return;
      }
      if (due && startDue && due < startDue) {
        return;
      }
      if (due && endDue && due > endDue) {
        return;
      }
      const completionDate = reminder.completionDate();
      if (!completionDate && (startCompleted || endCompleted)) {
        return;
      }
      if (completionDate && startCompleted && completionDate < startCompleted) {
        return;
      }
      if (completionDate && endCompleted && completionDate > endCompleted) {
        return;
      }
      rows.push({
        reminder_id: reminder.id(),
        list_id: listName,
        title: reminder.name(),
        notes: reminder.body(),
        due_date: due ? due.toISOString() : null,
        priority: reminder.priority(),
        is_completed: completed,
        completion_date: completionDate ? completionDate.toISOString() : null
      });
    });
  });
  return JSON.stringify(rows);
}
"""
)


_CREATE_REMINDER_JXA = (
    _LOCAL_DATE_FORMATTER_JXA
    + r"""
function run(argv) {
  const listId = argv[0];
  const title = argv[1];
  const notes = argv[2] || null;
  const dueDate = argv[3] ? new Date(argv[3] + "T00:00:00") : null;
  const priority = argv[4] ? Number(argv[4]) : 0;
  const Reminders = Application("Reminders");
  const list = Reminders.lists.byName(listId);
  const reminder = Reminders.Reminder({
    name: title,
    body: notes,
    dueDate: dueDate,
    priority: priority
  });
  list.reminders.push(reminder);
  return JSON.stringify({
    reminder_id: reminder.id(),
    list_id: listId,
    title: reminder.name(),
    notes: notes,
    due_date: dueDate ? dueDate.toISOString() : null,
    priority: reminder.priority(),
    is_completed: reminder.completed(),
    completion_date: null
  });
}
"""
)


_GET_REMINDER_JXA = (
    _LOCAL_DATE_FORMATTER_JXA
    + r"""
function run(argv) {
  const listId = argv[0];
  const reminderId = argv[1];
  const Reminders = Application("Reminders");
  const list = Reminders.lists.byName(listId);
  for (const reminder of list.reminders()) {
    if (reminder.id() === reminderId) {
      const due = reminder.dueDate();
      const completionDate = reminder.completionDate();
      return JSON.stringify({
        reminder_id: reminder.id(),
        list_id: listId,
        title: reminder.name(),
        notes: reminder.body(),
        due_date: due ? due.toISOString() : null,
        priority: reminder.priority(),
        is_completed: reminder.completed(),
        completion_date: completionDate ? completionDate.toISOString() : null
      });
    }
  }
  throw new Error("Reminder not found in configured list");
}
"""
)


_COMPLETE_REMINDER_JXA = (
    _LOCAL_DATE_FORMATTER_JXA
    + r"""
function run(argv) {
  const listId = argv[0];
  const reminderId = argv[1];
  const completionDate = new Date(argv[2]);
  const Reminders = Application("Reminders");
  const list = Reminders.lists.byName(listId);
  for (const reminder of list.reminders()) {
    if (reminder.id() === reminderId) {
      reminder.completed = true;
      reminder.completionDate = completionDate;
      const due = reminder.dueDate();
      return JSON.stringify({
        reminder_id: reminder.id(),
        list_id: listId,
        title: reminder.name(),
        notes: reminder.body(),
        due_date: due ? due.toISOString() : null,
        priority: reminder.priority(),
        is_completed: reminder.completed(),
        completion_date: reminder.completionDate().toISOString()
      });
    }
  }
  throw new Error("Reminder not found in configured list");
}
"""
)
