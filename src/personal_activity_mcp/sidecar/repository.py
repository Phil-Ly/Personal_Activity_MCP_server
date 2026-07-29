"""Compact SQLite sidecar repository."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_EXPECTED_TABLES = {"mcp_item", "idempotency_key", "operation_audit"}
_APPLICATION_ID = 0x50414D43
_SCHEMA_VERSION = 2
_MAX_TARGETS_PER_QUERY = 400


def _normalize_schema_sql(sql: str) -> str:
    normalized = " ".join(sql.split())
    return normalized.replace(
        "CREATE TABLE IF NOT EXISTS ",
        "CREATE TABLE ",
        1,
    )


_EXPECTED_COLUMNS = {
    "mcp_item": {
        "id",
        "item_type",
        "external_id",
        "external_container_id",
        "status_semantics",
        "completion_status",
        "source_refs_json",
        "created_by_mcp",
    },
    "operation_audit": {
        "id",
        "operation",
        "target_item_id",
        "request_hash",
        "result_status",
        "error_code",
        "confirmed_by_user",
        "created_at",
    },
    "idempotency_key": {
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
    },
}

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS mcp_item (
        id TEXT PRIMARY KEY,
        item_type TEXT NOT NULL CHECK (
            item_type IN ('calendar_event', 'reminder', 'calendar')
        ),
        external_id TEXT NOT NULL,
        external_container_id TEXT NOT NULL,
        status_semantics TEXT CHECK (
            status_semantics IS NULL
            OR status_semantics IN ('planned', 'probable', 'confirmed')
        ),
        completion_status TEXT CHECK (
            completion_status IN ('unknown', 'incomplete', 'completed')
        ),
        source_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (
            json_valid(source_refs_json) AND json_type(source_refs_json) = 'array'
        ),
        created_by_mcp INTEGER NOT NULL CHECK (created_by_mcp IN (0, 1)),
        UNIQUE (item_type, external_id, external_container_id),
        CHECK (
            (
                item_type IN ('calendar_event', 'reminder')
                AND status_semantics IS NOT NULL
            )
            OR
            (
                item_type = 'calendar'
                AND status_semantics IS NULL
            )
        ),
        CHECK (
            (
                item_type = 'calendar_event'
                AND completion_status IS NOT NULL
            )
            OR
            (
                item_type IN ('reminder', 'calendar')
                AND completion_status IS NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operation_audit (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_key (
        key TEXT NOT NULL,
        operation TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        result_item_id TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'succeeded', 'failed', 'external_state_unknown')
        ),
        error_code TEXT,
        audit_id TEXT,
        confirmed_by_user INTEGER NOT NULL DEFAULT 0 CHECK (
            confirmed_by_user IN (0, 1)
        ),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (key, operation),
        FOREIGN KEY (result_item_id) REFERENCES mcp_item(id),
        FOREIGN KEY (audit_id) REFERENCES operation_audit(id)
    )
    """,
)
_EXPECTED_TABLE_SQL = {
    table_name: _normalize_schema_sql(statement)
    for table_name, statement in zip(
        ("mcp_item", "operation_audit", "idempotency_key"),
        _SCHEMA_STATEMENTS,
        strict=True,
    )
}


@dataclass(frozen=True)
class ExternalItemContext:
    """One external mapping and its opaque source references."""

    item: dict[str, object]
    source_refs: tuple[str, ...]
    completion_status: Literal["unknown", "incomplete", "completed"] = "unknown"


