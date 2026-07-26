"""Provider-neutral models shared across MCP capabilities."""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, RootModel, field_validator

T = TypeVar("T", bound=BaseModel)


class TargetRef(BaseModel):
    """Stable provider-neutral reference to one external target."""

    resource_type: Literal["calendar_event", "reminder"]
    item_id: str
    container_id: str | None = None

    @field_validator("item_id", "container_id")
    @classmethod
    def validate_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("target identity fields must be non-empty")
        return normalized


class ToolWarning(BaseModel):
    """Non-fatal deterministic issue returned beside Tool data."""

    code: Literal["DUPLICATE_SOURCE_ITEM"]
    message: str
    related_item_ids: list[str] = Field(default_factory=list)


class ToolFailure(BaseModel):
    """Structured public error contract for all MCP Tools."""

    code: str
    message: str
    retryable: bool
    details: dict[str, object] | None = None


class ToolOutcome(RootModel[T | ToolFailure], Generic[T]):
    """A success model or the shared structured failure at the Tool boundary."""
