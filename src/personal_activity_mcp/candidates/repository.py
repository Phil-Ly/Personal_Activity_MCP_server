"""SQLite-backed ActionCandidate lifecycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

from personal_activity_mcp.candidates.models import (
    ActionCandidate,
    CandidateCreate,
    CandidateIssue,
    CandidateListResult,
    CandidateQuery,
    CandidateRoute,
    CandidateUpdate,
    ResultRef,
    normalize_payload,
    validate_full_candidate_size,
)
from personal_activity_mcp.common import ToolContractError, paginate
from personal_activity_mcp.sidecar import OperationResult, SidecarRepository, WriteControl
from personal_activity_mcp.time_policy import Clock, SystemClock, require_aware_datetime

LOCAL_PROVIDER = "personal_activity_mcp"
_LOCAL_OPERATION_BY_ACTION = {
    "create_event": "calendar.create_event",
    "update_event": "calendar.update_event",
    "create_task": "reminders.create_reminder",
    "complete_task": "reminders.complete_reminder",
}


class CandidateRepository:
    """Persist and validate provider-neutral ActionCandidates."""

    def __init__(
        self,
        sidecar: SidecarRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._sidecar = sidecar
        self._write_control = WriteControl(sidecar)
        self._clock = clock or SystemClock()

    def create(self, command: CandidateCreate) -> ActionCandidate:
        now = self._now()
        candidate = ActionCandidate(
            candidate_id=f"candidate:{uuid.uuid4().hex}",
            version=1,
            action_type=command.action_type,
            payload=command.payload,
            extensions=command.extensions,
            target_ref=command.target_ref,
            source_refs=command.source_refs,
            decision_status="pending",
            execution_status="not_started",
            issues=command.issues,
            route=command.route,
            result_ref=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        validate_full_candidate_size(candidate)
        with self._sidecar.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _insert_candidate(connection, candidate)
            _replace_source_refs(connection, candidate.candidate_id, candidate.source_refs)
            _insert_candidate_audit(
                connection,
                operation="candidates.create",
                candidate=candidate,
                request_hash=_request_hash(command.model_dump(mode="json")),
                result_status="created",
            )
        return candidate

    def get(
        self,
        candidate_id: str,
        *,
        include_deleted: bool = False,
    ) -> ActionCandidate:
        with self._sidecar.connect() as connection:
            row = _select_candidate_row(connection, candidate_id)
            if row is None or (row["deleted_at"] is not None and not include_deleted):
                raise _candidate_error(
                    "CANDIDATE_NOT_FOUND",
                    "ActionCandidate was not found",
                )
            return _candidate_from_row(connection, row)

    def list_candidates(self, query: CandidateQuery) -> CandidateListResult:
        predicates: list[str] = []
        parameters: list[object] = []
        if not query.include_deleted:
            predicates.append("deleted_at IS NULL")
        if query.decision_status is not None:
            predicates.append("decision_status = ?")
            parameters.append(query.decision_status)
        if query.execution_status is not None:
            predicates.append("execution_status = ?")
            parameters.append(query.execution_status)
        if query.action_type is not None:
            predicates.append("action_type = ?")
            parameters.append(query.action_type)
        if query.created_from is not None:
            predicates.append("created_at >= ?")
            parameters.append(_iso_utc(query.created_from))
        if query.created_to is not None:
            predicates.append("created_at < ?")
            parameters.append(_iso_utc(query.created_to))
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""

        with self._sidecar.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM action_candidate
                {where_clause}
                ORDER BY created_at, candidate_id
                """,
                parameters,
            ).fetchall()
            candidates = [_candidate_from_row(connection, row) for row in rows]
        page, next_cursor = paginate(
            candidates,
            key=_candidate_page_key,
            limit=query.limit,
            cursor=query.cursor,
        )
        return CandidateListResult(candidates=page, next_cursor=next_cursor)

    def update(
        self,
        candidate_id: str,
        command: CandidateUpdate,
    ) -> ActionCandidate:
        with self._sidecar.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _select_candidate_row(connection, candidate_id)
            if row is None:
                raise _candidate_error(
                    "CANDIDATE_NOT_FOUND",
                    "ActionCandidate was not found",
                )
            current = _candidate_from_row(connection, row)
            _require_current_version(current, command.expected_version)
            if current.deleted_at is not None:
                raise _candidate_error(
                    "CANDIDATE_NOT_FOUND",
                    "ActionCandidate was not found",
                )
            if command.reconcile_execution:
                extra_fields = command.model_fields_set - {
                    "expected_version",
                    "reconcile_execution",
                }
                if extra_fields:
                    raise ValueError("reconcile_execution cannot be combined with other updates")
                updated, audit_status, audit_error = self._reconcile(current)
                audit_operation = "candidates.reconcile"
            else:
                updated = self._apply_update(current, command)
                audit_operation = "candidates.update"
                audit_status = "updated"
                audit_error = None
            update_result = connection.execute(
                """
                UPDATE action_candidate
                SET version = ?,
                    action_type = ?,
                    payload_json = ?,
                    target_ref_json = ?,
                    decision_status = ?,
                    execution_status = ?,
                    issues_json = ?,
                    route_json = ?,
                    result_ref_json = ?,
                    updated_at = ?
                WHERE candidate_id = ? AND version = ? AND deleted_at IS NULL
                """,
                _candidate_update_parameters(updated, command.expected_version),
            )
            if update_result.rowcount != 1:
                raise _candidate_error(
                    "VERSION_CONFLICT",
                    "ActionCandidate version has changed",
                )
            _replace_source_refs(connection, updated.candidate_id, updated.source_refs)
            _insert_candidate_audit(
                connection,
                operation=audit_operation,
                candidate=updated,
                request_hash=_request_hash(command.model_dump(mode="json", exclude_unset=True)),
                result_status=audit_status,
                error_code=audit_error,
            )
            return updated

    def delete(
        self,
        candidate_id: str,
        *,
        expected_version: int,
    ) -> ActionCandidate:
        with self._sidecar.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _select_candidate_row(connection, candidate_id)
            if row is None:
                raise _candidate_error(
                    "CANDIDATE_NOT_FOUND",
                    "ActionCandidate was not found",
                )
            current = _candidate_from_row(connection, row)
            _require_current_version(current, expected_version)
            if current.deleted_at is not None:
                raise _candidate_error(
                    "CANDIDATE_NOT_FOUND",
                    "ActionCandidate was not found",
                )
            if current.execution_status == "in_progress":
                raise _candidate_error(
                    "INVALID_STATE_TRANSITION",
                    "An in-progress Candidate must reach or reconcile a terminal "
                    "state before deletion",
                )
            deleted_at = self._now()
            deleted = current.model_copy(
                update={
                    "version": current.version + 1,
                    "updated_at": deleted_at,
                    "deleted_at": deleted_at,
                }
            )
            update_result = connection.execute(
                """
                UPDATE action_candidate
                SET version = ?, updated_at = ?, deleted_at = ?
                WHERE candidate_id = ? AND version = ? AND deleted_at IS NULL
                """,
                (
                    deleted.version,
                    deleted.updated_at.isoformat(),
                    deleted.deleted_at.isoformat() if deleted.deleted_at else None,
                    candidate_id,
                    expected_version,
                ),
            )
            if update_result.rowcount != 1:
                raise _candidate_error(
                    "VERSION_CONFLICT",
                    "ActionCandidate version has changed",
                )
            _insert_candidate_audit(
                connection,
                operation="candidates.delete",
                candidate=deleted,
                request_hash=_request_hash(
                    {
                        "candidate_id": candidate_id,
                        "expected_version": expected_version,
                    }
                ),
                result_status="deleted",
            )
            return deleted

    def _apply_update(
        self,
        current: ActionCandidate,
        command: CandidateUpdate,
    ) -> ActionCandidate:
        fields = command.model_fields_set
        mutable_fields = fields - {"expected_version"}
        if current.execution_status == "in_progress" and (
            not mutable_fields.issubset({"execution_status", "result_ref"})
            or command.execution_status not in {"succeeded", "failed"}
        ):
            raise _candidate_error(
                "INVALID_STATE_TRANSITION",
                "An in-progress Candidate only accepts its terminal execution result",
            )
        action_type = command.action_type if "action_type" in fields else current.action_type
        payload_input = command.payload if "payload" in fields else current.payload
        if payload_input is None:
            raise ValueError("payload cannot be null")
        payload = normalize_payload(action_type, payload_input)
        extensions = command.extensions if "extensions" in fields else current.extensions
        if extensions is None:
            extensions = {}
        target_ref = command.target_ref if "target_ref" in fields else current.target_ref
        source_refs = command.source_refs if "source_refs" in fields else current.source_refs
        if source_refs is None:
            source_refs = []
        issues = command.issues if "issues" in fields else current.issues
        if issues is None:
            issues = []
        route = command.route if "route" in fields else current.route
        decision_status = current.decision_status
        execution_status = current.execution_status
        result_ref = current.result_ref

        semantic_changed = (
            action_type != current.action_type
            or payload != current.payload
            or target_ref != current.target_ref
        )
        if semantic_changed:
            if fields.intersection({"decision_status", "execution_status", "result_ref"}):
                raise _candidate_error(
                    "INVALID_STATE_TRANSITION",
                    "Semantic changes and lifecycle transitions must be separate updates",
                )
            decision_status = "pending"
            execution_status = "not_started"
            result_ref = None
            route = _clear_execution_route(route)

        if issues and decision_status == "confirmed":
            decision_status = "pending"
            execution_status = "not_started"
            result_ref = None
            route = _clear_execution_route(route)

        if "decision_status" in fields:
            if command.decision_status is None:
                raise ValueError("decision_status cannot be null")
            decision_status = _transition_decision(
                current=decision_status,
                requested=command.decision_status,
                issues=issues,
            )

        if "result_ref" in fields and "execution_status" not in fields:
            raise _candidate_error(
                "INVALID_STATE_TRANSITION",
                "result_ref requires an execution terminal transition",
            )

        if "execution_status" in fields:
            if command.execution_status is None:
                raise ValueError("execution_status cannot be null")
            execution_status, result_ref = _transition_execution(
                current=execution_status,
                requested=command.execution_status,
                action_type=action_type,
                decision_status=decision_status,
                issues=issues,
                route=route,
                target_ref=target_ref,
                requested_result=command.result_ref if "result_ref" in fields else None,
            )
            if (
                command.execution_status in {"succeeded", "failed"}
                and route is not None
                and route.provider == LOCAL_PROVIDER
            ):
                if command.result_ref is None:
                    raise _candidate_error(
                        "EXECUTION_RESULT_MISMATCH",
                        "Local execution result is missing",
                    )
                result_ref = self._verify_local_terminal_result(
                    route=route,
                    requested_status=command.execution_status,
                    submitted_result=command.result_ref,
                )

        updated = ActionCandidate(
            candidate_id=current.candidate_id,
            version=current.version + 1,
            action_type=action_type,
            payload=payload,
            extensions=extensions,
            target_ref=target_ref,
            source_refs=source_refs,
            decision_status=decision_status,
            execution_status=execution_status,
            issues=issues,
            route=route,
            result_ref=result_ref,
            created_at=current.created_at,
            updated_at=self._now(),
            deleted_at=None,
        )
        validate_full_candidate_size(updated)
        if _candidate_content(updated) == _candidate_content(current):
            raise ValueError("Candidate update does not change any field")
        return updated

    def _reconcile(
        self,
        current: ActionCandidate,
    ) -> tuple[ActionCandidate, str, str | None]:
        if current.execution_status != "in_progress":
            raise _candidate_error(
                "INVALID_STATE_TRANSITION",
                "Only an in-progress Candidate can be reconciled",
            )
        route = current.route
        if (
            route is None
            or route.provider != LOCAL_PROVIDER
            or route.operation is None
            or route.idempotency_key is None
        ):
            raise _candidate_error(
                "RECONCILIATION_UNAVAILABLE",
                "Candidate does not have a local execution to reconcile",
            )
        local_result = self._write_control.get_operation_result(
            idempotency_key=route.idempotency_key,
            operation=route.operation,
        )
        if local_result is None or local_result.status in {
            "pending",
            "external_state_unknown",
        }:
            issues = list(current.issues)
            if not any(issue.code == "EXTERNAL_STATE_UNKNOWN" for issue in issues):
                issues.append(
                    CandidateIssue(
                        code="EXTERNAL_STATE_UNKNOWN",
                        message="External write state cannot be determined from local records",
                    )
                )
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "issues": issues,
                    "updated_at": self._now(),
                }
            )
            validate_full_candidate_size(updated)
            return updated, "external_state_unknown", "EXTERNAL_STATE_UNKNOWN"

        verified = _verified_result_ref(
            local_result,
            provider=LOCAL_PROVIDER,
        )
        updated = current.model_copy(
            update={
                "version": current.version + 1,
                "execution_status": local_result.status,
                "result_ref": verified,
                "issues": [
                    issue for issue in current.issues if issue.code != "EXTERNAL_STATE_UNKNOWN"
                ],
                "updated_at": self._now(),
            }
        )
        validate_full_candidate_size(updated)
        return updated, f"reconciled_{local_result.status}", None

    def _verify_local_terminal_result(
        self,
        *,
        route: CandidateRoute,
        requested_status: str,
        submitted_result: ResultRef,
    ) -> ResultRef:
        if route.operation is None or route.idempotency_key is None:
            raise _candidate_error(
                "EXECUTION_RESULT_MISMATCH",
                "Local execution metadata is incomplete",
            )
        local_result = self._write_control.get_operation_result(
            idempotency_key=route.idempotency_key,
            operation=route.operation,
        )
        if local_result is None or local_result.status != requested_status:
            raise _candidate_error(
                "EXECUTION_RESULT_MISMATCH",
                "Local execution state does not match the submitted result",
            )
        verified = _verified_result_ref(
            local_result,
            provider=LOCAL_PROVIDER,
        )
        if (
            submitted_result.status != verified.status
            or submitted_result.provider != verified.provider
            or submitted_result.item_id != verified.item_id
            or submitted_result.error_code != verified.error_code
        ):
            raise _candidate_error(
                "EXECUTION_RESULT_MISMATCH",
                "Submitted result does not match local execution records",
            )
        return verified

    def _now(self) -> datetime:
        value = self._clock.now()
        require_aware_datetime(value, "clock.now()")
        return value.astimezone(UTC)


def _transition_decision(
    *,
    current: str,
    requested: str,
    issues: list[CandidateIssue],
) -> Literal["pending", "confirmed", "rejected"]:
    if current != "pending" or requested not in {"confirmed", "rejected"}:
        raise _candidate_error(
            "INVALID_STATE_TRANSITION",
            "Candidate decision transition is invalid",
        )
    if requested == "confirmed" and issues:
        raise _candidate_error(
            "CANDIDATE_HAS_BLOCKING_ISSUES",
            "Candidate has blocking issues",
        )
    return requested  # type: ignore[return-value]


def _transition_execution(
    *,
    current: str,
    requested: str,
    action_type: str,
    decision_status: str,
    issues: list[CandidateIssue],
    route: CandidateRoute | None,
    target_ref: object,
    requested_result: ResultRef | None,
) -> tuple[Literal["not_started", "in_progress", "succeeded", "failed"], ResultRef | None]:
    if requested == "in_progress":
        if current not in {"not_started", "failed"}:
            raise _candidate_error(
                "INVALID_STATE_TRANSITION",
                "Candidate execution transition is invalid",
            )
        if decision_status != "confirmed" or action_type == "none" or issues:
            raise _candidate_error(
                "INVALID_STATE_TRANSITION",
                "Candidate is not eligible for execution",
            )
        if route is None or not route.is_executable():
            raise _candidate_error(
                "ROUTE_NOT_EXECUTABLE",
                "Candidate route is not executable",
            )
        if action_type in {"update_event", "complete_task"} and (
            target_ref is None or getattr(target_ref, "container_id", None) is None
        ):
            raise _candidate_error(
                "TARGET_REF_REQUIRED",
                "Candidate requires a complete target_ref",
            )
        if route.provider == LOCAL_PROVIDER and not route.idempotency_key:
            raise _candidate_error(
                "ROUTE_NOT_EXECUTABLE",
                "Local Candidate execution requires an idempotency key",
            )
        if route.provider == LOCAL_PROVIDER:
            expected_operation = _LOCAL_OPERATION_BY_ACTION.get(action_type)
            if (
                expected_operation is None
                or route.tool_name != expected_operation
                or route.operation != expected_operation
            ):
                raise _candidate_error(
                    "ROUTE_OPERATION_MISMATCH",
                    "Local route does not match the Candidate action type",
                )
        return "in_progress", None

    if requested not in {"succeeded", "failed"} or current != "in_progress":
        raise _candidate_error(
            "INVALID_STATE_TRANSITION",
            "Candidate execution transition is invalid",
        )
    if requested_result is None or requested_result.status != requested:
        raise _candidate_error(
            "EXECUTION_RESULT_MISMATCH",
            "Candidate result does not match the requested execution status",
        )
    if route is None or route.provider is None:
        raise _candidate_error(
            "EXECUTION_RESULT_MISMATCH",
            "Candidate execution route is missing",
        )
    if requested_result.provider != route.provider:
        raise _candidate_error(
            "EXECUTION_RESULT_MISMATCH",
            "Candidate result provider does not match the execution route",
        )
    return requested, requested_result.model_copy(update={"verification_source": "agent_reported"})


def _clear_execution_route(route: CandidateRoute | None) -> CandidateRoute | None:
    if route is None:
        return None
    return route.model_copy(
        update={
            "operation": None,
            "idempotency_key": None,
        }
    )


def _verified_result_ref(
    result: OperationResult,
    *,
    provider: str,
) -> ResultRef:
    if result.status == "succeeded":
        if (
            result.result_item_id is None
            or result.audit_id is None
            or result.audit_result_status != "succeeded"
            or result.audit_target_item_id != result.result_item_id
            or result.error_code is not None
            or result.audit_error_code is not None
        ):
            raise _candidate_error(
                "EXECUTION_RESULT_MISMATCH",
                "Local success record and audit are inconsistent",
            )
        return ResultRef(
            provider=provider,
            status="succeeded",
            item_id=result.result_item_id,
            audit_id=result.audit_id,
            verification_source="sidecar_verified",
        )
    if result.status == "failed":
        if (
            result.error_code is None
            or result.audit_id is None
            or result.audit_result_status != "failed"
            or result.audit_target_item_id is not None
            or result.audit_error_code != result.error_code
            or result.result_item_id is not None
        ):
            raise _candidate_error(
                "EXECUTION_RESULT_MISMATCH",
                "Local failure record and audit are inconsistent",
            )
        return ResultRef(
            provider=provider,
            status="failed",
            error_code=result.error_code,
            audit_id=result.audit_id,
            verification_source="sidecar_verified",
        )
    raise _candidate_error(
        "EXECUTION_RESULT_MISMATCH",
        "Local execution has no verified terminal result",
    )


