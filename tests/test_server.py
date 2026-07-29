from pathlib import Path

import anyio
import pytest

from personal_activity_mcp import server as server_module
from personal_activity_mcp.calendar import (
    CalendarContainerRecord,
    CalendarEventRecord,
    DescriptionUpdate,
)
from personal_activity_mcp.reminders import ReminderListContainerRecord, ReminderRecord
from personal_activity_mcp.server import create_server, main


class FakeCalendarBackend:
    def __init__(self) -> None:
        self.calendars: dict[str, CalendarContainerRecord] = {
            "Personal": CalendarContainerRecord(
                calendar_id="Personal",
                source_id="source-icloud",
                source_title="iCloud",
                title="Personal",
                color="#3366CC",
                calendar_type="caldav",
                allows_content_modifications=True,
                is_immutable=False,
                is_subscribed=False,
            )
        }
        self.created_calendars: list[dict[str, object]] = []
        self.updated_calendars: list[dict[str, object]] = []
        self.created_events: list[dict[str, object]] = []
        self.updated_events: list[dict[str, object]] = []
        self.events: dict[tuple[str, str], CalendarEventRecord] = {}

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[CalendarContainerRecord]:
        return [
            calendar for calendar in self.calendars.values() if calendar.source_id in source_ids
        ]

    def get_calendar(self, *, calendar_id: str) -> CalendarContainerRecord:
        return self.calendars[calendar_id]

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> CalendarContainerRecord:
        self.created_calendars.append(
            {
                "source_id": source_id,
                "title": title,
                "color": color,
            }
        )
        record = CalendarContainerRecord(
            calendar_id=f"created-calendar-{len(self.created_calendars)}",
            source_id=source_id,
            source_title="iCloud",
            title=title,
            color=color or "#3366CC",
            calendar_type="caldav",
            allows_content_modifications=True,
            is_immutable=False,
            is_subscribed=False,
        )
        self.calendars[record.calendar_id] = record
        return record

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> CalendarContainerRecord:
        self.updated_calendars.append(
            {
                "calendar_id": calendar_id,
                "title": title,
                "color": color,
            }
        )
        current = self.calendars[calendar_id]
        record = current.model_copy(
            update={
                "title": title if title is not None else current.title,
                "color": color if color is not None else current.color,
            }
        )
        self.calendars[calendar_id] = record
        return record

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
        self.lists: dict[str, ReminderListContainerRecord] = {
            "Personal": ReminderListContainerRecord(
                list_id="Personal",
                source_id="source-icloud",
                source_title="iCloud",
                title="Personal",
                color="#3366CC",
                calendar_type="caldav",
                allows_content_modifications=True,
                is_immutable=False,
                is_subscribed=False,
            )
        }
        self.created_lists: list[dict[str, object]] = []
        self.updated_lists: list[dict[str, object]] = []
        self.created_reminders: list[dict[str, object]] = []
        self.completed_reminders: list[dict[str, object]] = []
        self.reminders: dict[tuple[str, str], ReminderRecord] = {}

    def list_reminder_lists(
        self,
        *,
        source_ids: list[str],
    ) -> list[ReminderListContainerRecord]:
        return [item for item in self.lists.values() if item.source_id in source_ids]

    def get_reminder_list(self, *, list_id: str) -> ReminderListContainerRecord:
        return self.lists[list_id]

    def create_reminder_list(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> ReminderListContainerRecord:
        self.created_lists.append(
            {
                "source_id": source_id,
                "title": title,
                "color": color,
            }
        )
        record = ReminderListContainerRecord(
            list_id=f"created-list-{len(self.created_lists)}",
            source_id=source_id,
            source_title="iCloud",
            title=title,
            color=color or "#3366CC",
            calendar_type="caldav",
            allows_content_modifications=True,
            is_immutable=False,
            is_subscribed=False,
        )
        self.lists[record.list_id] = record
        return record

    def update_reminder_list(
        self,
        *,
        list_id: str,
        title: str | None,
        color: str | None,
    ) -> ReminderListContainerRecord:
        self.updated_lists.append(
            {
                "list_id": list_id,
                "title": title,
                "color": color,
            }
        )
        current = self.lists[list_id]
        record = current.model_copy(
            update={
                "title": title if title is not None else current.title,
                "color": color if color is not None else current.color,
            }
        )
        self.lists[list_id] = record
        return record

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

[[eventkit_sources]]
source_id = "source-icloud"
title = "iCloud"
allow_calendar_write = true
default_calendar_source = true
""".strip(),
        encoding="utf-8",
    )


def write_config_with_reminders(config_path: Path, sidecar_path: Path) -> None:
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"

[[eventkit_sources]]
source_id = "source-icloud"
title = "iCloud"
allow_reminder_write = true
default_reminder_source = true
""".strip(),
        encoding="utf-8",
    )


def test_default_backends_share_one_eventkit_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    shared_client = object()
    backend_clients: list[object] = []

    monkeypatch.setattr(
        server_module,
        "EventKitClient",
        lambda: shared_client,
        raising=False,
    )
    monkeypatch.setattr(
        server_module,
        "MacOSCalendarBackend",
        lambda *, client: backend_clients.append(client) or FakeCalendarBackend(),
    )
    monkeypatch.setattr(
        server_module,
        "MacOSReminderBackend",
        lambda *, client: backend_clients.append(client) or FakeReminderBackend(),
    )

    create_server(config_path)

    assert backend_clients == [shared_client, shared_client]


