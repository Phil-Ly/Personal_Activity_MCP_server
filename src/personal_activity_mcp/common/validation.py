"""Deterministic validation for opaque cross-boundary references."""

from __future__ import annotations

import re

MAX_SOURCE_REFS = 100
MAX_SOURCE_REF_LENGTH = 512
_PROHIBITED_SENSITIVE_PATTERN = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:password|api[_-]?key|access[_-]?token)\s*[=:])",
    re.IGNORECASE,
)


def normalize_source_refs(values: list[str]) -> list[str]:
    """Trim, validate, deduplicate, and stably order opaque source references."""
    if len(values) > MAX_SOURCE_REFS:
        raise ValueError(f"source_refs cannot contain more than {MAX_SOURCE_REFS} values")

    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("source_ref must be a string")
        reference = value.strip()
        if not reference:
            continue
        if "\n" in reference or "\r" in reference:
            raise ValueError("source_ref must be a single-line opaque identifier")
        if len(reference) > MAX_SOURCE_REF_LENGTH:
            raise ValueError(f"source_ref cannot exceed {MAX_SOURCE_REF_LENGTH} characters")
        if _PROHIBITED_SENSITIVE_PATTERN.search(reference):
            raise ValueError("source_ref contains prohibited sensitive content")
        normalized.add(reference)
    return sorted(normalized)