def _insert_candidate(
    connection: sqlite3.Connection,
    candidate: ActionCandidate,
) -> None:
    connection.execute(
        """
        INSERT INTO action_candidate (
            candidate_id,
            version,
            action_type,
            payload_json,
            target_ref_json,
            decision_status,
            execution_status,
            issues_json,
            route_json,
            result_ref_json,
            created_at,
            updated_at,
            deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.candidate_id,
            candidate.version,
            candidate.action_type,
            _payload_json(candidate),
            _model_json(candidate.target_ref),
            candidate.decision_status,
            candidate.execution_status,
            _json_dumps([issue.model_dump(mode="json") for issue in candidate.issues]),
            _model_json(candidate.route),
            _model_json(candidate.result_ref),
            candidate.created_at.isoformat(),
            candidate.updated_at.isoformat(),
            candidate.deleted_at.isoformat() if candidate.deleted_at else None,
        ),
    )


def _candidate_update_parameters(
    candidate: ActionCandidate,
    expected_version: int,
) -> tuple[object, ...]:
    return (
        candidate.version,
        candidate.action_type,
        _payload_json(candidate),
        _model_json(candidate.target_ref),
        candidate.decision_status,
        candidate.execution_status,
        _json_dumps([issue.model_dump(mode="json") for issue in candidate.issues]),
        _model_json(candidate.route),
        _model_json(candidate.result_ref),
        candidate.updated_at.isoformat(),
        candidate.candidate_id,
        expected_version,
    )


def _select_candidate_row(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM action_candidate WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()


def _candidate_from_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> ActionCandidate:
    payload_envelope = json.loads(str(row["payload_json"]))
    source_refs = [
        str(link["source_ref"])
        for link in connection.execute(
            """
            SELECT source_ref
            FROM source_link
            WHERE target_candidate_id = ?
            ORDER BY source_ref
            """,
            (row["candidate_id"],),
        ).fetchall()
    ]
    return ActionCandidate(
        candidate_id=str(row["candidate_id"]),
        version=int(row["version"]),
        action_type=str(row["action_type"]),
        payload=payload_envelope["payload"],
        extensions=payload_envelope.get("extensions", {}),
        target_ref=_json_value(row["target_ref_json"]),
        source_refs=source_refs,
        decision_status=str(row["decision_status"]),
        execution_status=str(row["execution_status"]),
        issues=_json_value(row["issues_json"]) or [],
        route=_json_value(row["route_json"]),
        result_ref=_json_value(row["result_ref_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        deleted_at=str(row["deleted_at"]) if row["deleted_at"] is not None else None,
    )


def _replace_source_refs(
    connection: sqlite3.Connection,
    candidate_id: str,
    source_refs: list[str],
) -> None:
    connection.execute(
        "DELETE FROM source_link WHERE target_candidate_id = ?",
        (candidate_id,),
    )
    for source_ref in source_refs:
        connection.execute(
            """
            INSERT INTO source_link (
                id,
                target_item_id,
                target_candidate_id,
                source_ref,
                relation_type
            )
            VALUES (?, NULL, ?, ?, 'supported_by')
            """,
            (
                _source_link_id(candidate_id, source_ref),
                candidate_id,
                source_ref,
            ),
        )


def _insert_candidate_audit(
    connection: sqlite3.Connection,
    *,
    operation: str,
    candidate: ActionCandidate,
    request_hash: str,
    result_status: str,
    error_code: str | None = None,
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
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            f"audit:{uuid.uuid4().hex}",
            operation,
            candidate.candidate_id,
            request_hash,
            result_status,
            error_code,
            1 if candidate.decision_status == "confirmed" else 0,
        ),
    )


def _payload_json(candidate: ActionCandidate) -> str:
    return _json_dumps(
        {
            "payload": candidate.payload,
            "extensions": candidate.extensions,
        }
    )


def _model_json(model: object) -> str | None:
    if model is None:
        return None
    return _json_dumps(model.model_dump(mode="json"))  # type: ignore[attr-defined]


def _json_value(value: object) -> object:
    return json.loads(str(value)) if value is not None else None


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _request_hash(value: object) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _source_link_id(candidate_id: str, source_ref: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}\0{source_ref}".encode()).hexdigest()
    return f"source:{digest[:32]}"


def _candidate_page_key(candidate: ActionCandidate) -> tuple[str, ...]:
    return (
        candidate.created_at.astimezone(UTC).isoformat(),
        candidate.candidate_id,
    )


def _candidate_content(candidate: ActionCandidate) -> dict[str, object]:
    return candidate.model_dump(
        mode="json",
        exclude={"version", "updated_at"},
    )


def _require_current_version(candidate: ActionCandidate, expected_version: int) -> None:
    if candidate.version != expected_version:
        raise _candidate_error(
            "VERSION_CONFLICT",
            "ActionCandidate version has changed",
        )


def _candidate_error(code: str, message: str) -> ToolContractError:
    return ToolContractError(
        code=code,
        message=message,
        retryable=False,
        public_message=message,
    )


def _iso_utc(value: datetime) -> str:
    require_aware_datetime(value, "Candidate query datetime")
    return value.astimezone(UTC).isoformat()
