import json
import sqlite3
import stat
from pathlib import Path

import pytest

from personal_activity_mcp.sidecar import SidecarRepository


def table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def column_names(database_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def insert_item(
    repository: SidecarRepository,
    *,
    item_id: str,
    item_type: str,
    external_id: str,
    completion_status: str | None,
    source_refs: list[str],
    created_by_mcp: bool,
) -> None:
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO mcp_item (
                id,
                item_type,
                external_id,
                external_container_id,
                status_semantics,
                completion_status,
                source_refs_json,
                created_by_mcp
            )
            VALUES (?, ?, ?, 'Personal', 'planned', ?, ?, ?)
            """,
            (
                item_id,
                item_type,
                external_id,
                completion_status,
                json.dumps(source_refs),
                1 if created_by_mcp else 0,
            ),
        )


def test_initialize_creates_only_clean_schema_with_private_filesystem_modes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "secure-sidecar" / "state.sqlite3"

    SidecarRepository(database_path).initialize()

    assert table_names(database_path) == {
        "idempotency_key",
        "mcp_item",
        "operation_audit",
    }
    assert column_names(database_path, "mcp_item") == {
        "id",
        "item_type",
        "external_id",
        "external_container_id",
        "status_semantics",
        "completion_status",
        "source_refs_json",
        "created_by_mcp",
    }
    assert column_names(database_path, "idempotency_key") == {
        "key",
        "operation",
        "request_hash",
        "result_item_id",
        "status",
        "error_code",
        "audit_id",
        "confirmed_by_user",
        "created_at",
        "updated_at",
    }
    assert column_names(database_path, "operation_audit") == {
        "id",
        "operation",
        "target_item_id",
        "request_hash",
        "result_status",
        "error_code",
        "confirmed_by_user",
        "created_at",
    }
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_initialize_rejects_insecure_existing_parent_without_changing_mode(
    tmp_path: Path,
) -> None:
    database_parent = tmp_path / "shared"
    database_parent.mkdir(mode=0o755)
    database_parent.chmod(0o755)
    database_path = database_parent / "state.sqlite3"

    with pytest.raises(PermissionError, match="must not be accessible"):
        SidecarRepository(database_path).initialize()

    assert stat.S_IMODE(database_parent.stat().st_mode) == 0o755


def test_initialize_rejects_an_old_schema_instead_of_migrating_it(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_version (version) VALUES (3)")

    with pytest.raises(sqlite3.DatabaseError, match="Incompatible sidecar schema"):
        SidecarRepository(database_path).initialize()

    assert table_names(database_path) == {"schema_version"}
    assert list(tmp_path.glob("*.pre-v*.sqlite3")) == []


def test_initialize_rejects_same_table_names_with_wrong_schema_without_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE mcp_item (id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE operation_audit (id TEXT PRIMARY KEY)")
        connection.execute(
            """
            CREATE TABLE idempotency_key (
                status TEXT,
                audit_id TEXT,
                error_code TEXT,
                updated_at TEXT
            )
            """
        )
    database_path.chmod(0o640)
    original_bytes = database_path.read_bytes()

    with pytest.raises(sqlite3.DatabaseError, match="Incompatible sidecar schema"):
        SidecarRepository(database_path).initialize()

    assert database_path.read_bytes() == original_bytes
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o640


def test_initialize_rejects_matching_columns_without_required_constraints(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE mcp_item (
                id TEXT,
                item_type TEXT,
                external_id TEXT,
                external_container_id TEXT,
                status_semantics TEXT,
                completion_status TEXT,
                source_refs_json TEXT,
                created_by_mcp INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE operation_audit (
                id TEXT,
                operation TEXT,
                target_item_id TEXT,
                request_hash TEXT,
                result_status TEXT,
                error_code TEXT,
                confirmed_by_user INTEGER,
                created_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE idempotency_key (
                key TEXT,
                operation TEXT,
                request_hash TEXT,
                result_item_id TEXT,
                status TEXT,
                error_code TEXT,
                audit_id TEXT,
                confirmed_by_user INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute("PRAGMA application_id = 0x50414D43")
        connection.execute("PRAGMA user_version = 1")
    database_path.chmod(0o640)
    original_bytes = database_path.read_bytes()

    with pytest.raises(sqlite3.DatabaseError, match="Incompatible sidecar schema"):
        SidecarRepository(database_path).initialize()

    assert database_path.read_bytes() == original_bytes
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o640


def test_initialize_marks_only_abandoned_pending_operations_unknown(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    repository = SidecarRepository(database_path)
    repository.initialize()
    with repository.connect() as connection:
        connection.executemany(
            """
            INSERT INTO idempotency_key (
                key, operation, request_hash, status
            )
            VALUES (?, 'calendar.create_event', ?, ?)
            """,
            [
                ("pending-key", "hash-1", "pending"),
                ("failed-key", "hash-2", "failed"),
                ("succeeded-key", "hash-3", "succeeded"),
            ],
        )

    SidecarRepository(database_path).initialize()

    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT key, status, error_code FROM idempotency_key ORDER BY key"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("failed-key", "failed", None),
        ("pending-key", "external_state_unknown", "EXTERNAL_STATE_UNKNOWN"),
        ("succeeded-key", "succeeded", None),
    ]
    with repository.connect() as connection:
        recovered = connection.execute(
            """
            SELECT
                a.operation,
                a.request_hash,
                a.result_status,
                a.error_code,
                a.confirmed_by_user,
                i.audit_id
            FROM idempotency_key AS i
            JOIN operation_audit AS a ON a.id = i.audit_id
            WHERE i.key = 'pending-key'
            """
        ).fetchone()
    assert tuple(recovered) == (
        "calendar.create_event",
        "hash-1",
        "external_state_unknown",
        "EXTERNAL_STATE_UNKNOWN",
        0,
        recovered["audit_id"],
    )


def test_clean_schema_rejects_removed_action_record_type(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "state.sqlite3")
    repository.initialize()

    with pytest.raises(sqlite3.IntegrityError), repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO mcp_item (
                id,
                item_type,
                external_id,
                external_container_id,
                status_semantics,
                completion_status,
                source_refs_json,
                created_by_mcp
            )
            VALUES (
                'legacy:1',
                'action_record',
                'event-1',
                'Personal',
                'planned',
                'unknown',
                '[]',
                1
            )
            """
        )


