from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_activity_mcp.candidates import (
    LOCAL_PROVIDER,
    CandidateCreate,
    CandidateRepository,
    CandidateRoute,
    CandidateUpdate,
    ResultRef,
)
from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.sidecar import (
    AuditWrite,
    McpItemWrite,
    SidecarRepository,
    WriteControl,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 28, 9, tzinfo=UTC)


def make_repositories(
    tmp_path: Path,
) -> tuple[SidecarRepository, CandidateRepository, WriteControl]:
    sidecar = SidecarRepository(tmp_path / "sidecar" / "state.sqlite3")
    sidecar.initialize()
    return (
        sidecar,
        CandidateRepository(sidecar, clock=FixedClock()),
        WriteControl(sidecar),
    )


def start_local_candidate(
    repository: CandidateRepository,
    *,
    idempotency_key: str = "reminder:create:candidate-1",
):
    created = repository.create(
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            route=CandidateRoute(
                provider=LOCAL_PROVIDER,
                tool_name="reminders.create_reminder",
                operation="reminders.create_reminder",
                idempotency_key=idempotency_key,
            ),
        )
    )
    confirmed = repository.update(
        created.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )
    return repository.update(
        created.candidate_id,
        CandidateUpdate(
            expected_version=confirmed.version,
            execution_status="in_progress",
        ),
    )


def finalize_local_success(
    control: WriteControl,
    *,
    idempotency_key: str = "reminder:create:candidate-1",
) -> tuple[str, str]:
    operation = "reminders.create_reminder"
    request_hash = "external-request-hash"
    decision = control.reserve_operation(
        idempotency_key=idempotency_key,
        operation=operation,
        request_hash=request_hash,
    )
    assert decision.status == "execute"
    item_id = "reminder:stable-1"
    control.finalize_success(
        idempotency_key=idempotency_key,
        operation=operation,
        item=McpItemWrite(
            item_id=item_id,
            item_type="reminder",
            external_id="reminder-1",
            external_container_id="Personal",
            title_hash="title-hash",
            time_start=None,
            time_end=None,
            status_semantics="planned",
            state_token="reminder-state:1",
            created_by_mcp=True,
            source_relation_type="created_from",
        ),
        source_refs=[],
        audit=AuditWrite(
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=True,
        ),
    )
    return operation, item_id


def finalize_local_failure(
    control: WriteControl,
    *,
    idempotency_key: str = "reminder:create:candidate-1",
) -> str:
    operation = "reminders.create_reminder"
    request_hash = "external-request-hash"
    decision = control.reserve_operation(
        idempotency_key=idempotency_key,
        operation=operation,
        request_hash=request_hash,
    )
    assert decision.status == "execute"
    control.finalize_failure(
        idempotency_key=idempotency_key,
        operation=operation,
        status="failed",
        error_code="BACKEND_FAILURE",
        audit=AuditWrite(
            request_hash=request_hash,
            result_status="failed",
            error_code="BACKEND_FAILURE",
            confirmed_by_user=True,
        ),
    )
    return operation


def assert_error_code(error: pytest.ExceptionInfo[ToolContractError], code: str) -> None:
    assert error.value.code == code


def test_local_success_terminal_registration_is_verified_against_sidecar(
    tmp_path: Path,
) -> None:
    _, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    _, item_id = finalize_local_success(control)

    succeeded = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=started.version,
            execution_status="succeeded",
            result_ref=ResultRef(
                provider=LOCAL_PROVIDER,
                status="succeeded",
                item_id=item_id,
                verification_source="agent_reported",
            ),
        ),
    )

    assert succeeded.execution_status == "succeeded"
    assert succeeded.result_ref is not None
    assert succeeded.result_ref.item_id == item_id
    assert succeeded.result_ref.verification_source == "sidecar_verified"
    assert succeeded.result_ref.audit_id is not None


def test_local_terminal_registration_rejects_result_mismatch_without_state_change(
    tmp_path: Path,
) -> None:
    _, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    finalize_local_success(control)

    with pytest.raises(ToolContractError) as mismatch:
        repository.update(
            started.candidate_id,
            CandidateUpdate(
                expected_version=started.version,
                execution_status="succeeded",
                result_ref=ResultRef(
                    provider=LOCAL_PROVIDER,
                    status="succeeded",
                    item_id="reminder:wrong-item",
                    verification_source="agent_reported",
                ),
            ),
        )

    assert_error_code(mismatch, "EXECUTION_RESULT_MISMATCH")
    unchanged = repository.get(started.candidate_id)
    assert unchanged.version == started.version
    assert unchanged.execution_status == "in_progress"


