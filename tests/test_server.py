from pathlib import Path

import anyio

from personal_activity_mcp.calendar import CalendarEventRecord, DescriptionUpdate
from personal_activity_mcp.reminders import ReminderRecord
from personal_activity_mcp.server import create_server, main


class FakeCalendarBackend:
    def __init__(self) -> None:
        self.created_events: list[dict[str, object]] = []
        self.updated_events: list[dict[str, object]] = []
        self.events: dict[tuple[str, str], CalendarEventRecord] = {}

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start,
        end,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        record = CalendarEventRecord(
            event_id="event-1",
            calendar_id=calendar_ids[0],
            title="Calendar demo",
            start=start,
            end=end,
            is_all_day=False,
            location="Room 1",
            notes="Private notes",
        )
        self.events[(record.calendar_id, record.event_id)] = record
        return [record]

    def create_event(
        self,
        *,
        calendar_id: str,
        title: str,
        start,
        end,
        is_all_day: bool,
        notes: str | None,
        location: str | None,
        timezone: str,
    ) -> CalendarEventRecord:
        self.created_events.append(
            {
                "calendar_id": calendar_id,
                "title": title,
                "start": start,
                "end": end,
                "timezone": timezone,
            }
        )
        record = CalendarEventRecord(
            event_id="created-event-1",
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=location,
            notes=notes,
        )
        self.events[(record.calendar_id, record.event_id)] = record
        return record

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEventRecord:
        return self.events[(calendar_id, event_id)]

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
        description: DescriptionUpdate,
    ) -> CalendarEventRecord:
        self.updated_events.append(
            {
                "event_id": event_id,
                "calendar_id": calendar_id,
                "description": description.model_dump(),
            }
        )
        current = self.events[(calendar_id, event_id)]
        updated = current.model_copy(
            update={
                "notes": description.value if description.operation == "set" else None,
            }
        )
        self.events[(calendar_id, event_id)] = updated
        return updated


class FakeReminderBackend:
    def __init__(self) -> None:
        self.created_reminders: list[dict[str, object]] = []
        self.completed_reminders: list[dict[str, object]] = []
        self.reminders: dict[tuple[str, str], ReminderRecord] = {}

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_at,
        end_due_at,
        start_completed_at,
        end_completed_at,
        include_completed: bool,
        include_notes: bool,
    ) -> list[ReminderRecord]:
        record = ReminderRecord(
            reminder_id="reminder-1",
            list_id=list_ids[0],
            title="Reminder demo",
            notes="Private notes",
            due_date=start_due_at,
            priority=5,
            is_completed=False,
            completion_date=None,
        )
        self.reminders[(record.list_id, record.reminder_id)] = record
        return [record]

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date,
        priority: int | None,
    ) -> ReminderRecord:
        self.created_reminders.append(
            {
                "list_id": list_id,
                "title": title,
                "due_date": due_date,
                "priority": priority,
            }
        )
        record = ReminderRecord(
            reminder_id="created-reminder-1",
            list_id=list_id,
            title=title,
            notes=notes,
            due_date=due_date,
            priority=priority,
            is_completed=False,
            completion_date=None,
        )
        self.reminders[(record.list_id, record.reminder_id)] = record
        return record

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> ReminderRecord:
        return self.reminders[(list_id, reminder_id)]

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date,
    ) -> ReminderRecord:
        self.completed_reminders.append(
            {
                "reminder_id": reminder_id,
                "list_id": list_id,
                "completion_date": completion_date,
            }
        )
        current = self.reminders[(list_id, reminder_id)]
        record = current.model_copy(
            update={
                "is_completed": True,
                "completion_date": completion_date,
            }
        )
        self.reminders[(list_id, reminder_id)] = record
        return record


def write_config(config_path: Path) -> None:
    sidecar_path = config_path.parent / "sidecar.sqlite3"
    config_path.write_text(f'sidecar_path = "{sidecar_path}"', encoding="utf-8")


def write_config_with_calendar(config_path: Path, sidecar_path: Path) -> None:
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"
default_timezone = "Asia/Shanghai"

[[calendar_sources]]
calendar_id = "Personal"
title = "Personal"
allow_write = true
""".strip(),
        encoding="utf-8",
    )


def write_config_with_reminders(config_path: Path, sidecar_path: Path) -> None:
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"

[[reminder_sources]]
list_id = "Personal"
title = "Personal"
allow_write = true
""".strip(),
        encoding="utf-8",
    )


