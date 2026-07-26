from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_activity_mcp.candidates import (
    ActionCandidate,
    CandidateCreate,
    CandidateIssue,
    CandidateRoute,
    CandidateUpdate,
    ResultRef,
)
from personal_activity_mcp.common import TargetRef


@pytest.mark.parametrize(
    ("action_type", "payload"),
    [
        (
            "create_event",
            {
                "title": "Project review",
                "start": "2026-07-28T09:00:00+08:00",
                "end": "2026-07-28T10:00:00+08:00",
                "description": "Review the open decisions",
            },
        ),
        (
            "update_event",
            {
                "description": None,
                "completion_status": "completed",
            },
        ),
        (
            "create_task",
            {
                "title": "Send review notes",
                "due_at": "2026-07-28T18:00:00+08:00",
                "description": "Share with the project group",
            },
        ),
        ("complete_task", {}),
        ("none", {}),
    ],
)
def test_candidate_create_accepts_each_standard_action_payload(
    action_type: str,
    payload: dict[str, object],
) -> None:
    command = CandidateCreate(
        action_type=action_type,
        payload=payload,
        source_refs=[" reminder:Personal:item-1 ", "reminder:Personal:item-1"],
    )

    assert command.action_type == action_type
    assert command.source_refs == ["reminder:Personal:item-1"]


@pytest.mark.parametrize(
    ("action_type", "payload", "message"),
    [
        (
            "create_event",
            {
                "title": "Project review",
                "start": "2026-07-28T09:00:00",
                "end": "2026-07-28T10:00:00+08:00",
            },
            "timezone",
        ),
        (
            "create_event",
            {
                "title": "Project review",
                "start": "2026-07-28T10:00:00+08:00",
                "end": "2026-07-28T09:00:00+08:00",
            },
            "start must be before end",
        ),
        ("update_event", {}, "at least one"),
        ("update_event", {"title": "Not editable"}, "extra"),
        ("create_task", {"title": " "}, "title"),
        ("complete_task", {"title": "Not allowed"}, "extra"),
        ("none", {"title": "Not allowed"}, "extra"),
    ],
)
def test_candidate_create_rejects_payloads_outside_the_action_contract(
    action_type: str,
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        CandidateCreate(action_type=action_type, payload=payload)


def test_update_and_complete_targets_use_the_matching_resource_type() -> None:
    update = CandidateCreate(
        action_type="update_event",
        payload={"description": "Updated"},
        target_ref=TargetRef(
            resource_type="calendar_event",
            item_id="event-1",
            container_id="Personal",
        ),
    )
    complete = CandidateCreate(
        action_type="complete_task",
        payload={},
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-1",
            container_id="Personal",
        ),
    )

    assert update.target_ref is not None
    assert complete.target_ref is not None
    with pytest.raises(ValidationError, match="target_ref"):
        CandidateCreate(
            action_type="update_event",
            payload={"description": "Updated"},
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
        )


def test_route_and_its_public_routing_fields_are_independently_optional() -> None:
    assert CandidateRoute().model_dump() == {
        "provider": None,
        "tool_name": None,
        "operation": None,
        "idempotency_key": None,
    }
    assert CandidateRoute(provider="feishu").tool_name is None
    assert CandidateRoute(tool_name="tasks.create").provider is None


def test_extensions_require_a_provider_namespace() -> None:
    command = CandidateCreate(
        action_type="create_task",
        payload={"title": "Send review notes"},
        extensions={"feishu.priority": 1},
    )

    assert command.extensions == {"feishu.priority": 1}
    with pytest.raises(ValidationError, match="namespace"):
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            extensions={"priority": 1},
        )


@pytest.mark.parametrize(
    "unsafe_values",
    [
        {"feishu.auth": {"access_token": "secret-value"}},
        {"feishu.model": {"hidden_reasoning": "private chain"}},
    ],
)
def test_candidate_nested_data_rejects_credentials_and_hidden_reasoning(
    unsafe_values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="prohibited sensitive"):
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            extensions=unsafe_values,
        )


def test_candidate_source_and_result_references_reject_sensitive_content() -> None:
    with pytest.raises(ValidationError, match="prohibited sensitive"):
        CandidateCreate(
            action_type="none",
            payload={},
            source_refs=["access_token=secret-value"],
        )

    with pytest.raises(ValidationError, match="prohibited sensitive"):
        ResultRef(
            provider="external",
            status="failed",
            error_code="password=secret-value",
            verification_source="agent_reported",
        )


def test_candidate_serialized_size_is_limited_to_64_kib() -> None:
    with pytest.raises(ValidationError, match="64 KiB"):
        CandidateCreate(
            action_type="create_task",
            payload={"title": "Send review notes"},
            extensions={"provider.large": "x" * (65 * 1024)},
        )


def test_authoritative_candidate_revalidates_persisted_sensitive_payload() -> None:
    with pytest.raises(ValidationError, match="prohibited sensitive"):
        ActionCandidate(
            candidate_id="candidate:1",
            version=1,
            action_type="create_task",
            payload={"title": "password=secret-value"},
            target_ref=None,
            source_refs=[],
            decision_status="pending",
            execution_status="not_started",
            issues=[],
            route=None,
            result_ref=None,
            created_at=datetime(2026, 7, 28, tzinfo=UTC),
            updated_at=datetime(2026, 7, 28, tzinfo=UTC),
            deleted_at=None,
        )


def test_candidate_update_preserves_explicit_null_fields() -> None:
    command = CandidateUpdate(
        expected_version=3,
        target_ref=None,
        route=None,
        result_ref=None,
    )

    assert command.model_fields_set >= {"target_ref", "route", "result_ref"}


def test_issue_and_result_models_normalize_minimal_references() -> None:
    issue = CandidateIssue(
        code="missing_information",
        message=" Target calendar is missing ",
        related_item_ids=[" event-1 "],
    )
    result = ResultRef(
        provider=" feishu ",
        status="succeeded",
        item_id=" task-1 ",
        verification_source="agent_reported",
    )

    assert issue.message == "Target calendar is missing"
    assert issue.related_item_ids == ["event-1"]
    assert result.provider == "feishu"
    assert result.item_id == "task-1"


def test_candidate_query_datetimes_are_timezone_aware() -> None:
    from personal_activity_mcp.candidates import CandidateQuery

    query = CandidateQuery(
        created_from=datetime(2026, 7, 28, tzinfo=UTC),
        created_to=datetime(2026, 7, 29, tzinfo=UTC),
    )
    assert query.created_from is not None

    with pytest.raises(ValidationError):
        CandidateQuery(created_from=datetime(2026, 7, 28))
