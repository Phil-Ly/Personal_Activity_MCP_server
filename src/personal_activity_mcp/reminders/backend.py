"""macOS Reminders backend."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from personal_activity_mcp.reminders.models import ReminderRecord


class ReminderBackendError(RuntimeError):
    """Raised when Reminders.app automation fails."""


class MacOSReminderBackend:
    """Read and write Reminders.app through the local osascript bridge."""

    def __init__(self, osascript_path: Path = Path("/usr/bin/osascript")) -> None:
        self._osascript_path = osascript_path

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_date: date | None,
        end_due_date: date | None,
        include_completed: bool,
        include_notes: bool,
    ) -> list[ReminderRecord]:
        payload = self._run_jxa(
            _LIST_REMINDERS_JXA,
            [
                "\n".join(list_ids),
                start_due_date.isoformat() if start_due_date else "",
                end_due_date.isoformat() if end_due_date else "",
                "true" if include_completed else "false",
                "true" if include_notes else "false",
            ],
        )
        rows = json.loads(payload)
        if not isinstance(rows, list):
            raise ReminderBackendError("Reminders list_reminders returned a non-list payload")
        return [_record_from_payload(row) for row in rows]

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
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise ReminderBackendError("Reminders create_reminder returned a non-object payload")
        return _record_from_payload(row)

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_ids: list[str],
        completion_date: datetime,
    ) -> ReminderRecord:
        payload = self._run_jxa(
            _COMPLETE_REMINDER_JXA,
            [
                reminder_id,
                "\n".join(list_ids),
                completion_date.isoformat(),
            ],
        )
        row = json.loads(payload)
        if not isinstance(row, dict):
            raise ReminderBackendError("Reminders complete_reminder returned a non-object payload")
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
            raise ReminderBackendError(f"Unable to run osascript: {error}") from error
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown Reminders error"
            raise ReminderBackendError(f"Reminders automation failed: {message}")
        return result.stdout.strip()


def _record_from_payload(row: Any) -> ReminderRecord:
    if not isinstance(row, dict):
        raise ReminderBackendError("Reminder row must be an object")
    due_date = date.fromisoformat(str(row["due_date"])) if row.get("due_date") else None
    completion_date = (
        datetime.fromisoformat(str(row["completion_date"])) if row.get("completion_date") else None
    )
    return ReminderRecord(
        reminder_id=str(row["reminder_id"]),
        list_id=str(row["list_id"]),
        title=str(row["title"]),
        notes=str(row["notes"]) if row.get("notes") else None,
        due_date=due_date,
        priority=int(row["priority"]) if row.get("priority") is not None else None,
        is_completed=bool(row["is_completed"]),
        completion_date=completion_date,
    )


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
  const startDue = argv[1] ? new Date(argv[1] + "T00:00:00") : null;
  const endDue = argv[2] ? new Date(argv[2] + "T23:59:59") : null;
  const includeCompleted = argv[3] === "true";
  const includeNotes = argv[4] === "true";
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
      if (due && startDue && due < startDue) {
        return;
      }
      if (due && endDue && due > endDue) {
        return;
      }
      rows.push({
        reminder_id: reminder.id(),
        list_id: listName,
        title: reminder.name(),
        notes: includeNotes ? reminder.body() : null,
        due_date: due ? formatLocalDate(due) : null,
        priority: reminder.priority(),
        is_completed: completed,
        completion_date: reminder.completionDate() ? reminder.completionDate().toISOString() : null
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
    due_date: dueDate ? formatLocalDate(dueDate) : null,
    priority: reminder.priority(),
    is_completed: reminder.completed(),
    completion_date: null
  });
}
"""
)


_COMPLETE_REMINDER_JXA = (
    _LOCAL_DATE_FORMATTER_JXA
    + r"""
function run(argv) {
  const reminderId = argv[0];
  const listIds = argv[1].split("\n").filter(Boolean);
  const completionDate = new Date(argv[2]);
  const Reminders = Application("Reminders");
  for (const list of Reminders.lists()) {
    const listName = list.name();
    if (listIds.indexOf(listName) === -1) {
      continue;
    }
    for (const reminder of list.reminders()) {
      if (reminder.id() === reminderId) {
        reminder.completed = true;
        reminder.completionDate = completionDate;
        const due = reminder.dueDate();
        return JSON.stringify({
          reminder_id: reminder.id(),
          list_id: listName,
          title: reminder.name(),
          notes: null,
          due_date: due ? formatLocalDate(due) : null,
          priority: reminder.priority(),
          is_completed: reminder.completed(),
          completion_date: completionDate.toISOString()
        });
      }
    }
  }
  throw new Error("Reminder not found in configured lists");
}
"""
)