def test_local_failure_terminal_registration_uses_verified_error(
    tmp_path: Path,
) -> None:
    _, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    finalize_local_failure(control)

    failed = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=started.version,
            execution_status="failed",
            result_ref=ResultRef(
                provider=LOCAL_PROVIDER,
                status="failed",
                error_code="BACKEND_FAILURE",
                verification_source="agent_reported",
            ),
        ),
    )

    assert failed.execution_status == "failed"
    assert failed.result_ref is not None
    assert failed.result_ref.error_code == "BACKEND_FAILURE"
    assert failed.result_ref.verification_source == "sidecar_verified"


def test_reconcile_recovers_local_success_without_reserving_or_replaying_write(
    tmp_path: Path,
) -> None:
    sidecar, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    operation, item_id = finalize_local_success(control)
    with sidecar.connect() as connection:
        idempotency_before = dict(
            connection.execute(
                """
                SELECT status, result_item_id
                FROM idempotency_key
                WHERE key = ? AND operation = ?
                """,
                ("reminder:create:candidate-1", operation),
            ).fetchone()
        )

    reconciled = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=started.version,
            reconcile_execution=True,
        ),
    )

    with sidecar.connect() as connection:
        idempotency_after = dict(
            connection.execute(
                """
                SELECT status, result_item_id
                FROM idempotency_key
                WHERE key = ? AND operation = ?
                """,
                ("reminder:create:candidate-1", operation),
            ).fetchone()
        )
    assert reconciled.execution_status == "succeeded"
    assert reconciled.result_ref is not None
    assert reconciled.result_ref.item_id == item_id
    assert reconciled.result_ref.verification_source == "sidecar_verified"
    assert idempotency_after == idempotency_before


def test_reconcile_recovers_verified_local_failure(tmp_path: Path) -> None:
    _, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    finalize_local_failure(control)

    reconciled = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=started.version,
            reconcile_execution=True,
        ),
    )

    assert reconciled.execution_status == "failed"
    assert reconciled.result_ref is not None
    assert reconciled.result_ref.error_code == "BACKEND_FAILURE"
    assert reconciled.result_ref.verification_source == "sidecar_verified"


def test_reconcile_keeps_unknown_execution_in_progress_and_adds_one_issue(
    tmp_path: Path,
) -> None:
    _, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    decision = control.reserve_operation(
        idempotency_key="reminder:create:candidate-1",
        operation="reminders.create_reminder",
        request_hash="external-request-hash",
    )
    assert decision.status == "execute"
    control.finalize_failure(
        idempotency_key="reminder:create:candidate-1",
        operation="reminders.create_reminder",
        status="external_state_unknown",
        error_code="EXTERNAL_STATE_UNKNOWN",
        audit=AuditWrite(
            request_hash="external-request-hash",
            result_status="external_state_unknown",
            error_code="EXTERNAL_STATE_UNKNOWN",
            confirmed_by_user=True,
        ),
    )

    first = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=started.version,
            reconcile_execution=True,
        ),
    )
    second = repository.update(
        started.candidate_id,
        CandidateUpdate(
            expected_version=first.version,
            reconcile_execution=True,
        ),
    )

    assert first.execution_status == "in_progress"
    assert first.decision_status == "confirmed"
    assert [issue.code for issue in first.issues] == ["EXTERNAL_STATE_UNKNOWN"]
    assert [issue.code for issue in second.issues] == ["EXTERNAL_STATE_UNKNOWN"]


def test_reconcile_rejects_inconsistent_local_audit(tmp_path: Path) -> None:
    sidecar, repository, control = make_repositories(tmp_path)
    started = start_local_candidate(repository)
    operation, _ = finalize_local_success(control)
    with sidecar.connect() as connection:
        connection.execute(
            "DELETE FROM operation_audit WHERE operation = ?",
            (operation,),
        )

    with pytest.raises(ToolContractError) as mismatch:
        repository.update(
            started.candidate_id,
            CandidateUpdate(
                expected_version=started.version,
                reconcile_execution=True,
            ),
        )

    assert_error_code(mismatch, "EXECUTION_RESULT_MISMATCH")
    assert repository.get(started.candidate_id).execution_status == "in_progress"


def test_local_route_operation_must_match_candidate_action(tmp_path: Path) -> None:
    _, repository, _ = make_repositories(tmp_path)
    candidate = repository.create(
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            route=CandidateRoute(
                provider=LOCAL_PROVIDER,
                tool_name="calendar.create_event",
                operation="calendar.create_event",
                idempotency_key="calendar:create:wrong-action",
            ),
        )
    )
    confirmed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )

    with pytest.raises(ToolContractError) as mismatch:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(
                expected_version=confirmed.version,
                execution_status="in_progress",
            ),
        )

    assert_error_code(mismatch, "ROUTE_OPERATION_MISMATCH")