def test_server_exposes_no_local_file_tools_or_resources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    tools = anyio.run(server.list_tools)
    templates = anyio.run(server.list_resource_templates)

    assert [tool.name for tool in tools] == [
        "calendar.list_events",
        "calendar.create_event",
        "calendar.update_event",
        "reminders.list_reminders",
        "reminders.create_reminder",
        "reminders.complete_reminder",
    ]
    assert "reminders.delete_reminder" not in [tool.name for tool in tools]
    assert templates == []
    calendar_update = next(tool for tool in tools if tool.name == "calendar.update_event")
    assert set(calendar_update.inputSchema["properties"]) == {
        "target_ref",
        "description",
        "completion_status",
        "expected_state_token",
        "source_refs",
        "confirmed_by_user",
        "idempotency_key",
    }
    reminder_complete = next(tool for tool in tools if tool.name == "reminders.complete_reminder")
    assert set(reminder_complete.inputSchema["properties"]) == {
        "target_ref",
        "completion_date",
        "expected_state_token",
        "confirmed_by_user",
        "idempotency_key",
    }


def test_server_calendar_tools_use_configured_backend_and_sidecar(tmp_path: Path) -> None:
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_calendar(config_path, sidecar_path)
    backend = FakeCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    _, list_result = anyio.run(
        server.call_tool,
        "calendar.list_events",
        {
            "calendar_ids": ["Personal"],
            "start": "2026-07-08T09:00:00+00:00",
            "end": "2026-07-08T18:00:00+00:00",
        },
    )
    _, create_result = anyio.run(
        server.call_tool,
        "calendar.create_event",
        {
            "calendar_id": "Personal",
            "title": "MCP demo",
            "start": "2026-07-08T10:00:00+00:00",
            "end": "2026-07-08T11:00:00+00:00",
            "is_all_day": False,
            "notes": None,
            "location": None,
            "timezone": "Asia/Shanghai",
            "source_refs": [],
            "idempotency_key": "calendar:create:demo",
        },
    )
    _, update_result = anyio.run(
        server.call_tool,
        "calendar.update_event",
        {
            "target_ref": {
                "resource_type": "calendar_event",
                "item_id": "created-event-1",
                "container_id": "Personal",
            },
            "description": {
                "operation": "set",
                "value": "Updated notes",
            },
            "completion_status": None,
            "expected_state_token": None,
            "source_refs": [],
            "confirmed_by_user": False,
            "idempotency_key": "calendar:update:demo",
        },
    )
    assert list_result["events"][0]["event_id"] == "event-1"
    assert list_result["events"][0]["notes"] is None
    assert create_result["event_id"] == "created-event-1"
    assert create_result["created"] is True
    assert update_result["event_id"] == "created-event-1"
    assert update_result["updated"] is True
    assert len(backend.created_events) == 1
    assert len(backend.updated_events) == 1


def test_server_reminder_tools_use_configured_backend_and_sidecar(tmp_path: Path) -> None:
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_reminders(config_path, sidecar_path)
    backend = FakeReminderBackend()
    server = create_server(config_path, reminder_backend=backend)

    _, list_result = anyio.run(
        server.call_tool,
        "reminders.list_reminders",
        {
            "list_ids": ["Personal"],
            "start_due_at": "2026-07-09T00:00:00+00:00",
            "end_due_at": "2026-07-10T00:00:00+00:00",
        },
    )
    _, create_result = anyio.run(
        server.call_tool,
        "reminders.create_reminder",
        {
            "list_id": "Personal",
            "title": "MCP todo",
            "notes": None,
            "due_date": "2026-07-09",
            "priority": 5,
            "source_refs": [],
            "idempotency_key": "reminder:create:demo",
        },
    )
    _, complete_result = anyio.run(
        server.call_tool,
        "reminders.complete_reminder",
        {
            "target_ref": {
                "resource_type": "reminder",
                "item_id": "created-reminder-1",
                "container_id": "Personal",
            },
            "completion_date": "2026-07-09T12:00:00+00:00",
            "expected_state_token": None,
            "confirmed_by_user": True,
            "idempotency_key": "reminder:complete:demo",
        },
    )

    assert list_result["reminders"][0]["reminder_id"] == "reminder-1"
    assert list_result["reminders"][0]["notes"] is None
    assert create_result["reminder_id"] == "created-reminder-1"
    assert create_result["created"] is True
    assert complete_result["reminder_id"] == "created-reminder-1"
    assert complete_result["is_completed"] is True
    assert complete_result["stable_id"] == create_result["stable_id"]
    assert len(backend.created_reminders) == 1
    assert len(backend.completed_reminders) == 1


def test_main_reports_missing_configuration_without_traceback(
    tmp_path: Path, capsys: object
) -> None:
    exit_code = main(["--config", str(tmp_path / "missing.toml")])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 2
    assert "Configuration error: Configuration file not found" in captured.err
    assert "Traceback" not in captured.err


def test_tool_failures_use_structured_public_error_contract(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.call_tool,
        "calendar.list_events",
        {
            "calendar_ids": ["Secret"],
            "start": "2026-07-08T09:00:00+00:00",
            "end": "2026-07-08T18:00:00+00:00",
        },
    )

    assert result.structuredContent == {
        "code": "SOURCE_NOT_AUTHORIZED",
        "message": "Requested source is not authorized",
        "retryable": False,
    }
    assert result.isError is True
    assert len(result.content) == 1
    assert "Secret" not in result.content[0].text
