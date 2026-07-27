"""Transactional SQLite schema creation and migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

LATEST_SCHEMA_VERSION = 3


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the database inside the caller's transaction."""
    current_version = current_schema_version(connection)
    if current_version is None:
        _create_v3_schema(connection)
    elif current_version == 1:
        _migrate_v1_to_v3(connection)
    elif current_version == 2:
        _migrate_v2_to_v3(connection)
    elif current_version != LATEST_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(f"Unsupported sidecar schema version: {current_version}")

    connection.execute(
        """
        UPDATE idempotency_key
        SET status = 'external_state_unknown',
            error_code = 'EXTERNAL_STATE_UNKNOWN',
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
        """
    )


def current_schema_version(connection: sqlite3.Connection) -> int | None:
    """Return the installed schema version, or None for a new database."""
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_version'
        """
    ).fetchone()
    if row is None:
        return None
    version_row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if version_row is None or version_row[0] is None:
        raise sqlite3.DatabaseError("Sidecar schema_version is empty")
    return int(version_row[0])


def _create_v3_schema(connection: sqlite3.Connection) -> None:
    for statement in _V3_SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (LATEST_SCHEMA_VERSION,),
    )


def _migrate_v1_to_v3(connection: sqlite3.Connection) -> None:
    old_items = connection.execute(
        """
        SELECT
            id,
            item_type,
            external_id,
            external_calendar_or_list_id,
            title_hash,
            time_start,
            time_end,
            status_semantics,
            created_by_mcp,
            deleted_at,
            created_at,
            updated_at
        FROM mcp_item
        ORDER BY id
        """
    ).fetchall()
    keeper_by_item_id = _keeper_map(old_items)

    for table_name in (
        "source",
        "mcp_item",
        "idempotency_key",
        "source_link",
        "operation_audit",
    ):
        connection.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_v1")

    for statement in _V3_SCHEMA_STATEMENTS[1:]:
        connection.execute(statement)

    connection.execute(
        """
        INSERT INTO source (
            id, source_type, source_name, source_uri, config_key, created_at, updated_at
        )
        SELECT
            id, source_type, source_name, source_uri, config_key, created_at, updated_at
        FROM source_v1
        """
    )
    _copy_merged_items(connection, old_items, keeper_by_item_id)
    _copy_v1_idempotency(connection, keeper_by_item_id)
    _copy_v1_source_links(connection, keeper_by_item_id)
    _copy_v1_audits(connection, keeper_by_item_id)

    for table_name in (
        "source_link_v1",
        "operation_audit_v1",
        "idempotency_key_v1",
        "mcp_item_v1",
        "source_v1",
    ):
        connection.execute(f"DROP TABLE {table_name}")
    _set_schema_version(connection)


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE source_link RENAME TO source_link_v2")
    connection.execute("ALTER TABLE operation_audit RENAME TO operation_audit_v2")
    connection.execute(_SOURCE_LINK_SCHEMA)
    connection.execute(_OPERATION_AUDIT_SCHEMA)

    connection.execute(
        """
        INSERT INTO source_link (
            id, target_item_id, source_ref, relation_type, created_at
        )
        SELECT
            id, target_item_id, source_ref, relation_type, created_at
        FROM source_link_v2
        WHERE target_item_id IS NOT NULL
        """
    )
    connection.execute(
        """
        INSERT INTO operation_audit (
            id,
            operation,
            target_item_id,
            request_hash,
            result_status,
            error_code,
            confirmed_by_user,
            created_at
        )
        SELECT
            id,
            operation,
            target_item_id,
            request_hash,
            result_status,
            error_code,
            confirmed_by_user,
            created_at
        FROM operation_audit_v2
        WHERE target_candidate_id IS NULL
        """
    )
    connection.execute("DROP TABLE source_link_v2")
    connection.execute("DROP TABLE operation_audit_v2")
    connection.execute("DROP TABLE action_candidate")
    _set_schema_version(connection)


def _set_schema_version(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM schema_version")
    connection.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (LATEST_SCHEMA_VERSION,),
    )


def _copy_v1_idempotency(
    connection: sqlite3.Connection,
    keeper_by_item_id: dict[str, str],
) -> None:
    rows = connection.execute(
        """
        SELECT
            id, key, operation, request_hash, result_item_id, status, created_at, updated_at
        FROM idempotency_key_v1
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        status = str(row["status"])
        error_code = None
        if status == "conflict":
            status = "failed"
            error_code = "IDEMPOTENCY_CONFLICT"
        connection.execute(
            """
            INSERT INTO idempotency_key (
                id,
                key,
                operation,
                request_hash,
                hash_version,
                result_item_id,
                status,
                error_code,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["key"],
                row["operation"],
                row["request_hash"],
                _mapped_item_id(row["result_item_id"], keeper_by_item_id),
                status,
                error_code,
                row["created_at"],
                row["updated_at"],
            ),
        )


def _copy_v1_source_links(
    connection: sqlite3.Connection,
    keeper_by_item_id: dict[str, str],
) -> None:
    rows = connection.execute(
        """
        SELECT id, target_item_id, source_ref, relation_type, created_at
        FROM source_link_v1
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO source_link (
                id,
                target_item_id,
                source_ref,
                relation_type,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                _mapped_item_id(row["target_item_id"], keeper_by_item_id),
                row["source_ref"],
                row["relation_type"],
                row["created_at"],
            ),
        )


