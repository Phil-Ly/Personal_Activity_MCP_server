"""Stable bounded pagination with opaque cursors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

DEFAULT_LIMIT = 100
MAX_LIMIT = 200
_CURSOR_PERSON = b"pamcp-v1"
_DIGEST_SIZE = 8


def validate_limit(limit: int) -> int:
    """Validate the public page-size contract."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 200")
    return limit


def encode_cursor(sort_key: tuple[str, ...]) -> str:
    """Encode a stable sort key as a tamper-evident opaque token."""
    if not sort_key or not all(isinstance(value, str) for value in sort_key):
        raise ValueError("cursor sort key must contain strings")
    payload = json.dumps(sort_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.blake2s(
        payload,
        digest_size=_DIGEST_SIZE,
        person=_CURSOR_PERSON,
    ).digest()
    return base64.urlsafe_b64encode(payload + digest).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, ...]:
    """Decode and validate an opaque cursor."""
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = decoded[:-_DIGEST_SIZE]
        supplied_digest = decoded[-_DIGEST_SIZE:]
        expected_digest = hashlib.blake2s(
            payload,
            digest_size=_DIGEST_SIZE,
            person=_CURSOR_PERSON,
        ).digest()
        if len(decoded) <= _DIGEST_SIZE or not hmac.compare_digest(
            supplied_digest,
            expected_digest,
        ):
            raise ValueError
        values = json.loads(payload.decode("utf-8"))
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) for value in values)
        ):
            raise ValueError
        return tuple(values)
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as error:
        raise ValueError("cursor is invalid") from error


def paginate(
    items: Sequence[T],
    *,
    key: Callable[[T], tuple[str, ...]],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[T], str | None]:
    """Return a stable page from items already sorted by the supplied key."""
    page_size = validate_limit(limit)
    after_key = decode_cursor(cursor) if cursor is not None else None
    eligible = [item for item in items if after_key is None or key(item) > after_key]
    page = eligible[:page_size]
    has_more = len(eligible) > page_size
    next_cursor = encode_cursor(key(page[-1])) if page and has_more else None
    return page, next_cursor
