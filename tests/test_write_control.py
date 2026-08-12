import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from personal_activity_mcp.sidecar import (
    AuditWrite,
    McpItemWrite,
    SidecarRepository,
    WriteControl,
)


def make_control(tmp_path: Path) -> tuple[SidecarRepository, WriteControl]:
    repository = SidecarRepository(tmp_path / "sidecar.sqlite3")
    repository.initialize()
    return repository, WriteControl(repository)


def item_write() -> McpItemWrite:
    return McpItemWrite(
        item_id="calendar:event-1",
        item_type="calendar_event",
        external_id="event-1",
        external_container_id="Personal",
        status_semantics="planned",
        created_by_mcp=True,
        completion_status="unknown",
    )


def calendar_container_write() -> McpItemWrite:
    return McpItemWrite(
        item_id="calendar:container-1",
        item_type="calendar",
        external_id="calendar-1",
        external_container_id="source-icloud",
        status_semantics=None,
        created_by_mcp=True,
        completion_status=None,
    )


def reminder_list_container_write() -> McpItemWrite:
    return McpItemWrite(
        item_id="reminder-list:container-1",
        item_type="reminder_list",
        external_id="list-1",
        external_container_id="source-icloud",
        status_semantics=None,
        created_by_mcp=True,
        completion_status=None,
    )


def test_calendar_container_rejects_event_status_semantics() -> None:
    with pytest.raises(ValueError, match="status_semantics is not valid for calendar"):
        calendar_container_write().model_copy(
            update={"status_semantics": "planned"}
        ).model_validate(
            {
                **calendar_container_write().model_dump(),
                "status_semantics": "planned",
            }
        )


def test_reminder_list_container_rejects_reminder_status_semantics() -> None:
    with pytest.raises(ValueError, match="status_semantics is not valid for reminder_list"):
        reminder_list_container_write().model_copy(
            update={"status_semantics": "planned"}
        ).model_validate(
            reminder_list_container_write().model_copy(update={"status_semantics": "planned"})
        )


def test_calendar_event_requires_status_semantics() -> None:
    with pytest.raises(ValueError, match="status_semantics is required"):
        McpItemWrite(
            item_id="calendar:event-1",
            item_type="calendar_event",
            external_id="event-1",
            external_container_id="calendar-1",
            status_semantics=None,
            created_by_mcp=True,
            completion_status="unknown",
        )


def audit_write(result_status: str = "succeeded") -> AuditWrite:
    return AuditWrite(
        request_hash="request-hash",
        result_status=result_status,
        error_code=None,
    )


def test_reserve_operation_returns_stable_decisions_for_existing_states(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)

    execute = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    same_key_other_operation = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.update_event",
        request_hash="other-operation-hash",
    )
    in_progress = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=[],
        audit=audit_write(),
    )
    deduplicated = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    conflict = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="different-hash",
    )
    with repository.connect() as connection:
        connection.execute(
            """
            UPDATE idempotency_key
            SET status = 'external_state_unknown'
            WHERE key = ? AND operation = ?
            """,
            ("calendar:create:1", "calendar.create_event"),
        )
    unknown = control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )

    assert execute.status == "execute"
    assert same_key_other_operation.status == "execute"
    assert in_progress.status == "in_progress"
    assert deduplicated.model_dump() == {
        "status": "deduplicated",
        "result_item_id": "calendar:event-1",
    }
    assert conflict.status == "conflict"
    assert unknown.status == "external_state_unknown"


def test_two_concurrent_reservations_allow_only_one_executor(tmp_path: Path) -> None:
    database_path = tmp_path / "sidecar.sqlite3"
    SidecarRepository(database_path).initialize()
    barrier = Barrier(2)

    def reserve() -> str:
        control = WriteControl(SidecarRepository(database_path))
        barrier.wait()
        return control.reserve_operation(
            idempotency_key="calendar:create:concurrent",
            operation="calendar.create_event",
            request_hash="request-hash",
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: reserve(), range(2)))

    assert statuses == ["execute", "in_progress"]


