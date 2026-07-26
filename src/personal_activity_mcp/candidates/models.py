"""Provider-neutral ActionCandidate contracts."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from personal_activity_mcp.common import TargetRef, normalize_source_refs, validate_limit

ActionType = Literal[
    "create_event",
    "update_event",
    "create_task",
    "complete_task",
    "none",
]
DecisionStatus = Literal["pending", "confirmed", "rejected"]
ExecutionStatus = Literal["not_started", "in_progress", "succeeded", "failed"]
IssueCode = Literal[
    "source_conflict",
    "time_overlap",
    "possible_duplicate",
    "protected_item",
    "missing_information",
    "routing_unavailable",
    "EXTERNAL_STATE_UNKNOWN",
]
VerificationSource = Literal["sidecar_verified", "agent_reported"]

MAX_CANDIDATE_BYTES = 64 * 1024
_EXTENSION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SENSITIVE_TEXT = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)"
    r"\s*[=:])",
    re.IGNORECASE,
)
_RESERVED_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "chain_of_thought",
    "client_secret",
    "credential",
    "credentials",
    "hidden_reasoning",
    "model_reasoning",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "system_prompt",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateEventPayload(_StrictModel):
    title: str
    start: AwareDatetime
    end: AwareDatetime
    description: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _non_empty(value, "title")

    @model_validator(mode="after")
    def validate_range(self) -> CreateEventPayload:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class UpdateEventPayload(_StrictModel):
    description: str | None = None
    completion_status: Literal["unknown", "incomplete", "completed"] | None = None

    @model_validator(mode="after")
    def require_patch(self) -> UpdateEventPayload:
        if not self.model_fields_set.intersection({"description", "completion_status"}):
            raise ValueError("update_event payload requires at least one editable field")
        return self


class CreateTaskPayload(_StrictModel):
    title: str
    due_at: AwareDatetime | None = None
    description: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _non_empty(value, "title")


class CompleteTaskPayload(_StrictModel):
    pass


class NonePayload(_StrictModel):
    pass


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "create_event": CreateEventPayload,
    "update_event": UpdateEventPayload,
    "create_task": CreateTaskPayload,
    "complete_task": CompleteTaskPayload,
    "none": NonePayload,
}


class CandidateIssue(_StrictModel):
    code: IssueCode
    message: str
    related_item_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _safe_string(value, "issue message")

    @field_validator("related_item_ids")
    @classmethod
    def validate_related_item_ids(cls, values: list[str]) -> list[str]:
        return [_safe_string(value, "related item ID") for value in values]


class CandidateRoute(_StrictModel):
    provider: str | None = None
    tool_name: str | None = None
    operation: str | None = None
    idempotency_key: str | None = None

    @field_validator("provider", "tool_name", "operation", "idempotency_key")
    @classmethod
    def validate_route_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _safe_string(value, info.field_name)

    def is_executable(self) -> bool:
        return all((self.provider, self.tool_name, self.operation))


class ResultRef(_StrictModel):
    provider: str
    status: Literal["succeeded", "failed"]
    item_id: str | None = None
    container_id: str | None = None
    error_code: str | None = None
    audit_id: str | None = None
    verification_source: VerificationSource

    @field_validator(
        "provider",
        "item_id",
        "container_id",
        "error_code",
        "audit_id",
    )
    @classmethod
    def validate_reference_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _safe_string(value, info.field_name)

    @model_validator(mode="after")
    def validate_status_reference(self) -> ResultRef:
        if self.status == "succeeded" and self.item_id is None:
            raise ValueError("succeeded result_ref requires item_id")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed result_ref requires error_code")
        return self


class CandidateCreate(_StrictModel):
    action_type: ActionType
    payload: dict[str, object]
    extensions: dict[str, object] = Field(default_factory=dict)
    target_ref: TargetRef | None = None
    source_refs: list[str] = Field(default_factory=list)
    issues: list[CandidateIssue] = Field(default_factory=list)
    route: CandidateRoute | None = None

    @field_validator("source_refs")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        return normalize_source_refs(values)

    @field_validator("extensions")
    @classmethod
    def validate_extensions(cls, values: dict[str, object]) -> dict[str, object]:
        for key in values:
            if not _EXTENSION_KEY.fullmatch(key):
                raise ValueError("extension keys require a provider namespace")
        _validate_candidate_value(values)
        return values

    @model_validator(mode="after")
    def validate_candidate_create(self) -> CandidateCreate:
        self.payload = _normalize_payload(self.action_type, self.payload)
        _validate_candidate_value(self.payload)
        _validate_target_type(self.action_type, self.target_ref)
        _validate_serialized_size(self.model_dump(mode="json"))
        return self


class CandidateUpdate(_StrictModel):
    expected_version: int = Field(ge=1)
    action_type: ActionType | None = None
    payload: dict[str, object] | None = None
    extensions: dict[str, object] | None = None
    target_ref: TargetRef | None = None
    source_refs: list[str] | None = None
    decision_status: DecisionStatus | None = None
    execution_status: ExecutionStatus | None = None
    issues: list[CandidateIssue] | None = None
    route: CandidateRoute | None = None
    result_ref: ResultRef | None = None
    reconcile_execution: bool = False

    @field_validator("source_refs")
    @classmethod
    def normalize_references(cls, values: list[str] | None) -> list[str] | None:
        return normalize_source_refs(values) if values is not None else None

    @field_validator("extensions")
    @classmethod
    def validate_extensions(
        cls,
        values: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if values is None:
            return None
        for key in values:
            if not _EXTENSION_KEY.fullmatch(key):
                raise ValueError("extension keys require a provider namespace")
        _validate_candidate_value(values)
        return values

    @field_validator("payload")
    @classmethod
    def validate_payload_data(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if value is not None:
            _validate_candidate_value(value)
        return value

    @model_validator(mode="after")
    def validate_update_command(self) -> CandidateUpdate:
        fields = self.model_fields_set - {"expected_version", "reconcile_execution"}
        if not fields and not self.reconcile_execution:
            raise ValueError("Candidate update requires at least one changed field")
        if self.action_type is not None and self.payload is not None:
            self.payload = _normalize_payload(self.action_type, self.payload)
            _validate_target_type(self.action_type, self.target_ref)
        _validate_serialized_size(self.model_dump(mode="json"))
        return self


class CandidateQuery(_StrictModel):
    decision_status: DecisionStatus | None = None
    execution_status: ExecutionStatus | None = None
    action_type: ActionType | None = None
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    include_deleted: bool = False
    limit: int = 100
    cursor: str | None = None

    @field_validator("limit")
    @classmethod
    def validate_page_size(cls, value: int) -> int:
        return validate_limit(value)

    @model_validator(mode="after")
    def validate_created_range(self) -> CandidateQuery:
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from >= self.created_to
        ):
            raise ValueError("created_from must be before created_to")
        return self


class ActionCandidate(_StrictModel):
    candidate_id: str
    version: int = Field(ge=1)
    action_type: ActionType
    payload: dict[str, object]
    extensions: dict[str, object] = Field(default_factory=dict)
    target_ref: TargetRef | None
    source_refs: list[str]
    decision_status: DecisionStatus
    execution_status: ExecutionStatus
    issues: list[CandidateIssue]
    route: CandidateRoute | None
    result_ref: ResultRef | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_full_candidate(self) -> ActionCandidate:
        self.payload = _normalize_payload(self.action_type, self.payload)
        _validate_target_type(self.action_type, self.target_ref)
        _validate_candidate_value(self.payload)
        _validate_candidate_value(self.extensions)
        _validate_serialized_size(self.model_dump(mode="json"))
        return self


class CandidateListResult(_StrictModel):
    candidates: list[ActionCandidate]
    next_cursor: str | None = None


def normalize_payload(action_type: ActionType, payload: dict[str, object]) -> dict[str, object]:
    """Validate and canonicalize a payload for its selected action type."""
    return _normalize_payload(action_type, payload)


def validate_full_candidate_size(candidate: ActionCandidate) -> None:
    """Reject a full Candidate whose JSON representation exceeds the public limit."""
    _validate_serialized_size(candidate.model_dump(mode="json"))


def _normalize_payload(action_type: ActionType, payload: dict[str, object]) -> dict[str, object]:
    model = _PAYLOAD_MODELS[action_type].model_validate(payload)
    return model.model_dump(mode="json", exclude_unset=True)


def _validate_target_type(action_type: ActionType, target_ref: TargetRef | None) -> None:
    if target_ref is None:
        return
    expected_type = {
        "update_event": "calendar_event",
        "complete_task": "reminder",
    }.get(action_type)
    if expected_type is None:
        raise ValueError(f"{action_type} does not accept target_ref")
    if target_ref.resource_type != expected_type:
        raise ValueError(f"target_ref does not match {action_type}")


def _validate_candidate_value(value: object, *, key: str | None = None) -> None:
    if key is not None and _normalized_key(key) in _RESERVED_KEYS:
        raise ValueError("Candidate data contains a prohibited sensitive key")
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ValueError("Candidate object keys must be strings")
            _validate_candidate_value(nested_value, key=nested_key)
        return
    if isinstance(value, list):
        for nested_value in value:
            _validate_candidate_value(nested_value)
        return
    if isinstance(value, str) and _SENSITIVE_TEXT.search(value):
        raise ValueError("Candidate data contains prohibited sensitive content")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Candidate data must be JSON-compatible") from error


def _validate_serialized_size(value: dict[str, object]) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Candidate data must be JSON-compatible") from error
    if len(encoded) > MAX_CANDIDATE_BYTES:
        raise ValueError("ActionCandidate cannot exceed 64 KiB")


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _safe_string(value: str, field_name: str) -> str:
    normalized = _non_empty(value, field_name)
    if len(normalized) > 1024:
        raise ValueError(f"{field_name} cannot exceed 1024 characters")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{field_name} must be a single-line value")
    if _SENSITIVE_TEXT.search(normalized):
        raise ValueError(f"{field_name} contains prohibited sensitive content")
    return normalized


def _non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized
