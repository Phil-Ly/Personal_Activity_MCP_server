import sqlite3
import stat
from pathlib import Path

import pytest

from personal_activity_mcp.sidecar import SidecarRepository

V1_SCHEMA = """
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_version (version) VALUES (1);

CREATE TABLE source (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    config_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mcp_item (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    external_id TEXT,
    external_calendar_or_list_id TEXT,
    title_hash TEXT,
    time_start TEXT,
    time_end TEXT,
    status_semantics TEXT,
    created_by_mcp INTEGER NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE idempotency_key (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_item_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (result_item_id) REFERENCES mcp_item(id)
);

CREATE TABLE source_link (
    id TEXT PRIMARY KEY,
    target_item_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_item_id) REFERENCES mcp_item(id)
);

CREATE TABLE operation_audit (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_item_id TEXT,
    request_hash TEXT NOT NULL,
    result_status TEXT NOT NULL,
    error_code TEXT,
    confirmed_by_user INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_item_id) REFERENCES mcp_item(id)
);
"""


def create_v1_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(V1_SCHEMA)
    return connection


def table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def column_names(database_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def test_new_database_uses_schema_v2_and_private_filesystem_modes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "secure-sidecar" / "state.sqlite3"
    repository = SidecarRepository(database_path)

    repository.initialize()

    assert table_names(database_path) == {
        "action_candidate",
        "calendar_event_state",
        "idempotency_key",
        "mcp_item",
        "operation_audit",
        "schema_version",
        "source",
        "source_link",
    }
    assert column_names(database_path, "mcp_item") >= {
        "external_container_id",
        "state_token",
    }
    assert "external_calendar_or_list_id" not in column_names(database_path, "mcp_item")
    assert column_names(database_path, "idempotency_key") >= {
        "hash_version",
        "status",
    }
    assert column_names(database_path, "source_link") >= {
        "target_item_id",
        "target_candidate_id",
    }
    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
    assert versions == [(2,)]
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert repository.last_migration_backup_path is None
    assert list(database_path.parent.glob("*.pre-v2-*.sqlite3")) == []


def test_initialize_rejects_insecure_existing_parent_without_changing_its_mode(
    tmp_path: Path,
) -> None:
    sidecar_parent = tmp_path / "shared"
    sidecar_parent.mkdir(mode=0o755)
    sidecar_parent.chmod(0o755)
    database_path = sidecar_parent / "state.sqlite3"

    with pytest.raises(PermissionError, match="parent directory"):
        SidecarRepository(database_path).initialize()

    assert stat.S_IMODE(sidecar_parent.stat().st_mode) == 0o755
    assert not database_path.exists()


def test_schema_v1_migration_preserves_existing_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    with create_v1_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO source (id, source_type, source_name, source_uri, config_key)
            VALUES ('calendar:Personal', 'calendar', 'Personal',
                    'calendar://Personal', 'Personal')
            """
        )
        connection.execute(
            """
            INSERT INTO mcp_item (
                id, item_type, external_id, external_calendar_or_list_id,
                title_hash, time_start, time_end, status_semantics, created_by_mcp
            )
            VALUES (
                'calendar:event-1', 'calendar_event', 'event-1', 'Personal',
                'title-hash', '2026-07-08T10:00:00+08:00',
                '2026-07-08T11:00:00+08:00', 'planned', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO idempotency_key (
                id, key, operation, request_hash, result_item_id, status
            )
            VALUES (
                'idem-1', 'calendar:create:1', 'calendar.create_event',
                'request-hash', 'calendar:event-1', 'succeeded'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_link (
                id, target_item_id, source_ref, relation_type
            )
            VALUES (
                'link-1', 'calendar:event-1',
                'file:daily/2026-07-26.md', 'created_from'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO operation_audit (
                id, operation, target_item_id, request_hash, result_status,
                error_code, confirmed_by_user
            )
            VALUES (
                'audit-1', 'calendar.create_event', 'calendar:event-1',
                'request-hash', 'succeeded', NULL, 1
            )
            """
        )

    SidecarRepository(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute("SELECT * FROM source").fetchone()
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        source_link = connection.execute("SELECT * FROM source_link").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert source["id"] == "calendar:Personal"
    assert item["id"] == "calendar:event-1"
    assert item["external_container_id"] == "Personal"
    assert item["state_token"] is None
    assert idempotency["result_item_id"] == "calendar:event-1"
    assert idempotency["hash_version"] == 1
    assert idempotency["status"] == "succeeded"
    assert source_link["target_item_id"] == "calendar:event-1"
    assert source_link["target_candidate_id"] is None
    assert audit["id"] == "audit-1"
    assert audit["target_item_id"] == "calendar:event-1"


def test_schema_v1_migration_creates_private_pre_v2_backup(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    with create_v1_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO source (id, source_type, source_name, source_uri, config_key)
            VALUES ('calendar:Personal', 'calendar', 'Personal',
                    'calendar://Personal', 'Personal')
            """
        )

    repository = SidecarRepository(database_path)
    repository.initialize()

    backup_paths = list(tmp_path.glob("state.pre-v2-*.sqlite3"))
    assert len(backup_paths) == 1
    backup_path = backup_paths[0]
    assert repository.last_migration_backup_path == backup_path
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert table_names(backup_path) == {
        "idempotency_key",
        "mcp_item",
        "operation_audit",
        "schema_version",
        "source",
        "source_link",
    }
    with sqlite3.connect(backup_path) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        source_id = connection.execute("SELECT id FROM source").fetchone()[0]
    assert version == 1
    assert source_id == "calendar:Personal"


def test_schema_v1_migration_merges_duplicate_external_items_and_links(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    with create_v1_database(database_path) as connection:
        for item_id in ("z-item", "a-item"):
            connection.execute(
                """
                INSERT INTO mcp_item (
                    id, item_type, external_id, external_calendar_or_list_id,
                    title_hash, status_semantics, created_by_mcp
                )
                VALUES (?, 'calendar_event', 'event-1', 'Personal',
                        ?, 'planned', 1)
                """,
                (item_id, f"hash-{item_id}"),
            )
            connection.execute(
                """
                INSERT INTO source_link (
                    id, target_item_id, source_ref, relation_type
                )
                VALUES (?, ?, ?, 'created_from')
                """,
                (f"link-{item_id}", item_id, f"file:{item_id}"),
            )
        connection.execute(
            """
            INSERT INTO idempotency_key (
                id, key, operation, request_hash, result_item_id, status
            )
            VALUES (
                'idem-1', 'calendar:create:1', 'calendar.create_event',
                'request-hash', 'z-item', 'succeeded'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO operation_audit (
                id, operation, target_item_id, request_hash, result_status,
                error_code, confirmed_by_user
            )
            VALUES (
                'audit-1', 'calendar.create_event', 'z-item',
                'request-hash', 'succeeded', NULL, 1
            )
            """
        )

    SidecarRepository(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        items = connection.execute("SELECT * FROM mcp_item").fetchall()
        links = connection.execute(
            "SELECT target_item_id, source_ref FROM source_link ORDER BY source_ref"
        ).fetchall()
        result_item_id = connection.execute(
            "SELECT result_item_id FROM idempotency_key"
        ).fetchone()[0]
        audit_target = connection.execute("SELECT target_item_id FROM operation_audit").fetchone()[
            0
        ]
    assert [row["id"] for row in items] == ["a-item"]
    assert [(row["target_item_id"], row["source_ref"]) for row in links] == [
        ("a-item", "file:a-item"),
        ("a-item", "file:z-item"),
    ]
    assert result_item_id == "a-item"
    assert audit_target == "a-item"


def test_schema_v1_migration_rolls_back_all_changes_on_failure(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    with create_v1_database(database_path) as connection:
        connection.execute("ALTER TABLE mcp_item RENAME COLUMN external_id TO broken_external_id")

    with pytest.raises(sqlite3.OperationalError):
        SidecarRepository(database_path).initialize()

    assert table_names(database_path) == {
        "idempotency_key",
        "mcp_item",
        "operation_audit",
        "schema_version",
        "source",
        "source_link",
    }
    assert column_names(database_path, "mcp_item") >= {
        "broken_external_id",
        "external_calendar_or_list_id",
    }
    with sqlite3.connect(database_path) as connection:
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
    assert versions == [(1,)]
    backup_paths = list(tmp_path.glob("state.pre-v2-*.sqlite3"))
    assert len(backup_paths) == 1
    with sqlite3.connect(backup_paths[0]) as connection:
        backup_versions = connection.execute("SELECT version FROM schema_version").fetchall()
    assert backup_versions == [(1,)]


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
                id, key, operation, request_hash, hash_version, status
            )
            VALUES (?, ?, ?, ?, 2, ?)
            """,
            [
                ("pending-id", "pending-key", "calendar.create_event", "hash-1", "pending"),
                ("failed-id", "failed-key", "calendar.create_event", "hash-2", "failed"),
                (
                    "unknown-id",
                    "unknown-key",
                    "calendar.create_event",
                    "hash-3",
                    "external_state_unknown",
                ),
            ],
        )

    repository.initialize()

    with repository.connect() as connection:
        statuses = dict(
            connection.execute("SELECT key, status FROM idempotency_key ORDER BY key").fetchall()
        )
    assert statuses == {
        "failed-key": "failed",
        "pending-key": "external_state_unknown",
        "unknown-key": "external_state_unknown",
    }


def test_source_link_requires_exactly_one_existing_target(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    repository = SidecarRepository(database_path)
    repository.initialize()
    repository.upsert_mcp_item(
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        external_container_id="Personal",
        title_hash=None,
        time_start=None,
        time_end=None,
        status_semantics="planned",
        created_by_mcp=True,
    )
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO action_candidate (
                candidate_id,
                version,
                action_type,
                payload_json,
                decision_status,
                execution_status
            )
            VALUES (
                'candidate-1', 1, 'none', '{}', 'pending', 'not_started'
            )
            """
        )

    with pytest.raises(sqlite3.IntegrityError), repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO source_link (
                id, target_item_id, target_candidate_id, source_ref, relation_type
            )
            VALUES (
                'invalid-both', 'calendar:event-1', 'candidate-1',
                'file:a', 'supported_by'
            )
            """
        )
    with pytest.raises(sqlite3.IntegrityError), repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO source_link (
                id, target_item_id, target_candidate_id, source_ref, relation_type
            )
            VALUES ('invalid-neither', NULL, NULL, 'file:a', 'supported_by')
            """
        )

    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO source_link (
                id, target_item_id, target_candidate_id, source_ref, relation_type
            )
            VALUES ('candidate-link', NULL, 'candidate-1', 'file:a', 'supported_by')
            """
        )
        candidate_link = connection.execute(
            "SELECT * FROM source_link WHERE id = 'candidate-link'"
        ).fetchone()
    assert candidate_link["target_item_id"] is None
    assert candidate_link["target_candidate_id"] == "candidate-1"