def test_lists_external_item_contexts_from_compact_item_rows(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    insert_item(
        repository,
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        completion_status="completed",
        source_refs=["file:daily/2026-07-26.md"],
        created_by_mcp=True,
    )
    insert_item(
        repository,
        item_id="reminder:reminder-1",
        item_type="reminder",
        external_id="reminder-1",
        completion_status=None,
        source_refs=[],
        created_by_mcp=False,
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
    assert calendar.completion_status == "completed"
    assert reminder.item["id"] == "reminder:reminder-1"
    assert reminder.source_refs == ()
    assert reminder.completion_status == "unknown"
    assert len(contexts) == 2


def test_lists_external_item_contexts_in_bounded_query_chunks(tmp_path: Path) -> None:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    targets = [(f"event-{index}", "Personal") for index in range(1_200)]
    with repository.connect() as connection:
        connection.executemany(
            """
            INSERT INTO mcp_item (
                id,
                item_type,
                external_id,
                external_container_id,
                status_semantics,
                completion_status,
                source_refs_json,
                created_by_mcp
            )
            VALUES (?, 'calendar_event', ?, ?, 'planned', 'unknown', '[]', 0)
            """,
            [
                (f"calendar:{external_id}", external_id, container_id)
                for external_id, container_id in targets
            ],
        )

    contexts = repository.list_external_item_contexts(
        item_types=("calendar_event",),
        targets=targets,
    )

    assert len(contexts) == 1_200
    assert contexts[("calendar_event", "event-1199", "Personal")].item["id"] == (
        "calendar:event-1199"
    )