def test_failed_operation_can_be_reserved_again(tmp_path: Path) -> None:
    _, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:retry",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    control.finalize_failure(
        idempotency_key="calendar:create:retry",
        operation="calendar.create_event",
        status="failed",
        error_code="BACKEND_FAILURE",
        audit=audit_write("failed"),
    )

    retry = control.reserve_operation(
        idempotency_key="calendar:create:retry",
        operation="calendar.create_event",
        request_hash="request-hash",
    )

    assert retry.status == "execute"


def test_finalize_success_commits_compact_item_idempotency_and_audit_together(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    audit = audit_write()

    control.finalize_success(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=["file:b", "file:a"],
        audit=audit,
    )

    with repository.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        stored_audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == "calendar:event-1"
    assert json.loads(item["source_refs_json"]) == ["file:a", "file:b"]
    assert item["completion_status"] == "unknown"
    assert idempotency["status"] == "succeeded"
    assert idempotency["result_item_id"] == "calendar:event-1"
    assert idempotency["audit_id"] == audit.audit_id
    assert stored_audit["id"] == audit.audit_id
    assert stored_audit["result_status"] == "succeeded"


def test_finalize_success_commits_calendar_container_idempotency_and_audit(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create-container:1",
        operation="calendar.create_calendar",
        request_hash="request-hash",
    )
    audit = audit_write()

    control.finalize_success(
        idempotency_key="calendar:create-container:1",
        operation="calendar.create_calendar",
        item=calendar_container_write(),
        source_refs=[],
        audit=audit,
    )

    item = repository.get_mcp_item("calendar:container-1")
    result = control.get_operation_result(
        idempotency_key="calendar:create-container:1",
        operation="calendar.create_calendar",
    )
    assert item is not None
    assert item["item_type"] == "calendar"
    assert item["status_semantics"] is None
    assert item["completion_status"] is None
    assert result is not None
    assert result.status == "succeeded"
    assert result.result_item_id == "calendar:container-1"
    assert result.audit_id == audit.audit_id


def test_finalize_success_commits_reminder_list_container_idempotency_and_audit(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="reminders:create-list:1",
        operation="reminders.create_list",
        request_hash="request-hash",
    )
    audit = audit_write()

    control.finalize_success(
        idempotency_key="reminders:create-list:1",
        operation="reminders.create_list",
        item=reminder_list_container_write(),
        source_refs=[],
        audit=audit,
    )

    item = repository.get_mcp_item("reminder-list:container-1")
    result = control.get_operation_result(
        idempotency_key="reminders:create-list:1",
        operation="reminders.create_list",
    )
    assert item is not None
    assert item["item_type"] == "reminder_list"
    assert item["status_semantics"] is None
    assert item["completion_status"] is None
    assert result is not None
    assert result.status == "succeeded"
    assert result.result_item_id == "reminder-list:container-1"
    assert result.audit_id == audit.audit_id


def test_finalize_success_rolls_back_partial_rows_and_marks_unknown(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:fault",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    with repository.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_success_status
            BEFORE UPDATE OF status ON idempotency_key
            WHEN NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'injected finalization failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected finalization failure"):
        control.finalize_success(
            idempotency_key="calendar:create:fault",
            operation="calendar.create_event",
            item=item_write(),
            source_refs=["file:a"],
            audit=audit_write(),
        )

    with repository.connect() as connection:
        item_count = connection.execute("SELECT COUNT(*) FROM mcp_item").fetchone()[0]
        status = connection.execute(
            """
            SELECT status
            FROM idempotency_key
            WHERE key = ? AND operation = ?
            """,
            ("calendar:create:fault", "calendar.create_event"),
        ).fetchone()[0]
    retry = control.reserve_operation(
        idempotency_key="calendar:create:fault",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    assert item_count == 0
    assert status == "external_state_unknown"
    assert retry.status == "external_state_unknown"


def test_finalize_success_marks_local_only_failure_retryable(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:update:local-fault",
        operation="calendar.update_event",
        request_hash="request-hash",
    )
    with repository.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_success_status
            BEFORE UPDATE OF status ON idempotency_key
            WHEN NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'injected finalization failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected finalization failure"):
        control.finalize_success(
            idempotency_key="calendar:update:local-fault",
            operation="calendar.update_event",
            item=item_write(),
            source_refs=[],
            audit=audit_write(),
            external_write_attempted=False,
        )

    result = control.get_operation_result(
        idempotency_key="calendar:update:local-fault",
        operation="calendar.update_event",
    )
    assert result is not None
    assert result.status == "failed"
    assert result.error_code == "LOCAL_PERSISTENCE_FAILURE"
    retry = control.reserve_operation(
        idempotency_key="calendar:update:local-fault",
        operation="calendar.update_event",
        request_hash="request-hash",
    )
    assert retry.status == "execute"


def test_rejected_second_finalization_does_not_append_unknown_audit(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:already-finalized",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:create:already-finalized",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=[],
        audit=audit_write(),
    )

    with pytest.raises(ValueError, match="not pending"):
        control.finalize_success(
            idempotency_key="calendar:create:already-finalized",
            operation="calendar.create_event",
            item=item_write(),
            source_refs=[],
            audit=audit_write(),
        )

    with repository.connect() as connection:
        status = connection.execute(
            """
            SELECT status
            FROM idempotency_key
            WHERE key = ? AND operation = ?
            """,
            ("calendar:create:already-finalized", "calendar.create_event"),
        ).fetchone()[0]
        audits = connection.execute("SELECT result_status FROM operation_audit").fetchall()

    assert status == "succeeded"
    assert [row["result_status"] for row in audits] == ["succeeded"]


def test_operation_result_uses_direct_audit_id_when_request_hashes_repeat(
    tmp_path: Path,
) -> None:
    _, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:success",
        operation="calendar.create_event",
        request_hash="shared-request-hash",
    )
    success_audit = AuditWrite(
        request_hash="shared-request-hash",
        result_status="succeeded",
        error_code=None,
    )
    control.finalize_success(
        idempotency_key="calendar:create:success",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=[],
        audit=success_audit,
    )
    control.reserve_operation(
        idempotency_key="calendar:create:failure",
        operation="calendar.create_event",
        request_hash="shared-request-hash",
    )
    failure_audit = AuditWrite(
        request_hash="shared-request-hash",
        result_status="failed",
        error_code="BACKEND_FAILURE",
    )
    control.finalize_failure(
        idempotency_key="calendar:create:failure",
        operation="calendar.create_event",
        status="failed",
        error_code="BACKEND_FAILURE",
        audit=failure_audit,
    )

    success = control.get_operation_result(
        idempotency_key="calendar:create:success",
        operation="calendar.create_event",
    )
    failure = control.get_operation_result(
        idempotency_key="calendar:create:failure",
        operation="calendar.create_event",
    )

    assert success is not None
    assert success.audit_id == success_audit.audit_id
    assert success.audit_result_status == "succeeded"
    assert success.audit_target_item_id == "calendar:event-1"
    assert failure is not None
    assert failure.audit_id == failure_audit.audit_id
    assert failure.audit_result_status == "failed"
    assert failure.audit_error_code == "BACKEND_FAILURE"


def test_finalize_success_merges_source_refs_and_preserves_calendar_completion(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="create-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        item=item_write().model_copy(update={"completion_status": "completed"}),
        source_refs=["file:a"],
        audit=AuditWrite(
            request_hash="create-hash",
            result_status="succeeded",
            error_code=None,
        ),
    )
    control.reserve_operation(
        idempotency_key="calendar:update:1",
        operation="calendar.update_event",
        request_hash="update-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:update:1",
        operation="calendar.update_event",
        item=item_write().model_copy(update={"completion_status": None}),
        source_refs=["file:b", "file:a"],
        audit=AuditWrite(
            request_hash="update-hash",
            result_status="succeeded",
            error_code=None,
        ),
    )

    with repository.connect() as connection:
        item = connection.execute(
            "SELECT completion_status, source_refs_json FROM mcp_item"
        ).fetchone()

    assert item["completion_status"] == "completed"
    assert json.loads(item["source_refs_json"]) == ["file:a", "file:b"]


def test_finalize_success_never_reassigns_an_item_id_to_another_external_item(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        request_hash="request-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:create:1",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=[],
        audit=audit_write(),
    )
    control.reserve_operation(
        idempotency_key="calendar:update:2",
        operation="calendar.update_event",
        request_hash="other-hash",
    )

    with pytest.raises(sqlite3.IntegrityError, match="item identity"):
        control.finalize_success(
            idempotency_key="calendar:update:2",
            operation="calendar.update_event",
            item=item_write().model_copy(
                update={
                    "external_id": "event-2",
                    "created_by_mcp": False,
                }
            ),
            source_refs=[],
            audit=AuditWrite(
                request_hash="other-hash",
                result_status="succeeded",
                error_code=None,
            ),
        )

    assert repository.get_mcp_item("calendar:event-1")["external_id"] == "event-1"


def test_finalize_success_allows_cumulative_source_refs_beyond_request_limit(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    first_refs = [f"file:first/{index}" for index in range(60)]
    second_refs = [f"file:second/{index}" for index in range(60)]
    control.reserve_operation(
        idempotency_key="calendar:create:refs",
        operation="calendar.create_event",
        request_hash="create-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:create:refs",
        operation="calendar.create_event",
        item=item_write(),
        source_refs=first_refs,
        audit=AuditWrite(
            request_hash="create-hash",
            result_status="succeeded",
            error_code=None,
        ),
    )
    control.reserve_operation(
        idempotency_key="calendar:update:refs",
        operation="calendar.update_event",
        request_hash="update-hash",
    )

    control.finalize_success(
        idempotency_key="calendar:update:refs",
        operation="calendar.update_event",
        item=item_write(),
        source_refs=second_refs,
        audit=AuditWrite(
            request_hash="update-hash",
            result_status="succeeded",
            error_code=None,
        ),
    )

    item = repository.get_mcp_item("calendar:event-1")
    assert item is not None
    assert len(json.loads(str(item["source_refs_json"]))) == 120


def test_finalize_success_compare_and_sets_calendar_completion_status(
    tmp_path: Path,
) -> None:
    repository, control = make_control(tmp_path)
    control.reserve_operation(
        idempotency_key="calendar:update:first",
        operation="calendar.update_event",
        request_hash="first-hash",
    )
    control.reserve_operation(
        idempotency_key="calendar:update:second",
        operation="calendar.update_event",
        request_hash="second-hash",
    )
    control.finalize_success(
        idempotency_key="calendar:update:first",
        operation="calendar.update_event",
        item=item_write().model_copy(
            update={
                "completion_status": "completed",
                "expected_completion_status": "unknown",
            }
        ),
        source_refs=[],
        audit=AuditWrite(
            request_hash="first-hash",
            result_status="succeeded",
            error_code=None,
        ),
        external_write_attempted=False,
    )

    with pytest.raises(sqlite3.IntegrityError, match="completion status changed"):
        control.finalize_success(
            idempotency_key="calendar:update:second",
            operation="calendar.update_event",
            item=item_write().model_copy(
                update={
                    "completion_status": "incomplete",
                    "expected_completion_status": "unknown",
                }
            ),
            source_refs=[],
            audit=AuditWrite(
                request_hash="second-hash",
                result_status="succeeded",
                error_code=None,
            ),
            external_write_attempted=False,
        )

    item = repository.get_mcp_item("calendar:event-1")
    assert item is not None
    assert item["completion_status"] == "completed"
