from datetime import UTC, datetime
from pathlib import Path

import anyio

from personal_activity_mcp.calendar import CalendarContainerRecord, CalendarEventRecord
from personal_activity_mcp.server import create_server


class FlowCalendarBackend:
    def __init__(self) -> None:
        seed = CalendarEventRecord(
            event_id="source-event",
            calendar_id="Personal",
            title="Project review",
            start=datetime(2026, 7, 28, 8, tzinfo=UTC),
            end=datetime(2026, 7, 28, 9, tzinfo=UTC),
            is_all_day=False,
            location=None,
            notes=None,
        )
        self.events = {(seed.calendar_id, seed.event_id): seed}
        self.create_calls = 0
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
        return [
            event
            for (calendar_id, _), event in self.events.items()
            if calendar_id in calendar_ids and event.start < end and event.end > start
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
        event = CalendarEventRecord(
            event_id=f"flow-created-{self.create_calls}",
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=location,
            notes=notes,
        )
        self.events[(calendar_id, event.event_id)] = event
        return event

    def get_event(self, *, event_id: str, calendar_id: str) -> CalendarEventRecord:
        return self.events[(calendar_id, event_id)]


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


def call_tool(server, name: str, arguments: dict[str, object]):
    return anyio.run(server.call_tool, name, arguments)[1]


def create_calendar(
    server,
    *,
    idempotency_key: str,
    source_refs: list[str],
):
    return call_tool(
        server,
        "calendar.create_event",
        {
            "calendar_id": "Personal",
            "title": "Follow-up session",
            "start": "2026-07-28T10:00:00+00:00",
            "end": "2026-07-28T11:00:00+00:00",
            "is_all_day": False,
            "notes": "Created from review",
            "location": None,
            "timezone": "Asia/Shanghai",
            "source_refs": source_refs,
            "idempotency_key": idempotency_key,
        },
    )


def test_evidence_can_flow_directly_into_an_idempotent_calendar_write(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = FlowCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    listed = call_tool(
        server,
        "calendar.list_events",
        {
            "calendar_ids": ["Personal"],
            "start": "2026-07-28T00:00:00+00:00",
            "end": "2026-07-29T00:00:00+00:00",
        },
    )
    source_ref = listed["events"][0]["evidence_id"]

    created = create_calendar(
        server,
        idempotency_key="business-flow:create",
        source_refs=[source_ref],
    )
    repeated = create_calendar(
        server,
        idempotency_key="business-flow:create",
        source_refs=[source_ref],
    )

    assert created["created"] is True
    assert created["source_refs"] == [source_ref]
    assert repeated["created"] is False
    assert repeated["deduplicated"] is True
    assert backend.create_calls == 1


def test_direct_calendar_write_without_public_read_is_valid(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = FlowCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    result = create_calendar(
        server,
        idempotency_key="direct-flow:create",
        source_refs=[],
    )

    assert result["created"] is True
    assert backend.create_calls == 1
