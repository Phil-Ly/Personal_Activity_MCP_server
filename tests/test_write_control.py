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
        title_hash="title-hash",
        time_start="2026-07-08T10:00:00+08:00",
        time_end="2026-07-08T11:00:00+08:00",
        status_semantics="planned",
        state_token="calendar-state:1",
        created_by_mcp=True,
        source_relation_type="created_from",
    )


def audit_write(result_status: str = "succeeded") -> AuditWrite:
    return AuditWrite(
        request_hash="request-hash",
        result_status=result_status,
        error_code=None,
        confirmed_by_user=True,
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


def test_finalize_success_commits_mapping_links_idempotency_and_audit_together(
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
        links = connection.execute(
            "SELECT source_ref FROM source_link ORDER BY source_ref"
        ).fetchall()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        stored_audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == "calendar:event-1"
    assert item["state_token"] == "calendar-state:1"
    assert [row["source_ref"] for row in links] == ["file:a", "file:b"]
    assert idempotency["status"] == "succeeded"
    assert idempotency["result_item_id"] == "calendar:event-1"
    assert stored_audit["id"] == audit.audit_id
    assert stored_audit["result_status"] == "succeeded"


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
            CREATE TRIGGER fail_source_link
            BEFORE INSERT ON source_link
            BEGIN
                SELECT RAISE(ABORT, 'injected source link failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected source link failure"):
        control.finalize_success(
            idempotency_key="calendar:create:fault",
            operation="calendar.create_event",
            item=item_write(),
            source_refs=["file:a"],
            audit=audit_write(),
        )

    with repository.connect() as connection:
        item_count = connection.execute("SELECT COUNT(*) FROM mcp_item").fetchone()[0]
        link_count = connection.execute("SELECT COUNT(*) FROM source_link").fetchone()[0]
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
    assert link_count == 0
    assert status == "external_state_unknown"
    assert retry.status == "external_state_unknown"


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


def test_operation_result_matches_audit_by_terminal_state_when_hashes_repeat(
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
        confirmed_by_user=True,
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
        confirmed_by_user=True,
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
