import json
import sqlite3
from pathlib import Path

import anyio

from personal_activity_mcp.calendar import CalendarEventRecord
from personal_activity_mcp.server import create_server, main


class FakeCalendarBackend:
    def __init__(self) -> None:
        self.created_events: list[dict[str, object]] = []

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start,
        end,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        return [
            CalendarEventRecord(
                event_id="event-1",
                calendar_id=calendar_ids[0],
                title="Calendar demo",
                start=start,
                end=end,
                is_all_day=False,
                location="Room 1",
                notes="Private notes",
            )
        ]

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
        return CalendarEventRecord(
            event_id="created-event-1",
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=location,
            notes=notes,
        )


def write_config(config_path: Path, journal_path: Path) -> None:
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
extensions = [".md", ".txt"]
""".strip(),
        encoding="utf-8",
    )


def write_config_with_sidecar(config_path: Path, journal_path: Path, sidecar_path: Path) -> None:
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
extensions = [".md", ".txt"]
""".strip(),
        encoding="utf-8",
    )


def write_config_with_calendar(config_path: Path, journal_path: Path, sidecar_path: Path) -> None:
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"
default_timezone = "Asia/Shanghai"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
extensions = [".md", ".txt"]

[[calendar_sources]]
calendar_id = "Personal"
title = "Personal"
allow_write = true
""".strip(),
        encoding="utf-8",
    )


def test_server_exposes_journal_tool_and_resource_template(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    tools = anyio.run(server.list_tools)
    templates = anyio.run(server.list_resource_templates)

    assert [tool.name for tool in tools] == [
        "journal.list_entries",
        "journal.search_entries",
        "calendar.list_events",
        "calendar.create_event",
    ]
    assert str(templates[0].uriTemplate) == "journal://{source_id}/{entry_id}"


def test_server_tool_and_resource_form_an_end_to_end_read_path(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-03.md").write_text("Local-only content", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    _, structured_result = anyio.run(
        server.call_tool,
        "journal.list_entries",
        {"start_date": "2026-07-03", "end_date": "2026-07-03"},
    )
    resource_uri = structured_result["entries"][0]["resource_uri"]
    resource_result = list(anyio.run(server.read_resource, resource_uri))[0]
    resource = json.loads(resource_result.content)

    assert resource["resource_uri"] == resource_uri
    assert resource["content"] == "Local-only content"


def test_server_records_journal_metadata_in_sidecar_without_body(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-03.md").write_text(
        "Local-only sidecar body",
        encoding="utf-8",
    )
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_sidecar(config_path, journal_path, sidecar_path)
    server = create_server(config_path)

    _, structured_result = anyio.run(
        server.call_tool,
        "journal.list_entries",
        {"start_date": "2026-07-03", "end_date": "2026-07-03"},
    )
    evidence_id = structured_result["entries"][0]["evidence_id"]

    with sqlite3.connect(sidecar_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM journal_entry").fetchone()

    assert row["id"] == evidence_id
    assert row["source_id"] == "journal:daily"
    assert row["entry_date"] == "2026-07-03"
    assert "Local-only sidecar body" not in " ".join(str(value) for value in row)


def test_server_search_tool_returns_structured_keyword_hits(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-03.md").write_text("Local MCP search", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    _, structured_result = anyio.run(
        server.call_tool,
        "journal.search_entries",
        {
            "query": "mcp",
            "start_date": "2026-07-03",
            "end_date": "2026-07-03",
            "include_snippets": True,
        },
    )

    assert structured_result["entries"][0]["matched_terms"] == ["mcp"]
    assert structured_result["entries"][0]["snippets"] == ["Local MCP search"]


def test_server_calendar_tools_use_configured_backend_and_sidecar(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config_with_calendar(config_path, journal_path, sidecar_path)
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
            "provenance_ids": [],
            "idempotency_key": "calendar:create:demo",
        },
    )

    assert list_result["events"][0]["event_id"] == "event-1"
    assert list_result["events"][0]["notes"] is None
    assert create_result["event_id"] == "created-event-1"
    assert create_result["created"] is True
    assert len(backend.created_events) == 1


def test_main_reports_missing_configuration_without_traceback(
    tmp_path: Path, capsys: object
) -> None:
    exit_code = main(["--config", str(tmp_path / "missing.toml")])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 2
    assert "Configuration error: Configuration file not found" in captured.err
    assert "Traceback" not in captured.err
