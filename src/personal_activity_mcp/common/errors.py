"""Safe conversion from domain failures to structured MCP errors."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from mcp import types
from pydantic import BaseModel

from personal_activity_mcp.common.models import ToolFailure, ToolOutcome

T = TypeVar("T", bound=BaseModel)


class ToolContractError(Exception):
    """Internal typed error with a deliberately safe public representation."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        public_message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.public_message = public_message
        self.details = details


def error_result(error: Exception) -> types.CallToolResult:
    """Return an MCP error result without exposing the original exception text."""
    failure = _public_failure(error)
    structured = failure.model_dump(exclude_none=True)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"{failure.code}: {failure.message}",
            )
        ],
        structuredContent=structured,
        isError=True,
    )


def call_safely(operation: Callable[[], T]) -> ToolOutcome[T] | types.CallToolResult:
    """Execute one Tool operation and convert failures at the MCP boundary."""
    try:
        return ToolOutcome(root=operation())
    except Exception as error:
        return error_result(error)


def _public_failure(error: Exception) -> ToolFailure:
    if isinstance(error, ToolContractError):
        return ToolFailure(
            code=error.code,
            message=error.public_message,
            retryable=error.retryable,
            details=error.details,
        )

    message = str(error)
    if isinstance(error, ValueError):
        if message.startswith(("Unknown calendar_ids:", "Unknown reminder list_ids:")):
            return ToolFailure(
                code="SOURCE_NOT_AUTHORIZED",
                message="Requested source is not authorized",
                retryable=False,
            )
        if "not allowed for writes" in message or "not in a write-enabled list" in message:
            return ToolFailure(
                code="TARGET_READ_ONLY",
                message="Requested target is not write-enabled",
                retryable=False,
            )
        if "idempotency" in message and "conflict" in message:
            return ToolFailure(
                code="IDEMPOTENCY_CONFLICT",
                message="Idempotency key conflicts with another request",
                retryable=False,
            )
        return ToolFailure(
            code="INVALID_ARGUMENT",
            message="Tool arguments are invalid",
            retryable=False,
        )

    if error.__class__.__name__.endswith("BackendError"):
        return ToolFailure(
            code="BACKEND_FAILURE",
            message="External application request failed",
            retryable=True,
        )

    return ToolFailure(
        code="INTERNAL_ERROR",
        message="The Tool could not complete the request",
        retryable=False,
    )
