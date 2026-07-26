from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock

import pytest

from personal_activity_mcp.candidates import (
    CandidateCreate,
    CandidateIssue,
    CandidateQuery,
    CandidateRepository,
    CandidateRoute,
    CandidateUpdate,
    ResultRef,
)
from personal_activity_mcp.common import TargetRef, ToolContractError
from personal_activity_mcp.sidecar import SidecarRepository


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self._lock = Lock()

    def now(self) -> datetime:
        with self._lock:
            return self.value

    def advance(self, seconds: int = 1) -> None:
        with self._lock:
            self.value += timedelta(seconds=seconds)


def make_repository(
    tmp_path: Path,
    *,
    clock: MutableClock | None = None,
) -> tuple[SidecarRepository, CandidateRepository, MutableClock]:
    sidecar = SidecarRepository(tmp_path / "sidecar" / "state.sqlite3")
    sidecar.initialize()
    candidate_clock = clock or MutableClock(datetime(2026, 7, 28, 9, tzinfo=UTC))
    return sidecar, CandidateRepository(sidecar, clock=candidate_clock), candidate_clock


def create_task_command(
    title: str = "Send review notes",
    *,
    issues: list[CandidateIssue] | None = None,
    route: CandidateRoute | None = None,
) -> CandidateCreate:
    return CandidateCreate(
        action_type="create_task",
        payload={"title": title},
        extensions={"feishu.priority": 1},
        source_refs=["file:daily/2026-07-28.md"],
        issues=issues or [],
        route=route,
    )


def assert_error_code(error: pytest.ExceptionInfo[ToolContractError], code: str) -> None:
    assert error.value.code == code


def test_create_persists_authoritative_candidate_links_and_audit_across_restart(
    tmp_path: Path,
) -> None:
    sidecar, repository, clock = make_repository(tmp_path)

    created = repository.create(
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            extensions={"feishu.priority": 1},
            source_refs=[
                "file:daily/2026-07-28.md",
                " file:daily/2026-07-28.md ",
            ],
            route=CandidateRoute(provider="feishu"),
        )
    )
    restarted = CandidateRepository(sidecar, clock=clock).get(created.candidate_id)

    assert created.candidate_id.startswith("candidate:")
    assert created.version == 1
    assert created.decision_status == "pending"
    assert created.execution_status == "not_started"
    assert created.created_at == datetime(2026, 7, 28, 9, tzinfo=UTC)
    assert restarted == created
    assert restarted.extensions == {"feishu.priority": 1}
    assert restarted.source_refs == ["file:daily/2026-07-28.md"]
    with sidecar.connect() as connection:
        links = connection.execute(
            """
            SELECT target_candidate_id, source_ref, relation_type
            FROM source_link
            WHERE target_candidate_id = ?
            """,
            (created.candidate_id,),
        ).fetchall()
        audits = connection.execute(
            """
            SELECT operation, target_candidate_id, result_status
            FROM operation_audit
            WHERE target_candidate_id = ?
            """,
            (created.candidate_id,),
        ).fetchall()
    assert [tuple(row) for row in links] == [
        (
            created.candidate_id,
            "file:daily/2026-07-28.md",
            "supported_by",
        )
    ]
    assert [tuple(row) for row in audits] == [
        ("candidates.create", created.candidate_id, "created")
    ]


def test_list_filters_and_cursor_page_candidates_in_stable_order(tmp_path: Path) -> None:
    _, repository, clock = make_repository(tmp_path)
    first = repository.create(create_task_command("First"))
    clock.advance()
    second = repository.create(
        CandidateCreate(
            action_type="create_event",
            payload={
                "title": "Second",
                "start": "2026-07-29T09:00:00+00:00",
                "end": "2026-07-29T10:00:00+00:00",
            },
        )
    )
    clock.advance()
    third = repository.create(
        create_task_command(
            "Third",
            route=CandidateRoute(
                provider="external",
                tool_name="tasks.create",
                operation="tasks.create",
            ),
        )
    )
    repository.update(
        third.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )
    repository.update(
        third.candidate_id,
        CandidateUpdate(expected_version=2, execution_status="in_progress"),
    )

    page_one = repository.list_candidates(CandidateQuery(limit=1))
    page_two = repository.list_candidates(CandidateQuery(limit=1, cursor=page_one.next_cursor))
    task_candidates = repository.list_candidates(
        CandidateQuery(
            action_type="create_task",
            created_from=first.created_at,
            created_to=third.created_at + timedelta(seconds=1),
        )
    )
    active_candidates = repository.list_candidates(
        CandidateQuery(
            decision_status="confirmed",
            execution_status="in_progress",
        )
    )

    assert [item.candidate_id for item in page_one.candidates] == [first.candidate_id]
    assert page_one.next_cursor is not None
    assert [item.candidate_id for item in page_two.candidates] == [second.candidate_id]
    assert [item.candidate_id for item in task_candidates.candidates] == [
        first.candidate_id,
        third.candidate_id,
    ]
    assert [item.candidate_id for item in active_candidates.candidates] == [third.candidate_id]