class SidecarRepository:
    """Manage the local SQLite sidecar database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path.expanduser().resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a SQLite connection using row objects and foreign keys."""
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the clean schema and recover abandoned write reservations."""
        database_exists = self._database_path.exists()
        self._secure_parent_directory()
        if database_exists:
            self._validate_existing_database()
            os.chmod(self._database_path, 0o600)
        else:
            descriptor = os.open(
                self._database_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
            os.close(descriptor)
        connection = sqlite3.connect(self._database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._recover_pending_operations(connection)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError("Sidecar schema has foreign key violations")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _secure_parent_directory(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_mode = self._database_path.parent.stat().st_mode & 0o777
        if parent_mode & 0o077:
            raise PermissionError(
                "Sidecar parent directory must not be accessible by group or other users"
            )

    def _validate_existing_database(self) -> None:
        try:
            with sqlite3.connect(
                f"{self._database_path.as_uri()}?mode=ro",
                uri=True,
            ) as connection:
                application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if (
                    application_id != _APPLICATION_ID
                    or user_version != _SCHEMA_VERSION
                    or _table_names(connection) != _EXPECTED_TABLES
                    or _table_sql(connection) != _EXPECTED_TABLE_SQL
                    or any(
                        _column_names(connection, table_name) != expected
                        for table_name, expected in _EXPECTED_COLUMNS.items()
                    )
                ):
                    raise sqlite3.DatabaseError(_INCOMPATIBLE_SCHEMA_MESSAGE)
        except sqlite3.DatabaseError as error:
            if str(error) == _INCOMPATIBLE_SCHEMA_MESSAGE:
                raise
            raise sqlite3.DatabaseError(_INCOMPATIBLE_SCHEMA_MESSAGE) from error

    def _recover_pending_operations(self, connection: sqlite3.Connection) -> None:
        pending_rows = connection.execute(
            """
            SELECT key, operation, request_hash, confirmed_by_user
            FROM idempotency_key
            WHERE status = 'pending'
            """
        ).fetchall()
        for row in pending_rows:
            audit_id = f"audit:{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO operation_audit (
                    id,
                    operation,
                    target_item_id,
                    request_hash,
                    result_status,
                    error_code,
                    confirmed_by_user
                )
                VALUES (?, ?, NULL, ?, 'external_state_unknown',
                        'EXTERNAL_STATE_UNKNOWN', ?)
                """,
                (
                    audit_id,
                    str(row["operation"]),
                    str(row["request_hash"]),
                    int(row["confirmed_by_user"]),
                ),
            )
            connection.execute(
                """
                UPDATE idempotency_key
                SET status = 'external_state_unknown',
                    error_code = 'EXTERNAL_STATE_UNKNOWN',
                    audit_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = ? AND operation = ? AND status = 'pending'
                """,
                (audit_id, str(row["key"]), str(row["operation"])),
            )

    def get_mcp_item(self, item_id: str) -> dict[str, object] | None:
        """Return one MCP item row as a plain dictionary."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_item WHERE id = ?",
                (item_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def find_mcp_item_by_external(
        self,
        *,
        item_type: str,
        external_id: str,
        external_container_id: str,
    ) -> dict[str, object] | None:
        """Return one MCP item row by external system identifiers."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM mcp_item
                WHERE item_type = ?
                  AND external_id = ?
                  AND external_container_id = ?
                """,
                (item_type, external_id, external_container_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_external_item_contexts(
        self,
        *,
        item_types: tuple[str, ...],
        targets: list[tuple[str, str]],
    ) -> dict[tuple[str, str, str], ExternalItemContext]:
        """Return external mappings and embedded source refs in one query."""
        unique_item_types = tuple(dict.fromkeys(item_types))
        unique_targets = tuple(dict.fromkeys(targets))
        if not unique_item_types or not unique_targets:
            return {}

        rows: list[sqlite3.Row] = []
        with self.connect() as connection:
            for start in range(0, len(unique_targets), _MAX_TARGETS_PER_QUERY):
                target_chunk = unique_targets[start : start + _MAX_TARGETS_PER_QUERY]
                rows.extend(
                    _query_external_item_context_rows(
                        connection,
                        item_types=unique_item_types,
                        targets=target_chunk,
                    )
                )

        contexts: dict[tuple[str, str, str], ExternalItemContext] = {}
        for row in rows:
            item = dict(row)
            key = (
                str(row["item_type"]),
                str(row["external_id"]),
                str(row["external_container_id"]),
            )
            contexts[key] = ExternalItemContext(
                item=item,
                source_refs=_decode_source_refs(row["source_refs_json"]),
                completion_status=_completion_status(row["completion_status"]),
            )
        return contexts


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


_INCOMPATIBLE_SCHEMA_MESSAGE = (
    "Incompatible sidecar schema; recreate the database for this pre-release build"
)


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _table_sql(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {str(row[0]): _normalize_schema_sql(str(row[1])) for row in rows if row[1] is not None}


def _query_external_item_context_rows(
    connection: sqlite3.Connection,
    *,
    item_types: tuple[str, ...],
    targets: tuple[tuple[str, str], ...],
) -> list[sqlite3.Row]:
    item_type_placeholders = ", ".join("?" for _ in item_types)
    target_predicates = " OR ".join(
        "(external_id = ? AND external_container_id = ?)" for _ in targets
    )
    parameters: list[str] = list(item_types)
    for external_id, container_id in targets:
        parameters.extend((external_id, container_id))
    return connection.execute(
        f"""
        SELECT *
        FROM mcp_item
        WHERE item_type IN ({item_type_placeholders})
          AND ({target_predicates})
        ORDER BY item_type, external_id, external_container_id, id
        """,
        parameters,
    ).fetchall()


def _decode_source_refs(raw_value: object) -> tuple[str, ...]:
    try:
        values = json.loads(str(raw_value))
    except (TypeError, ValueError) as error:
        raise sqlite3.DatabaseError("mcp_item.source_refs_json is invalid") from error
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise sqlite3.DatabaseError("mcp_item.source_refs_json must be a string array")
    return tuple(values)


def _completion_status(
    raw_value: object,
) -> Literal["unknown", "incomplete", "completed"]:
    if raw_value is None:
        return "unknown"
    value = str(raw_value)
    if value not in {"unknown", "incomplete", "completed"}:
        raise sqlite3.DatabaseError("mcp_item.completion_status is invalid")
    return value  # type: ignore[return-value]
