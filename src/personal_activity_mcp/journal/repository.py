"""Discover and read entries inside explicitly authorized journal directories."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from personal_activity_mcp.config import AppConfig, JournalSource
from personal_activity_mcp.journal.models import (
    JournalEntryEvidence,
    JournalListResult,
    JournalResource,
    JournalSearchEvidence,
    JournalSearchResult,
    TimeRange,
)

_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_ENTRY_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class JournalResourceError(ValueError):
    """Raised when a journal Resource URI cannot be safely resolved."""


class JournalRepository:
    """Read-only access to configured journal sources."""

    def __init__(self, config: AppConfig) -> None:
        self._sources = {source.source_id: source for source in config.journal_sources}

    def list_entries(
        self,
        *,
        start_date: date,
        end_date: date,
        source_ids: list[str] | None = None,
        extensions: list[str] | None = None,
        include_frontmatter: bool = False,
    ) -> JournalListResult:
        """List journal metadata in an inclusive date range."""
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")

        sources = self._select_sources(source_ids)
        entries: list[JournalEntryEvidence] = []
        warnings: list[str] = []
        for source in sources:
            allowed_extensions = _select_extensions(source, extensions)
            for path in sorted(source.path.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in allowed_extensions:
                    continue
                if not _is_within(path, source.path):
                    continue
                relative_path = path.relative_to(source.path).as_posix()
                try:
                    content = path.read_bytes()
                    frontmatter = _parse_frontmatter(content)
                except (OSError, UnicodeError, yaml.YAMLError):
                    warnings.append(
                        f"Unable to read journal entry: {source.source_id}/{relative_path}"
                    )
                    continue

                entry_date = _entry_date(frontmatter, path.name)
                if entry_date is None:
                    warnings.append(
                        f"Unable to determine journal date: {source.source_id}/{relative_path}"
                    )
                    continue
                if not start_date <= entry_date <= end_date:
                    continue

                entries.append(
                    _build_evidence(
                        source=source,
                        path=path,
                        relative_path=relative_path,
                        content=content,
                        frontmatter=frontmatter,
                        entry_date=entry_date,
                        include_frontmatter=include_frontmatter,
                    )
                )

        entries.sort(key=lambda entry: (entry.date, entry.source_id, entry.path))
        return JournalListResult(entries=entries, warnings=warnings)

    def search_entries(
        self,
        *,
        query: str,
        start_date: date,
        end_date: date,
        source_ids: list[str] | None = None,
        limit: int = 20,
        include_snippets: bool = False,
    ) -> JournalSearchResult:
        """Search authorized journal files using case-insensitive keywords."""
        terms = _query_terms(query)
        if not terms:
            raise ValueError("query must contain at least one keyword")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        entries: list[JournalSearchEvidence] = []
        warnings: list[str] = []
        for source in self._select_sources(source_ids):
            for path in sorted(source.path.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in source.extensions:
                    continue
                if not _is_within(path, source.path):
                    continue
                relative_path = path.relative_to(source.path).as_posix()
                try:
                    content = path.read_bytes()
                    text = content.decode("utf-8")
                    frontmatter = _parse_frontmatter(content)
                except (OSError, UnicodeError, yaml.YAMLError):
                    warnings.append(
                        f"Unable to read journal entry: {source.source_id}/{relative_path}"
                    )
                    continue

                entry_date = _entry_date(frontmatter, path.name)
                if entry_date is None:
                    warnings.append(
                        f"Unable to determine journal date: {source.source_id}/{relative_path}"
                    )
                    continue
                if not start_date <= entry_date <= end_date:
                    continue

                folded_text = text.casefold()
                matched_terms = [term for term, folded in terms if folded in folded_text]
                if not matched_terms:
                    continue
                entries.append(
                    _build_search_evidence(
                        source=source,
                        path=path,
                        relative_path=relative_path,
                        content=content,
                        text=text,
                        frontmatter=frontmatter,
                        entry_date=entry_date,
                        matched_terms=matched_terms,
                        folded_terms=[folded for _, folded in terms if folded in folded_text],
                        include_snippets=include_snippets,
                    )
                )

        entries.sort(
            key=lambda entry: (
                -len(entry.matched_terms),
                -entry.date.toordinal(),
                entry.source_id,
                entry.path,
            )
        )
        return JournalSearchResult(entries=entries[:limit], warnings=warnings)

    def read_entry(self, resource_uri: str) -> JournalResource:
        """Read the latest content addressed by a journal Resource URI."""
        parsed = urlparse(resource_uri)
        entry_id = parsed.path.removeprefix("/")
        if (
            parsed.scheme != "journal"
            or not parsed.netloc
            or not _ENTRY_ID_PATTERN.fullmatch(entry_id)
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise JournalResourceError("Invalid journal resource URI")

        source = self._sources.get(parsed.netloc)
        if source is None:
            raise JournalResourceError(f"Unknown journal source_id: {parsed.netloc}")

        path = self._find_entry_path(source, entry_id)
        if path is None:
            raise JournalResourceError("Journal entry not found")
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content.encode("utf-8"))
            stat = path.stat()
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise JournalResourceError("Unable to read journal entry") from error

        title = frontmatter.get("title")
        if not isinstance(title, str) or not title.strip():
            title = path.stem
        mime_type = "text/markdown" if path.suffix.lower() == ".md" else "text/plain"
        return JournalResource(
            resource_uri=resource_uri,
            entry_id=entry_id,
            title=title.strip(),
            mime_type=mime_type,
            last_modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            content=content,
        )

    def _select_sources(self, source_ids: list[str] | None) -> list[JournalSource]:
        if source_ids is None:
            return list(self._sources.values())
        unknown = sorted(set(source_ids) - self._sources.keys())
        if unknown:
            raise ValueError(f"Unknown journal source_ids: {', '.join(unknown)}")
        return [self._sources[source_id] for source_id in source_ids]

    @staticmethod
    def _find_entry_path(source: JournalSource, entry_id: str) -> Path | None:
        for path in source.path.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in source.extensions
                and _is_within(path, source.path)
                and _stable_entry_id(source.source_id, path.relative_to(source.path).as_posix())
                == entry_id
            ):
                return path
        return None


def _select_extensions(source: JournalSource, requested: list[str] | None) -> set[str]:
    allowed = set(source.extensions)
    if requested is None:
        return allowed
    return allowed.intersection(extension.lower() for extension in requested)


def _parse_frontmatter(content: bytes) -> dict[str, Any]:
    text = content.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration:
        return {}
    parsed = yaml.safe_load("\n".join(lines[1:closing_index]))
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise yaml.YAMLError("Journal frontmatter must be a mapping")
    return parsed


def _entry_date(frontmatter: dict[str, Any], filename: str) -> date | None:
    frontmatter_date = frontmatter.get("date")
    if isinstance(frontmatter_date, datetime):
        return frontmatter_date.date()
    if isinstance(frontmatter_date, date):
        return frontmatter_date
    if isinstance(frontmatter_date, str):
        try:
            return date.fromisoformat(frontmatter_date)
        except ValueError:
            pass

    match = _DATE_PATTERN.search(filename)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _build_evidence(
    *,
    source: JournalSource,
    path: Path,
    relative_path: str,
    content: bytes,
    frontmatter: dict[str, Any],
    entry_date: date,
    include_frontmatter: bool,
) -> JournalEntryEvidence:
    entry_id = _stable_entry_id(source.source_id, relative_path)
    stat = path.stat()
    created_timestamp = getattr(stat, "st_birthtime", stat.st_ctime)
    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        title = path.stem

    return JournalEntryEvidence(
        evidence_id=f"journal:{entry_id}",
        source_id=source.source_id,
        time_range=TimeRange(start=entry_date, end=entry_date),
        title=title.strip(),
        metadata={"file_type": path.suffix.lower()},
        entry_id=entry_id,
        date=entry_date,
        path=relative_path,
        created_at=datetime.fromtimestamp(created_timestamp, UTC),
        modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        frontmatter=frontmatter if include_frontmatter else {},
        content_hash=hashlib.sha256(content).hexdigest(),
        resource_uri=f"journal://{source.source_id}/{entry_id}",
    )


def _build_search_evidence(
    *,
    source: JournalSource,
    path: Path,
    relative_path: str,
    content: bytes,
    text: str,
    frontmatter: dict[str, Any],
    entry_date: date,
    matched_terms: list[str],
    folded_terms: list[str],
    include_snippets: bool,
) -> JournalSearchEvidence:
    entry_id = _stable_entry_id(source.source_id, relative_path)
    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        title = path.stem
    return JournalSearchEvidence(
        evidence_id=f"journal:{entry_id}",
        source_id=source.source_id,
        time_range=TimeRange(start=entry_date, end=entry_date),
        title=title.strip(),
        metadata={"file_type": path.suffix.lower()},
        entry_id=entry_id,
        date=entry_date,
        path=relative_path,
        matched_terms=matched_terms,
        snippets=_make_snippets(text, folded_terms) if include_snippets else [],
        content_hash=hashlib.sha256(content).hexdigest(),
        resource_uri=f"journal://{source.source_id}/{entry_id}",
    )


def _query_terms(query: str) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term in query.split():
        folded = term.casefold()
        if folded and folded not in seen:
            seen.add(folded)
            terms.append((term, folded))
    return terms


def _make_snippets(text: str, folded_terms: list[str]) -> list[str]:
    folded_text = text.casefold()
    positions: list[int] = []
    for term in folded_terms:
        start = 0
        while len(positions) < 100:
            position = folded_text.find(term, start)
            if position < 0:
                break
            positions.append(position)
            start = position + max(1, len(term))

    snippets: list[str] = []
    for position in sorted(positions):
        snippet_start = max(0, position - 118)
        snippet_end = min(len(text), snippet_start + 240)
        if snippet_end == len(text):
            snippet_start = max(0, snippet_end - 240)
        snippet = text[snippet_start:snippet_end].strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet)
        if len(snippets) == 3:
            break
    return snippets


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _stable_entry_id(source_id: str, relative_path: str) -> str:
    return hashlib.sha256(f"{source_id}\0{relative_path}".encode()).hexdigest()[:32]