def test_semantic_update_resets_confirmation_execution_and_result(
    tmp_path: Path,
) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        create_task_command(
            route=CandidateRoute(
                provider="feishu",
                tool_name="tasks.create",
                operation="tasks.create",
            )
        )
    )
    confirmed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=1,
            decision_status="confirmed",
        ),
    )
    started = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=2,
            execution_status="in_progress",
        ),
    )
    succeeded = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=3,
            execution_status="succeeded",
            result_ref=ResultRef(
                provider="feishu",
                status="succeeded",
                item_id="task-1",
                verification_source="agent_reported",
            ),
        ),
    )

    changed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=succeeded.version,
            payload={"title": "Send final review notes"},
        ),
    )

    assert confirmed.decision_status == "confirmed"
    assert started.execution_status == "in_progress"
    assert succeeded.execution_status == "succeeded"
    assert changed.version == 5
    assert changed.decision_status == "pending"
    assert changed.execution_status == "not_started"
    assert changed.result_ref is None
    assert changed.route is not None
    assert changed.route.provider == "feishu"
    assert changed.route.tool_name == "tasks.create"
    assert changed.route.operation is None
    assert changed.route.idempotency_key is None


def test_concurrent_updates_with_the_same_version_allow_only_one_writer(
    tmp_path: Path,
) -> None:
    sidecar, repository, clock = make_repository(tmp_path)
    candidate = repository.create(create_task_command())
    barrier = Barrier(2)

    def update(provider: str) -> str:
        concurrent_repository = CandidateRepository(sidecar, clock=clock)
        barrier.wait()
        try:
            concurrent_repository.update(
                candidate.candidate_id,
                CandidateUpdate(
                    expected_version=1,
                    route=CandidateRoute(provider=provider),
                ),
            )
        except ToolContractError as error:
            return error.code
        return "updated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(update, ("feishu", "other")))

    assert outcomes == ["VERSION_CONFLICT", "updated"]
    assert repository.get(candidate.candidate_id).version == 2


def test_blocking_issues_and_incomplete_route_prevent_lifecycle_progress(
    tmp_path: Path,
) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        create_task_command(
            issues=[
                CandidateIssue(
                    code="missing_information",
                    message="Target provider is missing",
                )
            ]
        )
    )

    with pytest.raises(ToolContractError) as confirmation_error:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(expected_version=1, decision_status="confirmed"),
        )
    assert_error_code(confirmation_error, "CANDIDATE_HAS_BLOCKING_ISSUES")
    assert repository.get(candidate.candidate_id).version == 1

    cleared = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, issues=[]),
    )
    confirmed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=2, decision_status="confirmed"),
    )
    with pytest.raises(ToolContractError) as route_error:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(expected_version=3, execution_status="in_progress"),
        )
    assert_error_code(route_error, "ROUTE_NOT_EXECUTABLE")
    assert cleared.issues == []
    assert confirmed.decision_status == "confirmed"
    assert repository.get(candidate.candidate_id).version == 3


def test_existing_target_actions_require_complete_target_before_execution(
    tmp_path: Path,
) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        CandidateCreate(
            action_type="update_event",
            payload={"description": "Updated"},
            route=CandidateRoute(
                provider="external",
                tool_name="calendar.update",
                operation="calendar.update",
            ),
        )
    )
    confirmed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )

    with pytest.raises(ToolContractError) as missing_target:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(expected_version=2, execution_status="in_progress"),
        )
    assert_error_code(missing_target, "TARGET_REF_REQUIRED")

    target_added = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=confirmed.version,
            target_ref=TargetRef(
                resource_type="calendar_event",
                item_id="event-1",
                container_id="Personal",
            ),
        ),
    )
    assert target_added.decision_status == "pending"


