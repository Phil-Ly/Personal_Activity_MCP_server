"""Shared MCP contracts and deterministic validation helpers."""

from personal_activity_mcp.common.errors import (
    ToolContractError,
    call_safely,
    error_result,
)
from personal_activity_mcp.common.models import (
    TargetRef,
    ToolFailure,
    ToolOutcome,
    ToolWarning,
)
from personal_activity_mcp.common.pagination import (
    decode_cursor,
    encode_cursor,
    paginate,
    validate_limit,
)
from personal_activity_mcp.common.validation import (
    normalize_optional_text,
    normalize_source_refs,
)

__all__ = [
    "TargetRef",
    "ToolContractError",
    "ToolFailure",
    "ToolOutcome",
    "ToolWarning",
    "call_safely",
    "decode_cursor",
    "encode_cursor",
    "error_result",
    "normalize_optional_text",
    "normalize_source_refs",
    "paginate",
    "validate_limit",
]
