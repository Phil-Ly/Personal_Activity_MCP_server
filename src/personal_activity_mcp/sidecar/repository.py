"""SQLite sidecar repository."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_activity_mcp.config import CalendarSource, ReminderSource
from personal_activity_mcp.sidecar.migrations import (
    current_schema_version,
    initialize_schema,
)


@dataclass(frozen=True)
class IdempotencyDecision:
    """Result of checking whether a write operation can proceed."""

    decision: Literal["new", "deduplicated", "conflict"]
    result_item_id: str | None


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
        self._last_migration_backup_path: Path | None = None

    @property
    def last_migration_backup_path(self) -> Path | None:
        """Return the backup created by this repository instance, if any."""
        return self._last_migration_backup_path

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
        """Create or transactionally migrate the private sidecar database."""
        self._secure_database_path()
        connection = sqlite3.connect(self._database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            if current_schema_version(connection) == 1:
                self._last_migration_backup_path = self._create_pre_v2_backup(connection)
            connection.execute("BEGIN IMMEDIATE")
            initialize_schema(connection)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError("Sidecar migration produced foreign key violations")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.close()

    def _secure_database_path(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_mode = self._database_path.parent.stat().st_mode & 0o777
        if parent_mode & 0o077:
            raise PermissionError(
                "Sidecar parent directory must not be accessible by group or other users"
            )
        descriptor = os.open(self._database_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self._database_path, 0o600)

    def _create_pre_v2_backup(self, connection: sqlite3.Connection) -> Path:
        backup_path = self._database_path.with_name(
            f"{self._database_path.stem}.pre-v2-{uuid.uuid4().hex}.sqlite3"
        )
        descriptor = os.open(
            backup_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            0o600,
        )
        os.close(descriptor)
        backup_connection: sqlite3.Connection | None = None
        try:
            backup_connection = sqlite3.connect(backup_path)
            connection.backup(backup_connection)
            backup_connection.close()
            backup_connection = None
            os.chmod(backup_path, 0o600)
        except Exception:
            if backup_connection is not None:
                backup_connection.close()
            backup_path.unlink(missing_ok=True)
            raise
        return backup_path

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
        external_container_id: str | None,
        title_hash: str | None,
        time_start: str | None,
        time_end: str | None,
        status_semantics: str | None,
        created_by_mcp: bool,
        state_token: str | None = None,
    ) -> str:
        """Store an MCP-managed external item mapping."""
        with self.connect() as connection:
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
                    created_by_mcp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    item_type = excluded.item_type,
                    external_id = excluded.external_id,
                    external_container_id = excluded.external_container_id,
                    title_hash = excluded.title_hash,
                    time_start = excluded.time_start,
                    time_end = excluded.time_end,
                    status_semantics = excluded.status_semantics,
                    state_token = excluded.state_token,
                    created_by_mcp = excluded.created_by_mcp,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_id,
                    item_type,
                    external_id,
                    external_container_id,
                    title_hash,
                    time_start,
                    time_end,
                    status_semantics,
                    state_token,
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
                  AND deleted_at IS NULL
                """,
                (item_type, external_id, external_container_id),
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

    def set_calendar_completion_status(
        self,
        *,
        item_id: str,
        completion_status: Literal["unknown", "incomplete", "completed"],
    ) -> None:
        """Store completion independently from Calendar time semantics."""
        if completion_status not in {"unknown", "incomplete", "completed"}:
            raise ValueError("completion_status is invalid")
        with self.connect() as connection:
            item = connection.execute(
                """
                SELECT item_type
                FROM mcp_item
                WHERE id = ? AND deleted_at IS NULL
                """,
                (item_id,),
            ).fetchone()
            if item is None or item["item_type"] != "calendar_event":
                raise ValueError("Calendar event mapping is missing")
            connection.execute(
                """
                INSERT INTO calendar_event_state (item_id, completion_status)
                VALUES (?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    completion_status = excluded.completion_status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (item_id, completion_status),
            )

    def list_external_item_contexts(
        self,
        *,
        item_types: tuple[str, ...],
        targets: list[tuple[str, str]],
    ) -> dict[tuple[str, str, str], ExternalItemContext]:
        """Return external mappings and source refs without per-item queries."""
        unique_item_types = tuple(dict.fromkeys(item_types))
        unique_targets = tuple(dict.fromkeys(targets))
        if not unique_item_types or not unique_targets:
            return {}

        item_type_placeholders = ", ".join("?" for _ in unique_item_types)
        target_predicates = " OR ".join(
            "(external_id = ? AND external_container_id = ?)" for _ in unique_targets
        )
        parameters: list[str] = list(unique_item_types)
        for external_id, container_id in unique_targets:
            parameters.extend((external_id, container_id))

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM mcp_item
                WHERE deleted_at IS NULL
                  AND item_type IN ({item_type_placeholders})
                  AND ({target_predicates})
                ORDER BY
                    item_type,
                    external_id,
                    external_container_id,
                    updated_at DESC,
                    id
                """,
                parameters,
            ).fetchall()
            items: dict[tuple[str, str, str], dict[str, object]] = {}
            for row in rows:
                key = (
                    str(row["item_type"]),
                    str(row["external_id"]),
                    str(row["external_container_id"]),
                )
                items.setdefault(key, dict(row))

            item_ids = [str(item["id"]) for item in items.values()]
            refs_by_item: dict[str, list[str]] = {item_id: [] for item_id in item_ids}
            completion_by_item: dict[str, str] = {}
            if item_ids:
                item_id_placeholders = ", ".join("?" for _ in item_ids)
                source_rows = connection.execute(
                    f"""
                    SELECT target_item_id, source_ref
                    FROM source_link
                    WHERE target_item_id IN ({item_id_placeholders})
                    ORDER BY target_item_id, created_at, id
                    """,
                    item_ids,
                ).fetchall()
                for source_row in source_rows:
                    refs_by_item[str(source_row["target_item_id"])].append(
                        str(source_row["source_ref"])
                    )
                completion_rows = connection.execute(
                    f"""
                    SELECT item_id, completion_status
                    FROM calendar_event_state
                    WHERE item_id IN ({item_id_placeholders})
                    """,
                    item_ids,
                ).fetchall()
                completion_by_item = {
                    str(row["item_id"]): str(row["completion_status"]) for row in completion_rows
                }

        return {
            key: ExternalItemContext(
                item=item,
                source_refs=tuple(refs_by_item[str(item["id"])]),
                completion_status=completion_by_item.get(str(item["id"]), "unknown"),
            )
            for key, item in items.items()
        }

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
                WHERE key = ? AND operation = ?
                """,
                (key, operation),
            ).fetchone()
        if row is None:
            return IdempotencyDecision("new", None)
        if row["request_hash"] == request_hash:
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
        row_identity = operation + "\0" + key
        row_id = f"idempotency:{uuid.uuid5(uuid.NAMESPACE_URL, row_identity).hex}"
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT request_hash
                FROM idempotency_key
                WHERE key = ? AND operation = ?
                """,
                (key, operation),
            ).fetchone()
            if existing is not None and existing["request_hash"] != request_hash:
                raise ValueError("idempotency key conflicts with an existing request")
            connection.execute(
                """
                INSERT INTO idempotency_key (
                    id, key, operation, request_hash, hash_version,
                    result_item_id, status
                )
                VALUES (?, ?, ?, ?, 2, ?, 'succeeded')
                ON CONFLICT(key, operation) DO UPDATE SET
                    result_item_id = excluded.result_item_id,
                    status = 'succeeded',
                    error_code = NULL,
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