def test_execution_transitions_are_strict_and_failed_can_retry(tmp_path: Path) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        create_task_command(
            route=CandidateRoute(
                provider="external",
                tool_name="tasks.create",
                operation="tasks.create",
            )
        )
    )
    with pytest.raises(ToolContractError) as direct_success:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(
                expected_version=1,
                execution_status="succeeded",
                result_ref=ResultRef(
                    provider="external",
                    status="succeeded",
                    item_id="task-1",
                    verification_source="agent_reported",
                ),
            ),
        )
    assert_error_code(direct_success, "INVALID_STATE_TRANSITION")

    confirmed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )
    started = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=2, execution_status="in_progress"),
    )
    failed = repository.update(
        candidate.candidate_id,
        CandidateUpdate(
            expected_version=3,
            execution_status="failed",
            result_ref=ResultRef(
                provider="external",
                status="failed",
                error_code="REMOTE_FAILURE",
                verification_source="agent_reported",
            ),
        ),
    )
    retried = repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=4, execution_status="in_progress"),
    )

    assert confirmed.version == 2
    assert started.execution_status == "in_progress"
    assert failed.execution_status == "failed"
    assert retried.execution_status == "in_progress"


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"title": "Changed while executing"}},
        {
            "route": CandidateRoute(
                provider="other",
                tool_name="tasks.create",
                operation="tasks.create",
            )
        },
    ],
)
def test_in_progress_candidate_allows_only_terminal_execution_result(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        create_task_command(
            route=CandidateRoute(
                provider="external",
                tool_name="tasks.create",
                operation="tasks.create",
            )
        )
    )
    repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )
    repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=2, execution_status="in_progress"),
    )

    with pytest.raises(ToolContractError) as mutation_error:
        repository.update(
            candidate.candidate_id,
            CandidateUpdate(expected_version=3, **change),
        )

    assert_error_code(mutation_error, "INVALID_STATE_TRANSITION")
    unchanged = repository.get(candidate.candidate_id)
    assert unchanged.version == 3
    assert unchanged.execution_status == "in_progress"


def test_in_progress_candidate_cannot_be_deleted_before_reconciliation(
    tmp_path: Path,
) -> None:
    _, repository, _ = make_repository(tmp_path)
    candidate = repository.create(
        create_task_command(
            route=CandidateRoute(
                provider="external",
                tool_name="tasks.create",
                operation="tasks.create",
            )
        )
    )
    repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=1, decision_status="confirmed"),
    )
    repository.update(
        candidate.candidate_id,
        CandidateUpdate(expected_version=2, execution_status="in_progress"),
    )

    with pytest.raises(ToolContractError) as delete_error:
        repository.delete(candidate.candidate_id, expected_version=3)

    assert_error_code(delete_error, "INVALID_STATE_TRANSITION")
    unchanged = repository.get(candidate.candidate_id)
    assert unchanged.version == 3
    assert unchanged.deleted_at is None


def test_soft_delete_hides_candidate_and_preserves_audited_record(
    tmp_path: Path,
) -> None:
    sidecar, repository, _ = make_repository(tmp_path)
    candidate = repository.create(create_task_command())

    deleted = repository.delete(candidate.candidate_id, expected_version=1)

    assert deleted.version == 2
    assert deleted.deleted_at is not None
    with pytest.raises(ToolContractError) as hidden:
        repository.get(candidate.candidate_id)
    assert_error_code(hidden, "CANDIDATE_NOT_FOUND")
    assert repository.get(candidate.candidate_id, include_deleted=True) == deleted
    assert repository.list_candidates(CandidateQuery()).candidates == []
    assert repository.list_candidates(CandidateQuery(include_deleted=True)).candidates == [deleted]
    with pytest.raises(ToolContractError) as stale_delete:
        repository.delete(candidate.candidate_id, expected_version=1)
    assert_error_code(stale_delete, "VERSION_CONFLICT")
    with sidecar.connect() as connection:
        audit_statuses = [
            row[0]
            for row in connection.execute(
                """
                SELECT result_status
                FROM operation_audit
                WHERE target_candidate_id = ?
                ORDER BY rowid
                """,
                (candidate.candidate_id,),
            ).fetchall()
        ]
    assert audit_statuses == ["created", "deleted"]


def test_get_and_list_do_not_append_audit_rows(tmp_path: Path) -> None:
    sidecar, repository, _ = make_repository(tmp_path)
    candidate = repository.create(create_task_command())

    repository.get(candidate.candidate_id)
    repository.list_candidates(CandidateQuery())

    with sidecar.connect() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM operation_audit WHERE target_candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0]
    assert audit_count == 1