def test_server_exposes_no_local_file_tools_or_resources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    tools = anyio.run(server.list_tools)
    templates = anyio.run(server.list_resource_templates)

    assert server.name == "PAMCP"
    assert [tool.name for tool in tools] == [
        "calendar.list_calendars",
        "calendar.create_calendar",
        "calendar.update_calendar",
        "calendar.list_events",
        "calendar.create_event",
        "calendar.update_event",
        "reminders.list_lists",
        "reminders.create_list",
        "reminders.update_list",
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
    calendar_create = next(tool for tool in tools if tool.name == "calendar.create_calendar")
    assert set(calendar_create.inputSchema["required"]) == {
        "title",
        "idempotency_key",
    }
    calendar_update_container = next(
        tool for tool in tools if tool.name == "calendar.update_calendar"
    )
    assert set(calendar_update_container.inputSchema["required"]) == {
        "calendar_id",
        "idempotency_key",
    }
    reminder_list_create = next(tool for tool in tools if tool.name == "reminders.create_list")
    assert set(reminder_list_create.inputSchema["required"]) == {
        "title",
        "idempotency_key",
    }
    reminder_list_update = next(tool for tool in tools if tool.name == "reminders.update_list")
    assert set(reminder_list_update.inputSchema["required"]) == {
        "list_id",
        "idempotency_key",
    }


def test_server_calendar_tools_use_configured_backend_and_sidecar(tmp_path: Path) -> None:
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_calendar(config_path, sidecar_path)
    backend = FakeCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    _, container_list_result = anyio.run(
        server.call_tool,
        "calendar.list_calendars",
        {},
    )
    _, container_create_result = anyio.run(
        server.call_tool,
        "calendar.create_calendar",
        {
            "title": "Japanese Plan",
            "color": "#3366CC",
            "idempotency_key": "calendar:create-container:demo",
        },
    )
    _, container_update_result = anyio.run(
        server.call_tool,
        "calendar.update_calendar",
        {
            "calendar_id": "created-calendar-1",
            "title": "Japanese Plan 2026",
            "idempotency_key": "calendar:update-container:demo",
        },
    )
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
    assert container_list_result["calendars"][0]["calendar_id"] == "Personal"
    assert container_create_result["calendar"]["calendar_id"] == "created-calendar-1"
    assert container_create_result["created"] is True
    assert container_update_result["calendar"]["title"] == "Japanese Plan 2026"
    assert container_update_result["updated"] is True
    assert list_result["events"][0]["event_id"] == "event-1"
    assert list_result["events"][0]["notes"] is None
    assert create_result["event_id"] == "created-event-1"
    assert create_result["created"] is True
    assert update_result["event_id"] == "created-event-1"
    assert update_result["updated"] is True
    assert len(backend.created_events) == 1
    assert len(backend.updated_events) == 1
    assert len(backend.created_calendars) == 1
    assert len(backend.updated_calendars) == 1


def test_server_reminder_tools_use_configured_backend_and_sidecar(tmp_path: Path) -> None:
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_reminders(config_path, sidecar_path)
    backend = FakeReminderBackend()
    server = create_server(config_path, reminder_backend=backend)

    _, container_list_result = anyio.run(
        server.call_tool,
        "reminders.list_lists",
        {},
    )
    _, container_create_result = anyio.run(
        server.call_tool,
        "reminders.create_list",
        {
            "title": "Japanese Plan",
            "color": "#3366CC",
            "idempotency_key": "reminder:create-list:demo",
        },
    )
    _, container_update_result = anyio.run(
        server.call_tool,
        "reminders.update_list",
        {
            "list_id": "created-list-1",
            "title": "Japanese Plan 2026",
            "idempotency_key": "reminder:update-list:demo",
        },
    )
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

    assert container_list_result["lists"][0]["list_id"] == "Personal"
    assert container_create_result["list"]["list_id"] == "created-list-1"
    assert container_create_result["created"] is True
    assert container_update_result["list"]["title"] == "Japanese Plan 2026"
    assert container_update_result["updated"] is True
    assert list_result["reminders"][0]["reminder_id"] == "reminder-1"
    assert list_result["reminders"][0]["notes"] is None
    assert create_result["reminder_id"] == "created-reminder-1"
    assert create_result["created"] is True
    assert complete_result["reminder_id"] == "created-reminder-1"
    assert complete_result["is_completed"] is True
    assert complete_result["stable_id"] == create_result["stable_id"]
    assert len(backend.created_reminders) == 1
    assert len(backend.completed_reminders) == 1
    assert len(backend.created_lists) == 1
    assert len(backend.updated_lists) == 1


def test_main_reports_missing_configuration_without_traceback(
    tmp_path: Path, capsys: object
) -> None:
    exit_code = main(["--config", str(tmp_path / "missing.toml")])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 2
    assert "Configuration error: Configuration file not found" in captured.err
    assert "Traceback" not in captured.err


def test_main_uses_pamcp_config_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    launched: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str) -> None:
            launched["transport"] = transport

    def fake_create_server(path: Path) -> FakeServer:
        launched["config_path"] = path
        return FakeServer()

    monkeypatch.setenv("PAMCP_CONFIG", str(config_path))
    monkeypatch.setattr("personal_activity_mcp.server.create_server", fake_create_server)

    assert main([]) == 0
    assert launched == {
        "config_path": config_path,
        "transport": "stdio",
    }


def test_main_uses_pamcp_default_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str) -> None:
            launched["transport"] = transport

    def fake_create_server(path: Path) -> FakeServer:
        launched["config_path"] = path
        return FakeServer()

    monkeypatch.delenv("PAMCP_CONFIG", raising=False)
    monkeypatch.setattr("personal_activity_mcp.server.create_server", fake_create_server)

    assert main([]) == 0
    assert launched == {
        "config_path": Path("~/.config/pamcp/config.toml").expanduser(),
        "transport": "stdio",
    }


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
