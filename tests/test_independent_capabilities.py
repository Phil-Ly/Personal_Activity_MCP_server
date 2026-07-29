from datetime import UTC, datetime
from pathlib import Path

import anyio

from personal_activity_mcp.calendar import (
    CalendarContainerRecord,
    CalendarEventRecord,
    DescriptionUpdate,
)
from personal_activity_mcp.server import create_server


class IndependentCalendarBackend:
    def __init__(self) -> None:
        existing = CalendarEventRecord(
            event_id="existing-event",
            calendar_id="Personal",
            title="Existing event",
            start=datetime(2026, 7, 28, 8, tzinfo=UTC),
            end=datetime(2026, 7, 28, 9, tzinfo=UTC),
            is_all_day=False,
            location=None,
            notes=None,
        )
        self.events = {(existing.calendar_id, existing.event_id): existing}
        self.list_calls = 0
        self.create_calls = 0
        self.update_calls = 0
        self.calendar = CalendarContainerRecord(
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

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[CalendarContainerRecord]:
        return [self.calendar] if self.calendar.source_id in source_ids else []

    def get_calendar(self, *, calendar_id: str) -> CalendarContainerRecord:
        if calendar_id != self.calendar.calendar_id:
            raise KeyError(calendar_id)
        return self.calendar

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        self.list_calls += 1
        return [
            record
            for (calendar_id, _), record in self.events.items()
            if calendar_id in calendar_ids and record.start < end and record.end > start
        ]

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
        self.create_calls += 1
        record = CalendarEventRecord(
            event_id=f"created-event-{self.create_calls}",
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=location,
            notes=notes,
        )
        self.events[(calendar_id, record.event_id)] = record
        return record

    def get_event(self, *, event_id: str, calendar_id: str) -> CalendarEventRecord:
        return self.events[(calendar_id, event_id)]

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
        description: DescriptionUpdate,
    ) -> CalendarEventRecord:
        self.update_calls += 1
        current = self.events[(calendar_id, event_id)]
        updated = current.model_copy(
            update={
                "notes": description.value if description.operation == "set" else None,
            }
        )
        self.events[(calendar_id, event_id)] = updated
        return updated


def write_config(path: Path) -> None:
    sidecar_path = path.parent / "sidecar.sqlite3"
    path.write_text(
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


def create_calendar(server, *, key: str):
    return anyio.run(
        server.call_tool,
        "calendar.create_event",
        {
            "calendar_id": "Personal",
            "title": "Independent write",
            "start": "2026-07-28T10:00:00+00:00",
            "end": "2026-07-28T11:00:00+00:00",
            "is_all_day": False,
            "notes": None,
            "location": None,
            "timezone": "Asia/Shanghai",
            "source_refs": [],
            "idempotency_key": key,
        },
    )[1]


def test_calendar_read_is_independent_from_all_write_capabilities(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = IndependentCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    _, result = anyio.run(
        server.call_tool,
        "calendar.list_events",
        {
            "calendar_ids": ["Personal"],
            "start": "2026-07-28T00:00:00+00:00",
            "end": "2026-07-29T00:00:00+00:00",
        },
    )

    assert [event["event_id"] for event in result["events"]] == ["existing-event"]
    assert backend.list_calls == 1
    assert backend.create_calls == 0
    assert backend.update_calls == 0


def test_calendar_write_needs_no_read_or_prompt_call(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = IndependentCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    result = create_calendar(server, key="independent:create")

    assert result["created"] is True
    assert backend.create_calls == 1
    assert backend.list_calls == 0


def test_calendar_write_can_precede_public_read(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = IndependentCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    created = create_calendar(server, key="write-before-read")
    _, listed = anyio.run(
        server.call_tool,
        "calendar.list_events",
        {
            "calendar_ids": ["Personal"],
            "start": "2026-07-28T00:00:00+00:00",
            "end": "2026-07-29T00:00:00+00:00",
        },
    )

    assert created["event_id"] in {event["event_id"] for event in listed["events"]}
    assert backend.create_calls == 1
    assert backend.list_calls == 1


def test_review_prompt_is_independent_from_data_tools(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = IndependentCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    result = anyio.run(
        server.get_prompt,
        "activity.review_summary",
        {
            "period_start": "2026-07-01T00:00:00+08:00",
            "period_end": "2026-07-08T00:00:00+08:00",
        },
    )

    assert "user context alone" in result.messages[0].content.text
    assert backend.list_calls == 0
    assert backend.create_calls == 0


def test_internal_target_reads_are_not_registered_as_public_tools(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    tool_names = {tool.name for tool in anyio.run(server.list_tools)}

    assert "calendar.get_event" not in tool_names
    assert "reminders.get_reminder" not in tool_names
    assert "calendar.delete_event" not in tool_names
    assert "reminders.delete_reminder" not in tool_names
    assert not any("bulk" in name for name in tool_names)
