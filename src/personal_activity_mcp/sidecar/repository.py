"""SQLite sidecar repository."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_activity_mcp.config import CalendarSource, ReminderSource


@dataclass(frozen=True)
class IdempotencyDecision:
    """Result of checking whether a write operation can proceed."""

    decision: Literal["new", "deduplicated", "conflict"]
    result_item_id: str | None


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
        """Create the sidecar database schema if it does not already exist."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(_SCHEMA_SQL)

    def upsert_calendar_source(self, source: CalendarSource) -> str:
        """Store metadata for one configured Calendar source."""
        source_id = f"calendar:{source.calendar_id}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source (
                    id, source_type, source_name, source_uri, config_key
                )
                VALUES (?, 'calendar', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_name = excluded.source_name,
                    source_uri = excluded.source_uri,
                    config_key = excluded.config_key,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source_id,
                    source.title,
                    f"calendar://{source.calendar_id}",
                    source.calendar_id,
                ),
            )
        return source_id

    def upsert_reminder_source(self, source: ReminderSource) -> str:
        """Store metadata for one configured Reminder list source."""
        source_id = f"reminder:{source.list_id}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source (
                    id, source_type, source_name, source_uri, config_key
                )
                VALUES (?, 'reminder', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_name = excluded.source_name,
                    source_uri = excluded.source_uri,
                    config_key = excluded.config_key,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source_id,
                    source.title,
                    f"reminder://{source.list_id}",
                    source.list_id,
                ),
            )
        return source_id

    def upsert_mcp_item(
        self,
        *,
        item_id: str,
        item_type: Literal["calendar_event", "reminder", "action_record"],
        external_id: str | None,
        external_calendar_or_list_id: str | None,
        title_hash: str | None,
        time_start: str | None,
        time_end: str | None,
        status_semantics: str | None,
        created_by_mcp: bool,
    ) -> str:
        """Store an MCP-managed external item mapping."""
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_item (
                    id,
                    item_type,
                    external_id,
                    external_calendar_or_list_id,
                    title_hash,
                    time_start,
                    time_end,
                    status_semantics,
                    created_by_mcp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    item_type = excluded.item_type,
                    external_id = excluded.external_id,
                    external_calendar_or_list_id = excluded.external_calendar_or_list_id,
                    title_hash = excluded.title_hash,
                    time_start = excluded.time_start,
                    time_end = excluded.time_end,
                    status_semantics = excluded.status_semantics,
                    created_by_mcp = excluded.created_by_mcp,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_id,
                    item_type,
                    external_id,
                    external_calendar_or_list_id,
                    title_hash,
                    time_start,
                    time_end,
                    status_semantics,
                    1 if created_by_mcp else 0,
                ),
            )
        return item_id

    def get_mcp_item(self, item_id: str) -> dict[str, object] | None:
        """Return one MCP item row as a plain dictionary."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_item WHERE id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def find_mcp_item_by_external(
        self,
        *,
        item_type: str,
        external_id: str,
        external_calendar_or_list_id: str,
    ) -> dict[str, object] | None:
        """Return one MCP item row by external system identifiers."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM mcp_item
                WHERE item_type = ?
                  AND external_id = ?
                  AND external_calendar_or_list_id = ?
                  AND deleted_at IS NULL
                """,
                (item_type, external_id, external_calendar_or_list_id),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_source_refs(self, target_item_id: str) -> list[str]:
        """Return opaque source references for one MCP item."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_ref
                FROM source_link
                WHERE target_item_id = ?
                ORDER BY created_at, id
                """,
                (target_item_id,),
            ).fetchall()
        return [str(row["source_ref"]) for row in rows]

    def check_idempotency_key(
        self,
        *,
        key: str,
        operation: str,
        request_hash: str,
    ) -> IdempotencyDecision:
        """Check whether a write request is new, duplicated, or conflicting."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT operation, request_hash, result_item_id
                FROM idempotency_key
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return IdempotencyDecision("new", None)
        if row["operation"] == operation and row["request_hash"] == request_hash:
            return IdempotencyDecision("deduplicated", row["result_item_id"])
        return IdempotencyDecision("conflict", row["result_item_id"])

    def record_idempotency_success(
        self,
        *,
        key: str,
        operation: str,
        request_hash: str,
        result_item_id: str,
    ) -> str:
        """Persist a successful idempotent write result."""
        row_id = f"idempotency:{key}"
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT operation, request_hash
                FROM idempotency_key
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
            if existing is not None and (
                existing["operation"] != operation or existing["request_hash"] != request_hash
            ):
                raise ValueError("idempotency key conflicts with an existing request")
            connection.execute(
                """
                INSERT INTO idempotency_key (
                    id, key, operation, request_hash, result_item_id, status
                )
                VALUES (?, ?, ?, ?, ?, 'succeeded')
                ON CONFLICT(key) DO UPDATE SET
                    result_item_id = excluded.result_item_id,
                    status = 'succeeded',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (row_id, key, operation, request_hash, result_item_id),
            )
        return row_id

    def record_source_link(
        self,
        *,
        target_item_id: str,
        source_ref: str,
        relation_type: Literal["created_from", "supported_by", "updated_from"],
    ) -> str:
        """Persist an opaque source reference without resolving its provider."""
        row_id = ":".join(
            [
                "source",
                target_item_id,
                source_ref,
                relation_type,
            ]
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_link (
                    id, target_item_id, source_ref, relation_type
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (row_id, target_item_id, source_ref, relation_type),
            )
        return row_id

    def record_operation_audit(
        self,
        *,
        operation: str,
        target_item_id: str | None,
        request_hash: str,
        result_status: str,
        error_code: str | None,
        confirmed_by_user: bool,
    ) -> str:
        """Append one operation audit record."""
        row_id = f"audit:{uuid.uuid4().hex}"
        with self.connect() as connection:
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
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    operation,
                    target_item_id,
                    request_hash,
                    result_status,
                    error_code,
                    1 if confirmed_by_user else 0,
                ),
            )
        return row_id


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);

CREATE TABLE IF NOT EXISTS source (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('calendar', 'reminder')),
    source_name TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    config_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mcp_item (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL CHECK (
        item_type IN ('calendar_event', 'reminder', 'action_record')
    ),
    external_id TEXT,
    external_calendar_or_list_id TEXT,
    title_hash TEXT,
    time_start TEXT,
    time_end TEXT,
    status_semantics TEXT,
    created_by_mcp INTEGER NOT NULL CHECK (created_by_mcp IN (0, 1)),
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idempotency_key (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_item_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'conflict')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (result_item_id) REFERENCES mcp_item(id)
);

CREATE TABLE IF NOT EXISTS source_link (
    id TEXT PRIMARY KEY,
    target_item_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN ('created_from', 'supported_by', 'updated_from')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_item_id) REFERENCES mcp_item(id)
);

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
);
"""
