"""Structured journal outputs exposed through MCP."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TimeRange(BaseModel):
    """Inclusive date range represented by an evidence item."""

    start: date
    end: date


class JournalEntryEvidence(BaseModel):
    """Metadata for one journal entry, excluding its body."""

    evidence_id: str
    source_type: Literal["journal"] = "journal"
    source_id: str
    time_range: TimeRange
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    entry_id: str
    date: date
    path: str
    created_at: datetime
    modified_at: datetime
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    resource_uri: str


class JournalListResult(BaseModel):
    """Journal discovery result with non-fatal warnings."""

    entries: list[JournalEntryEvidence]
    warnings: list[str]


class JournalSearchEvidence(BaseModel):
    """One keyword search hit without the full journal body."""

    evidence_id: str
    source_type: Literal["journal"] = "journal"
    source_id: str
    time_range: TimeRange
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    entry_id: str
    date: date
    path: str
    matched_terms: list[str]
    snippets: list[str]
    content_hash: str
    resource_uri: str


class JournalSearchResult(BaseModel):
    """Keyword search results with non-fatal scan warnings."""

    entries: list[JournalSearchEvidence]
    warnings: list[str]


class JournalResource(BaseModel):
    """The current original content of one authorized journal entry."""

    resource_uri: str
    entry_id: str
    title: str
    mime_type: Literal["text/markdown", "text/plain"]
    last_modified: datetime
    content: str
