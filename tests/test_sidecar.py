from datetime import date
from pathlib import Path

from personal_activity_mcp.config import AppConfig, CalendarSource, JournalSource, ReminderSource
from personal_activity_mcp.journal import JournalRepository
from personal_activity_mcp.sidecar import SidecarRepository


def table_names(database_path: Path) -> set[str]:
    repository = SidecarRepository(database_path)
    with repository.connect() as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_initialize_creates_required_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "personal_activity.sqlite3"

    repository = SidecarRepository(database_path)
    repository.initialize()

    assert table_names(database_path) >= {
        "source",
        "journal_entry",
        "mcp_item",
        "idempotency_key",
        "provenance_link",
        "operation_audit",
        "schema_version",
    }


def test_upsert_journal_entry_stores_metadata_without_body(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    entry_path = journal_path / "2026-07-03.md"
    entry_path.write_text(
        "---\ntitle: Sidecar note\n---\n\nSECRET BODY MUST NOT BE STORED",
        encoding="utf-8",
    )
    source = JournalSource("daily", journal_path.resolve(), (".md", ".txt"))
    journal_repository = JournalRepository(AppConfig((source,), tmp_path / "unused.sqlite3"))
    entry = journal_repository.list_entries(
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 3),
    ).entries[0]
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()

    repository.upsert_journal_source(source)
    repository.upsert_journal_entry(entry)

    with repository.connect() as connection:
        source_row = connection.execute("SELECT * FROM source").fetchone()
        entry_row = connection.execute("SELECT * FROM journal_entry").fetchone()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(journal_entry)").fetchall()
        }

    assert source_row["id"] == "journal:daily"
    assert source_row["source_uri"] == journal_path.resolve().as_uri()
    assert entry_row["id"] == entry.evidence_id
    assert entry_row["source_id"] == "journal:daily"
    assert entry_row["entry_date"] == "2026-07-03"
    assert entry_row["file_path"] == entry.path
    assert entry_row["title"] == "Sidecar note"
    assert entry_row["content_hash"] == entry.content_hash
    assert "content" not in columns
    assert "SECRET BODY MUST NOT BE STORED" not in " ".join(str(value) for value in entry_row)


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


def test_records_provenance_and_operation_audit(tmp_path: Path) -> None:
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

    provenance_id = repository.record_provenance_link(
        target_item_id="calendar:event-1",
        evidence_type="journal_entry",
        evidence_id="journal:abc123",
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
        provenance = connection.execute("SELECT * FROM provenance_link").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()

    assert provenance["id"] == provenance_id
    assert provenance["target_item_id"] == "calendar:event-1"
    assert provenance["evidence_type"] == "journal_entry"
    assert provenance["evidence_id"] == "journal:abc123"
    assert provenance["relation_type"] == "created_from"
    assert audit["id"] == audit_id
    assert audit["operation"] == "calendar.create_event"
    assert audit["target_item_id"] == "calendar:event-1"
    assert audit["request_hash"] == "request-hash-1"
    assert audit["result_status"] == "succeeded"
    assert audit["confirmed_by_user"] == 1
