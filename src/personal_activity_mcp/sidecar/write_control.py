"""Atomic reservation and finalization for external write operations."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from personal_activity_mcp.common import normalize_source_refs
from personal_activity_mcp.sidecar.repository import SidecarRepository


class ReservationDecision(BaseModel):
    status: Literal[
        "execute",
        "deduplicated",
        "conflict",
        "in_progress",
        "external_state_unknown",
    ]
    result_item_id: str | None = None


class McpItemWrite(BaseModel):
    item_id: str
    item_type: Literal["calendar_event", "reminder", "action_record"]
    external_id: str
    external_container_id: str
    title_hash: str | None
    time_start: str | None
    time_end: str | None
    status_semantics: str | None
    state_token: str | None = None
    created_by_mcp: bool
    source_relation_type: Literal["created_from", "supported_by", "updated_from"]


class AuditWrite(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"audit:{uuid.uuid4().hex}")
    request_hash: str
    result_status: str
    error_code: str | None
    confirmed_by_user: bool


class OperationResult(BaseModel):
    status: Literal[
        "pending",
        "succeeded",
        "failed",
        "external_state_unknown",
    ]
    request_hash: str
    result_item_id: str | None
    error_code: str | None
    audit_id: str | None
    audit_result_status: str | None
    audit_target_item_id: str | None
    audit_error_code: str | None


class WriteControl:
    def __init__(self, repository: SidecarRepository) -> None:
        self._repository = repository

    def reserve_operation(
        self,
        *,
        idempotency_key: str,
        operation: str,
        request_hash: str,
        hash_version: int = 2,
    ) -> ReservationDecision:
        _require_non_empty(idempotency_key, "idempotency_key")
        _require_non_empty(operation, "operation")
        _require_non_empty(request_hash, "request_hash")
        if hash_version < 1:
            raise ValueError("hash_version must be positive")

        with self._repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_hash, status, result_item_id
                FROM idempotency_key
                WHERE key = ? AND operation = ?
                """,
                (idempotency_key, operation),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO idempotency_key (
                        id,
                        key,
                        operation,
                        request_hash,
                        hash_version,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        _idempotency_row_id(idempotency_key, operation),
                        idempotency_key,
                        operation,
                        request_hash,
                        hash_version,
                    ),
                )
                return ReservationDecision(status="execute")

            if str(row["request_hash"]) != request_hash:
                return ReservationDecision(
                    status="conflict",
                    result_item_id=_optional_string(row["result_item_id"]),
                )

            status = str(row["status"])
            if status == "succeeded":
                return ReservationDecision(
                    status="deduplicated",
                    result_item_id=_optional_string(row["result_item_id"]),
                )
            if status == "pending":
                return ReservationDecision(status="in_progress")
            if status == "external_state_unknown":
                return ReservationDecision(status="external_state_unknown")
            if status == "failed":
                connection.execute(
                    """
                    UPDATE idempotency_key
                    SET status = 'pending',
                        hash_version = ?,
                        result_item_id = NULL,
                        error_code = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ?
                    """,
                    (hash_version, idempotency_key, operation),
                )
                return ReservationDecision(status="execute")
            raise sqlite3.DatabaseError(f"Unsupported idempotency status: {status}")

    def finalize_success(
        self,
        *,
        idempotency_key: str,
        operation: str,
        item: McpItemWrite,
        source_refs: list[str],
        audit: AuditWrite,
    ) -> None:
        normalized_refs = normalize_source_refs(source_refs)
        try:
            with self._repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                _require_pending_reservation(
                    connection,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_hash=audit.request_hash,
                )
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
                        item.item_id,
                        item.item_type,
                        item.external_id,
                        item.external_container_id,
                        item.title_hash,
                        item.time_start,
                        item.time_end,
                        item.status_semantics,
                        item.state_token,
                        1 if item.created_by_mcp else 0,
                    ),
                )
                for source_ref in normalized_refs:
                    connection.execute(
                        """
                        INSERT INTO source_link (
                            id,
                            target_item_id,
                            target_candidate_id,
                            source_ref,
                            relation_type
                        )
                        VALUES (?, ?, NULL, ?, ?)
                        ON CONFLICT(id) DO NOTHING
                        """,
                        (
                            _source_link_id(
                                item.item_id,
                                source_ref,
                                item.source_relation_type,
                            ),
                            item.item_id,
                            source_ref,
                            item.source_relation_type,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE idempotency_key
                    SET status = 'succeeded',
                        result_item_id = ?,
                        error_code = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ?
                    """,
                    (item.item_id, idempotency_key, operation),
                )
                _insert_audit(
                    connection,
                    audit=audit,
                    operation=operation,
                    target_item_id=item.item_id,
                )
        except Exception:
            self._mark_external_state_unknown(
                idempotency_key=idempotency_key,
                operation=operation,
                audit=audit,
            )
            raise

    def finalize_failure(
        self,
        *,
        idempotency_key: str,
        operation: str,
        status: Literal["failed", "external_state_unknown"],
        error_code: str,
        audit: AuditWrite,
    ) -> None:
        with self._repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_pending_reservation(
                connection,
                idempotency_key=idempotency_key,
                operation=operation,
                request_hash=audit.request_hash,
            )
            connection.execute(
                """
                UPDATE idempotency_key
                SET status = ?,
                    result_item_id = NULL,
                    error_code = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = ? AND operation = ?
                """,
                (status, error_code, idempotency_key, operation),
            )
            _insert_audit(
                connection,
                audit=audit.model_copy(
                    update={
                        "result_status": status,
                        "error_code": error_code,
                    }
                ),
                operation=operation,
                target_item_id=None,
            )

    def get_operation_result(
        self,
        *,
        idempotency_key: str,
        operation: str,
    ) -> OperationResult | None:
        """Read one local write result and its latest matching audit without side effects."""
        _require_non_empty(idempotency_key, "idempotency_key")
        _require_non_empty(operation, "operation")
        with self._repository.connect() as connection:
            row = connection.execute(
                """
                SELECT request_hash, status, result_item_id, error_code
                FROM idempotency_key
                WHERE key = ? AND operation = ?
                """,
                (idempotency_key, operation),
            ).fetchone()
            if row is None:
                return None
            audit = connection.execute(
                """
                SELECT id, result_status, target_item_id, error_code
                FROM operation_audit
                WHERE operation = ?
                  AND request_hash = ?
                  AND result_status = ?
                  AND target_item_id IS ?
                  AND error_code IS ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (
                    operation,
                    row["request_hash"],
                    row["status"],
                    row["result_item_id"],
                    row["error_code"],
                ),
            ).fetchone()
        return OperationResult(
            status=str(row["status"]),
            request_hash=str(row["request_hash"]),
            result_item_id=_optional_string(row["result_item_id"]),
            error_code=_optional_string(row["error_code"]),
            audit_id=str(audit["id"]) if audit is not None else None,
            audit_result_status=(str(audit["result_status"]) if audit is not None else None),
            audit_target_item_id=(
                _optional_string(audit["target_item_id"]) if audit is not None else None
            ),
            audit_error_code=(_optional_string(audit["error_code"]) if audit is not None else None),
        )

    def _mark_external_state_unknown(
        self,
        *,
        idempotency_key: str,
        operation: str,
        audit: AuditWrite,
    ) -> None:
        try:
            with self._repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                update = connection.execute(
                    """
                    UPDATE idempotency_key
                    SET status = 'external_state_unknown',
                        result_item_id = NULL,
                        error_code = 'EXTERNAL_STATE_UNKNOWN',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ? AND status = 'pending'
                    """,
                    (idempotency_key, operation),
                )
                if update.rowcount != 1:
                    return
                _insert_audit(
                    connection,
                    audit=audit.model_copy(
                        update={
                            "result_status": "external_state_unknown",
                            "error_code": "EXTERNAL_STATE_UNKNOWN",
                        }
                    ),
                    operation=operation,
                    target_item_id=None,
                )
        except Exception:
            return


def _require_pending_reservation(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str,
    operation: str,
    request_hash: str,
) -> None:
    row = connection.execute(
        """
        SELECT request_hash, status
        FROM idempotency_key
        WHERE key = ? AND operation = ?
        """,
        (idempotency_key, operation),
    ).fetchone()
    if row is None:
        raise ValueError("idempotency reservation is missing")
    if str(row["request_hash"]) != request_hash:
        raise ValueError("idempotency reservation hash conflicts")
    if str(row["status"]) != "pending":
        raise ValueError("idempotency reservation is not pending")


def _insert_audit(
    connection: sqlite3.Connection,
    *,
    audit: AuditWrite,
    operation: str,
    target_item_id: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO operation_audit (
            id,
            operation,
            target_item_id,
            target_candidate_id,
            request_hash,
            result_status,
            error_code,
            confirmed_by_user
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            audit.audit_id,
            operation,
            target_item_id,
            audit.request_hash,
            audit.result_status,
            audit.error_code,
            1 if audit.confirmed_by_user else 0,
        ),
    )


def _idempotency_row_id(idempotency_key: str, operation: str) -> str:
    digest = hashlib.sha256(f"{operation}\0{idempotency_key}".encode()).hexdigest()
    return f"idempotency:{digest[:32]}"


def _source_link_id(item_id: str, source_ref: str, relation_type: str) -> str:
    digest = hashlib.sha256(f"{item_id}\0{source_ref}\0{relation_type}".encode()).hexdigest()
    return f"source:{digest[:32]}"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
