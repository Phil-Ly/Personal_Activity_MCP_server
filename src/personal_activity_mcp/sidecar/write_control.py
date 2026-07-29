"""Atomic reservation and finalization for external write operations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    item_type: Literal["calendar_event", "reminder", "calendar", "reminder_list"]
    external_id: str
    external_container_id: str
    status_semantics: Literal["planned", "probable", "confirmed"] | None
    created_by_mcp: bool
    completion_status: Literal["unknown", "incomplete", "completed"] | None = None
    expected_completion_status: Literal["unknown", "incomplete", "completed"] | None = None

    @model_validator(mode="after")
    def validate_completion_status(self) -> McpItemWrite:
        if self.item_type in {"reminder", "calendar", "reminder_list"} and (
            self.completion_status is not None or self.expected_completion_status is not None
        ):
            raise ValueError("completion status fields are only valid for calendar_event")
        if self.item_type in {"calendar", "reminder_list"} and self.status_semantics is not None:
            raise ValueError(f"status_semantics is not valid for {self.item_type}")
        if self.item_type not in {"calendar", "reminder_list"} and self.status_semantics is None:
            raise ValueError("status_semantics is required for Calendar Events and Reminders")
        return self


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


class SidecarStateConflict(sqlite3.IntegrityError):
    """Raised when a local compare-and-set precondition no longer holds."""


class WriteControl:
    def __init__(self, repository: SidecarRepository) -> None:
        self._repository = repository

    def reserve_operation(
        self,
        *,
        idempotency_key: str,
        operation: str,
        request_hash: str,
        confirmed_by_user: bool = False,
    ) -> ReservationDecision:
        _require_non_empty(idempotency_key, "idempotency_key")
        _require_non_empty(operation, "operation")
        _require_non_empty(request_hash, "request_hash")

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
                        key, operation, request_hash, status, confirmed_by_user
                    )
                    VALUES (?, ?, ?, 'pending', ?)
                    """,
                    (
                        idempotency_key,
                        operation,
                        request_hash,
                        1 if confirmed_by_user else 0,
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
                        result_item_id = NULL,
                        error_code = NULL,
                        audit_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ?
                    """,
                    (idempotency_key, operation),
                )
                return ReservationDecision(status="execute")
            raise sqlite3.DatabaseError(f"Unsupported idempotency status: {status}")

    def audit_non_executable_reservation(
        self,
        decision: ReservationDecision,
        *,
        operation: str,
        request_hash: str,
        confirmed_by_user: bool,
    ) -> None:
        """Record a conflicting or concurrent request without changing its owner."""
        error_code = {
            "conflict": "IDEMPOTENCY_CONFLICT",
            "in_progress": "OPERATION_IN_PROGRESS",
        }.get(decision.status)
        if error_code is None:
            return
        self.record_blocked(
            operation=operation,
            target_item_id=decision.result_item_id,
            request_hash=request_hash,
            error_code=error_code,
            confirmed_by_user=confirmed_by_user,
        )

    def record_blocked(
        self,
        *,
        operation: str,
        target_item_id: str | None,
        request_hash: str,
        error_code: str,
        confirmed_by_user: bool,
    ) -> str:
        """Append one audit for a request rejected before reservation or execution."""
        audit = AuditWrite(
            request_hash=request_hash,
            result_status="blocked",
            error_code=error_code,
            confirmed_by_user=confirmed_by_user,
        )
        with self._repository.connect() as connection:
            _insert_audit(
                connection,
                audit=audit,
                operation=operation,
                target_item_id=target_item_id,
            )
        return audit.audit_id

    def finalize_success(
        self,
        *,
        idempotency_key: str,
        operation: str,
        item: McpItemWrite,
        source_refs: list[str],
        audit: AuditWrite,
        external_write_attempted: bool = True,
    ) -> None:
        try:
            with self._repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                _require_pending_reservation(
                    connection,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_hash=audit.request_hash,
                )
                existing = connection.execute(
                    """
                    SELECT
                        item_type,
                        external_id,
                        external_container_id,
                        source_refs_json,
                        completion_status
                    FROM mcp_item
                    WHERE id = ?
                    """,
                    (item.item_id,),
                ).fetchone()
                if existing is not None and (
                    str(existing["item_type"]) != item.item_type
                    or str(existing["external_id"]) != item.external_id
                    or str(existing["external_container_id"]) != item.external_container_id
                ):
                    raise sqlite3.IntegrityError("mcp_item item identity cannot be reassigned")
                current_completion_status = (
                    str(existing["completion_status"])
                    if existing is not None and existing["completion_status"] is not None
                    else "unknown"
                )
                if (
                    item.expected_completion_status is not None
                    and current_completion_status != item.expected_completion_status
                ):
                    raise SidecarStateConflict(
                        "calendar completion status changed before finalization"
                    )
                refs = _merge_source_refs(existing, source_refs)
                completion_status = _resolved_completion_status(item, existing)
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status_semantics = excluded.status_semantics,
                        completion_status = excluded.completion_status,
                        source_refs_json = excluded.source_refs_json,
                        created_by_mcp = excluded.created_by_mcp
                    """,
                    (
                        item.item_id,
                        item.item_type,
                        item.external_id,
                        item.external_container_id,
                        item.status_semantics,
                        completion_status,
                        json.dumps(refs, separators=(",", ":")),
                        1 if item.created_by_mcp else 0,
                    ),
                )
                _insert_audit(
                    connection,
                    audit=audit,
                    operation=operation,
                    target_item_id=item.item_id,
                )
                update = connection.execute(
                    """
                    UPDATE idempotency_key
                    SET status = 'succeeded',
                        result_item_id = ?,
                        error_code = NULL,
                        audit_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ?
                    """,
                    (item.item_id, audit.audit_id, idempotency_key, operation),
                )
                if update.rowcount != 1:
                    raise sqlite3.DatabaseError("idempotency finalization did not update one row")
        except SidecarStateConflict:
            raise
        except Exception:
            status: Literal["failed", "external_state_unknown"] = (
                "external_state_unknown" if external_write_attempted else "failed"
            )
            error_code = (
                "EXTERNAL_STATE_UNKNOWN"
                if external_write_attempted
                else "LOCAL_PERSISTENCE_FAILURE"
            )
            self._mark_finalization_failure(
                idempotency_key=idempotency_key,
                operation=operation,
                audit=audit,
                status=status,
                error_code=error_code,
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
        terminal_audit = audit.model_copy(
            update={
                "result_status": status,
                "error_code": error_code,
            }
        )
        with self._repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_pending_reservation(
                connection,
                idempotency_key=idempotency_key,
                operation=operation,
                request_hash=audit.request_hash,
            )
            _insert_audit(
                connection,
                audit=terminal_audit,
                operation=operation,
                target_item_id=None,
            )
            connection.execute(
                """
                UPDATE idempotency_key
                SET status = ?,
                    result_item_id = NULL,
                    error_code = ?,
                    audit_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = ? AND operation = ?
                """,
                (
                    status,
                    error_code,
                    terminal_audit.audit_id,
                    idempotency_key,
                    operation,
                ),
            )

    def get_operation_result(
        self,
        *,
        idempotency_key: str,
        operation: str,
    ) -> OperationResult | None:
        """Read one local write result and its directly linked terminal audit."""
        _require_non_empty(idempotency_key, "idempotency_key")
        _require_non_empty(operation, "operation")
        with self._repository.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    i.request_hash,
                    i.status,
                    i.result_item_id,
                    i.error_code,
                    i.audit_id,
                    a.result_status AS audit_result_status,
                    a.target_item_id AS audit_target_item_id,
                    a.error_code AS audit_error_code
                FROM idempotency_key AS i
                LEFT JOIN operation_audit AS a ON a.id = i.audit_id
                WHERE i.key = ? AND i.operation = ?
                """,
                (idempotency_key, operation),
            ).fetchone()
        if row is None:
            return None
        return OperationResult(
            status=str(row["status"]),
            request_hash=str(row["request_hash"]),
            result_item_id=_optional_string(row["result_item_id"]),
            error_code=_optional_string(row["error_code"]),
            audit_id=_optional_string(row["audit_id"]),
            audit_result_status=_optional_string(row["audit_result_status"]),
            audit_target_item_id=_optional_string(row["audit_target_item_id"]),
            audit_error_code=_optional_string(row["audit_error_code"]),
        )

    def _mark_finalization_failure(
        self,
        *,
        idempotency_key: str,
        operation: str,
        audit: AuditWrite,
        status: Literal["failed", "external_state_unknown"],
        error_code: str,
    ) -> None:
        terminal_audit = audit.model_copy(
            update={
                "audit_id": f"audit:{uuid.uuid4().hex}",
                "result_status": status,
                "error_code": error_code,
            }
        )
        try:
            with self._repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT status
                    FROM idempotency_key
                    WHERE key = ? AND operation = ?
                    """,
                    (idempotency_key, operation),
                ).fetchone()
                if row is None or str(row["status"]) != "pending":
                    return
                _insert_audit(
                    connection,
                    audit=terminal_audit,
                    operation=operation,
                    target_item_id=None,
                )
                connection.execute(
                    """
                    UPDATE idempotency_key
                    SET status = ?,
                        result_item_id = NULL,
                        error_code = ?,
                        audit_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND operation = ?
                    """,
                    (
                        status,
                        error_code,
                        terminal_audit.audit_id,
                        idempotency_key,
                        operation,
                    ),
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
            request_hash,
            result_status,
            error_code,
            confirmed_by_user
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
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


def _merge_source_refs(
    existing: sqlite3.Row | None,
    source_refs: list[str],
) -> list[str]:
    previous: list[str] = []
    if existing is not None:
        raw_previous = json.loads(str(existing["source_refs_json"]))
        if not isinstance(raw_previous, list) or not all(
            isinstance(value, str) for value in raw_previous
        ):
            raise sqlite3.DatabaseError("mcp_item.source_refs_json must be a string array")
        previous = raw_previous
    normalized_new = normalize_source_refs(source_refs)
    return sorted(set(previous).union(normalized_new))


def _resolved_completion_status(
    item: McpItemWrite,
    existing: sqlite3.Row | None,
) -> str | None:
    if item.item_type != "calendar_event":
        return None
    if item.completion_status is not None:
        return item.completion_status
    if existing is not None and existing["completion_status"] is not None:
        return str(existing["completion_status"])
    return "unknown"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
