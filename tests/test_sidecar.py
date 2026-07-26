import sqlite3
from pathlib import Path

import pytest

from personal_activity_mcp.config import CalendarSource, ReminderSource
from personal_activity_mcp.sidecar import SidecarRepository


def table_names(database_path: Path) -> set[str]:
    repository = SidecarRepository(database_path)
    with repository.connect() as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_initialize_creates_only_current_required_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "personal_activity.sqlite3"

    repository = SidecarRepository(database_path)
    repository.initialize()

    assert table_names(database_path) == {
        "source",
        "mcp_item",
        "idempotency_key",
        "source_link",
        "operation_audit",
        "schema_version",
    }


def test_source_table_rejects_removed_local_file_source_type(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()

    with pytest.raises(sqlite3.IntegrityError), repository.connect() as connection:
        connection.execute(
            """
                INSERT INTO source (id, source_type, source_name, source_uri, config_key)
                VALUES ('local:daily', 'local_file', 'Daily', 'file:///tmp/daily', 'daily')
                """
        )


def test_upsert_calendar_source_stores_allowlisted_calendar_metadata(
    tmp_path: Path,
) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()

    source_id = repository.upsert_calendar_source(
        CalendarSource(calendar_id="Personal", title="Personal", allow_write=True)
    )

    with repository.connect() as connection:
        row = connection.execute(
            "SELECT * FROM source WHERE id = ?",
            (source_id,),
        ).fetchone()
    assert row["id"] == "calendar:Personal"
    assert row["source_type"] == "calendar"
    assert row["source_name"] == "Personal"
    assert row["source_uri"] == "calendar://Personal"
    assert row["config_key"] == "Personal"


def test_upsert_reminder_source_stores_allowlisted_list_metadata(
    tmp_path: Path,
) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()

    source_id = repository.upsert_reminder_source(
        ReminderSource(list_id="Personal", title="Personal", allow_write=True)
    )

    with repository.connect() as connection:
        row = connection.execute(
            "SELECT * FROM source WHERE id = ?",
            (source_id,),
        ).fetchone()
    assert row["id"] == "reminder:Personal"
    assert row["source_type"] == "reminder"
    assert row["source_name"] == "Personal"
    assert row["source_uri"] == "reminder://Personal"
    assert row["config_key"] == "Personal"


def test_idempotency_detects_new_deduplicated_and_conflict(
    tmp_path: Path,
) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    repository.upsert_mcp_item(
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        external_calendar_or_list_id="calendar-1",
        title_hash="title-hash",
        time_start="2026-07-08T10:00:00+08:00",
        time_end="2026-07-08T11:00:00+08:00",
        status_semantics="scheduled",
        created_by_mcp=True,
    )

    initial = repository.check_idempotency_key(
        key="calendar:create:demo",
        operation="calendar.create_event",
        request_hash="request-hash-1",
    )
    repository.record_idempotency_success(
        key="calendar:create:demo",
        operation="calendar.create_event",
        request_hash="request-hash-1",
        result_item_id="calendar:event-1",
    )
    repeated = repository.check_idempotency_key(
        key="calendar:create:demo",
        operation="calendar.create_event",
        request_hash="request-hash-1",
    )
    conflict = repository.check_idempotency_key(
        key="calendar:create:demo",
        operation="calendar.create_event",
        request_hash="request-hash-2",
    )

    assert initial.decision == "new"
    assert initial.result_item_id is None
    assert repeated.decision == "deduplicated"
    assert repeated.result_item_id == "calendar:event-1"
    assert conflict.decision == "conflict"
    assert conflict.result_item_id == "calendar:event-1"


def test_records_opaque_source_reference_and_operation_audit(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    repository.upsert_mcp_item(
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        external_calendar_or_list_id="calendar-1",
        title_hash="title-hash",
        time_start="2026-07-08T10:00:00+08:00",
        time_end="2026-07-08T11:00:00+08:00",
        status_semantics="scheduled",
        created_by_mcp=True,
    )

    source_link_id = repository.record_source_link(
        target_item_id="calendar:event-1",
        source_ref="file:daily/2026-07-26.md",
        relation_type="created_from",
    )
    audit_id = repository.record_operation_audit(
        operation="calendar.create_event",
        target_item_id="calendar:event-1",
        request_hash="request-hash-1",
        result_status="succeeded",
        error_code=None,
        confirmed_by_user=True,
    )

    with repository.connect() as connection:
        source_link = connection.execute("SELECT * FROM source_link").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()

    assert source_link["id"] == source_link_id
    assert source_link["target_item_id"] == "calendar:event-1"
    assert source_link["source_ref"] == "file:daily/2026-07-26.md"
    assert source_link["relation_type"] == "created_from"
    assert audit["id"] == audit_id
    assert audit["operation"] == "calendar.create_event"
    assert audit["target_item_id"] == "calendar:event-1"
    assert audit["request_hash"] == "request-hash-1"
    assert audit["result_status"] == "succeeded"
    assert audit["confirmed_by_user"] == 1


def test_lists_external_item_contexts_in_one_batch(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    repository.upsert_mcp_item(
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        external_calendar_or_list_id="Personal",
        title_hash="calendar-title",
        time_start="2026-07-08T10:00:00+08:00",
        time_end="2026-07-08T11:00:00+08:00",
        status_semantics="planned",
        created_by_mcp=True,
    )
    repository.upsert_mcp_item(
        item_id="reminder:reminder-1",
        item_type="reminder",
        external_id="reminder-1",
        external_calendar_or_list_id="Personal",
        title_hash="reminder-title",
        time_start=None,
        time_end=None,
        status_semantics="planned",
        created_by_mcp=False,
    )
    repository.record_source_link(
        target_item_id="calendar:event-1",
        source_ref="file:daily/2026-07-26.md",
        relation_type="created_from",
    )

    contexts = repository.list_external_item_contexts(
        item_types=("calendar_event", "reminder"),
        targets=[
            ("event-1", "Personal"),
            ("reminder-1", "Personal"),
            ("missing", "Personal"),
        ],
    )

    calendar = contexts[("calendar_event", "event-1", "Personal")]
    reminder = contexts[("reminder", "reminder-1", "Personal")]
    assert calendar.item["id"] == "calendar:event-1"
    assert calendar.source_refs == ("file:daily/2026-07-26.md",)
    assert reminder.item["id"] == "reminder:reminder-1"
    assert reminder.source_refs == ()
    assert len(contexts) == 2
