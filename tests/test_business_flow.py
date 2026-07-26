from datetime import UTC, datetime
from pathlib import Path

import anyio

from personal_activity_mcp.calendar import CalendarEventRecord
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


def write_config(path: Path) -> None:
    sidecar_path = path.parent / "sidecar.sqlite3"
    path.write_text(
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


def call_tool(server, name: str, arguments: dict[str, object]):
    return anyio.run(server.call_tool, name, arguments)[1]


def create_calendar(server, *, idempotency_key: str):
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
            "source_refs": ["calendar:source-event"],
            "idempotency_key": idempotency_key,
        },
    )


def start_candidate(server, *, source_ref: str, idempotency_key: str):
    created = call_tool(
        server,
        "candidates.create",
        {
            "command": {
                "action_type": "create_event",
                "payload": {
                    "title": "Follow-up session",
                    "start": "2026-07-28T10:00:00+00:00",
                    "end": "2026-07-28T11:00:00+00:00",
                    "description": "Created from review",
                },
                "source_refs": [source_ref],
            }
        },
    )
    confirmed = call_tool(
        server,
        "candidates.update",
        {
            "candidate_id": created["candidate_id"],
            "command": {
                "expected_version": created["version"],
                "decision_status": "confirmed",
            },
        },
    )
    routed = call_tool(
        server,
        "candidates.update",
        {
            "candidate_id": created["candidate_id"],
            "command": {
                "expected_version": confirmed["version"],
                "route": {
                    "provider": "personal_activity_mcp",
                    "tool_name": "calendar.create_event",
                    "operation": "calendar.create_event",
                    "idempotency_key": idempotency_key,
                },
            },
        },
    )
    return call_tool(
        server,
        "candidates.update",
        {
            "candidate_id": created["candidate_id"],
            "command": {
                "expected_version": routed["version"],
                "execution_status": "in_progress",
            },
        },
    )


def test_typical_read_candidate_confirm_route_write_and_register_flow(
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
    source = listed["events"][0]
    key = "business-flow:create"
    started = start_candidate(
        server,
        source_ref=source["evidence_id"],
        idempotency_key=key,
    )
    write_result = create_calendar(server, idempotency_key=key)
    finished = call_tool(
        server,
        "candidates.update",
        {
            "candidate_id": started["candidate_id"],
            "command": {
                "expected_version": started["version"],
                "execution_status": "succeeded",
                "result_ref": {
                    "provider": "personal_activity_mcp",
                    "status": "succeeded",
                    "item_id": write_result["stable_id"],
                    "container_id": "Personal",
                    "verification_source": "agent_reported",
                },
            },
        },
    )

    assert started["decision_status"] == "confirmed"
    assert started["execution_status"] == "in_progress"
    assert finished["execution_status"] == "succeeded"
    assert finished["result_ref"]["item_id"] == write_result["stable_id"]
    assert finished["result_ref"]["verification_source"] == "sidecar_verified"
    assert finished["result_ref"]["audit_id"] is not None
    assert backend.create_calls == 1


def test_direct_calendar_write_without_candidate_is_valid(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = FlowCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)

    result = create_calendar(server, idempotency_key="direct-flow:create")
    candidates = call_tool(server, "candidates.list", {"query": {}})

    assert result["created"] is True
    assert candidates["candidates"] == []
    assert backend.create_calls == 1


def test_interrupted_candidate_terminal_registration_reconciles_without_rewrite(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    backend = FlowCalendarBackend()
    server = create_server(config_path, calendar_backend=backend)
    key = "interrupted-flow:create"
    started = start_candidate(
        server,
        source_ref="agent:file-record-1",
        idempotency_key=key,
    )

    write_result = create_calendar(server, idempotency_key=key)
    reconciled = call_tool(
        server,
        "candidates.update",
        {
            "candidate_id": started["candidate_id"],
            "command": {
                "expected_version": started["version"],
                "reconcile_execution": True,
            },
        },
    )

    assert reconciled["execution_status"] == "succeeded"
    assert reconciled["result_ref"]["item_id"] == write_result["stable_id"]
    assert reconciled["result_ref"]["verification_source"] == "sidecar_verified"
    assert backend.create_calls == 1