def _copy_v1_audits(
    connection: sqlite3.Connection,
    keeper_by_item_id: dict[str, str],
) -> None:
    rows = connection.execute(
        """
        SELECT
            id,
            operation,
            target_item_id,
            request_hash,
            result_status,
            error_code,
            confirmed_by_user,
            created_at
        FROM operation_audit_v1
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO operation_audit (
                id,
                operation,
                target_item_id,
                request_hash,
                result_status,
                error_code,
                confirmed_by_user,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["operation"],
                _mapped_item_id(row["target_item_id"], keeper_by_item_id),
                row["request_hash"],
                row["result_status"],
                row["error_code"],
                row["confirmed_by_user"],
                row["created_at"],
            ),
        )


def _keeper_map(rows: Iterable[sqlite3.Row]) -> dict[str, str]:
    keeper_by_identity: dict[tuple[str, str, str], str] = {}
    keeper_by_item_id: dict[str, str] = {}
    for row in rows:
        item_id = str(row["id"])
        external_id = row["external_id"]
        external_container_id = row["external_calendar_or_list_id"]
        if external_id is None or external_container_id is None:
            keeper_by_item_id[item_id] = item_id
            continue
        identity = (
            str(row["item_type"]),
            str(external_id),
            str(external_container_id),
        )
        keeper_id = keeper_by_identity.setdefault(identity, item_id)
        keeper_by_item_id[item_id] = keeper_id
    return keeper_by_item_id


def _copy_merged_items(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    keeper_by_item_id: dict[str, str],
) -> None:
    rows_by_keeper: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        rows_by_keeper.setdefault(keeper_by_item_id[str(row["id"])], []).append(row)

    for keeper_id, matches in rows_by_keeper.items():
        keeper = next(row for row in matches if str(row["id"]) == keeper_id)
        connection.execute(
            """
            INSERT INTO mcp_item (
                id,
                item_type,
                external_id,
                external_container_id,
                title_hash,
                time_start,
                time_end,
                status_semantics,
                state_token,
                created_by_mcp,
                deleted_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                keeper_id,
                keeper["item_type"],
                keeper["external_id"],
                keeper["external_calendar_or_list_id"],
                keeper["title_hash"],
                keeper["time_start"],
                keeper["time_end"],
                keeper["status_semantics"],
                max(int(row["created_by_mcp"]) for row in matches),
                keeper["deleted_at"],
                min(str(row["created_at"]) for row in matches),
                max(str(row["updated_at"]) for row in matches),
            ),
        )


def _mapped_item_id(
    item_id: object,
    keeper_by_item_id: dict[str, str],
) -> str | None:
    if item_id is None:
        return None
    return keeper_by_item_id[str(item_id)]


_SOURCE_LINK_SCHEMA = """
CREATE TABLE source_link (
    id TEXT PRIMARY KEY,
    target_item_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN ('created_from', 'supported_by', 'updated_from')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_item_id) REFERENCES mcp_item(id) ON DELETE CASCADE
)
"""

_OPERATION_AUDIT_SCHEMA = """
CREATE TABLE operation_audit (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_item_id TEXT,
    request_hash TEXT NOT NULL,
    result_status TEXT NOT NULL,
    error_code TEXT,
    confirmed_by_user INTEGER NOT NULL CHECK (confirmed_by_user IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_item_id) REFERENCES mcp_item(id)
)
"""

_V3_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE source (
        id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL CHECK (source_type IN ('calendar', 'reminder')),
        source_name TEXT NOT NULL,
        source_uri TEXT NOT NULL,
        config_key TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE mcp_item (
        id TEXT PRIMARY KEY,
        item_type TEXT NOT NULL CHECK (
            item_type IN ('calendar_event', 'reminder', 'action_record')
        ),
        external_id TEXT,
        external_container_id TEXT,
        title_hash TEXT,
        time_start TEXT,
        time_end TEXT,
        status_semantics TEXT,
        state_token TEXT,
        created_by_mcp INTEGER NOT NULL CHECK (created_by_mcp IN (0, 1)),
        deleted_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX mcp_item_external_identity
    ON mcp_item (item_type, external_id, external_container_id)
    WHERE external_id IS NOT NULL AND external_container_id IS NOT NULL
    """,
    """
    CREATE TABLE calendar_event_state (
        item_id TEXT PRIMARY KEY,
        completion_status TEXT NOT NULL CHECK (
            completion_status IN ('unknown', 'incomplete', 'completed')
        ),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES mcp_item(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE idempotency_key (
        id TEXT PRIMARY KEY,
        key TEXT NOT NULL,
        operation TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        hash_version INTEGER NOT NULL CHECK (hash_version >= 1),
        result_item_id TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'succeeded', 'failed', 'external_state_unknown')
        ),
        error_code TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (key, operation),
        FOREIGN KEY (result_item_id) REFERENCES mcp_item(id)
    )
    """,
    _SOURCE_LINK_SCHEMA,
    _OPERATION_AUDIT_SCHEMA,
)
