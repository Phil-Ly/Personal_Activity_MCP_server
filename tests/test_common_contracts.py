from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_activity_mcp.common import (
    TargetRef,
    ToolContractError,
    decode_cursor,
    encode_cursor,
    error_result,
    normalize_source_refs,
    validate_limit,
)


def test_target_ref_requires_non_empty_identity() -> None:
    target = TargetRef(
        resource_type="calendar_event",
        item_id="event-1",
        container_id="Personal",
    )

    assert target.item_id == "event-1"
    assert target.container_id == "Personal"

    with pytest.raises(ValidationError):
        TargetRef(
            resource_type="calendar_event",
            item_id=" ",
            container_id="Personal",
        )


def test_cursor_round_trip_is_opaque_and_tampering_is_rejected() -> None:
    cursor = encode_cursor(("2026-07-01T09:00:00+00:00", "Personal", "event-1"))

    assert "event-1" not in cursor
    assert decode_cursor(cursor) == (
        "2026-07-01T09:00:00+00:00",
        "Personal",
        "event-1",
    )

    with pytest.raises(ValueError, match="cursor is invalid"):
        decode_cursor(f"{cursor}broken")


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_limit_rejects_values_outside_public_bounds(limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        validate_limit(limit)


def test_source_refs_are_trimmed_deduplicated_and_stably_sorted() -> None:
    assert normalize_source_refs(
        [
            " reminder:Personal:item-2 ",
            "",
            "calendar:Personal:event-1",
            "reminder:Personal:item-2",
        ]
    ) == [
        "calendar:Personal:event-1",
        "reminder:Personal:item-2",
    ]


def test_source_refs_reject_multiline_content_and_private_keys() -> None:
    with pytest.raises(ValueError, match="source_ref must be a single-line opaque identifier"):
        normalize_source_refs(["meeting notes\nfull body"])

    with pytest.raises(ValueError, match="source_ref contains prohibited sensitive content"):
        normalize_source_refs(["-----BEGIN PRIVATE KEY-----"])


def test_error_result_returns_structured_failure_without_sensitive_text() -> None:
    result = error_result(
        ToolContractError(
            code="BACKEND_FAILURE",
            message=(
                f"Calendar automation failed for {Path.home()}/Library/Calendars with notes=private"
            ),
            retryable=True,
            public_message="Calendar backend request failed",
        )
    )

    assert result.isError is True
    assert result.structuredContent == {
        "code": "BACKEND_FAILURE",
        "message": "Calendar backend request failed",
        "retryable": True,
    }
    rendered = result.model_dump_json()
    assert str(Path.home()) not in rendered
    assert "private" not in rendered


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("INVALID_ARGUMENT", False),
        ("SOURCE_NOT_AUTHORIZED", False),
        ("TARGET_READ_ONLY", False),
        ("USER_CONFIRMATION_REQUIRED", False),
        ("VERSION_CONFLICT", False),
        ("EXTERNAL_STATE_CHANGED", False),
        ("IDEMPOTENCY_CONFLICT", False),
        ("BACKEND_FAILURE", True),
        ("EXTERNAL_STATE_UNKNOWN", True),
    ],
)
def test_typed_contract_errors_preserve_public_error_codes(
    code: str,
    retryable: bool,
) -> None:
    result = error_result(
        ToolContractError(
            code=code,
            message="sensitive internal context",
            retryable=retryable,
            public_message="Safe public message",
        )
    )

    assert result.isError is True
    assert result.structuredContent == {
        "code": code,
        "message": "Safe public message",
        "retryable": retryable,
    }


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("Unknown calendar_ids: Secret", "SOURCE_NOT_AUTHORIZED"),
        ("Reminder list is not allowed for writes: Work", "TARGET_READ_ONLY"),
        ("USER_CONFIRMATION_REQUIRED", "USER_CONFIRMATION_REQUIRED"),
        ("idempotency_key conflicts with different request", "IDEMPOTENCY_CONFLICT"),
        ("start must be before end", "INVALID_ARGUMENT"),
    ],
)
def test_error_result_maps_existing_repository_errors(
    message: str,
    expected_code: str,
) -> None:
    result = error_result(ValueError(message))

    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["code"] == expected_code
    assert set(result.structuredContent) == {"code", "message", "retryable"}
