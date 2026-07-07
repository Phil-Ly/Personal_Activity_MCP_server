from datetime import date
from pathlib import Path

import pytest

from personal_activity_mcp.config import AppConfig, JournalSource
from personal_activity_mcp.journal import JournalRepository, JournalResourceError


def make_repository(journal_path: Path) -> JournalRepository:
    source = JournalSource("daily", journal_path.resolve(), (".md", ".txt"))
    return JournalRepository(AppConfig((source,)))


def test_list_entries_uses_frontmatter_date_and_returns_evidence(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "wrong-date-2026-07-01.md").write_text(
        """---
date: 2026-07-03
title: Project journal
tags: [mcp, local]
---
Private journal body.
""",
        encoding="utf-8",
    )
    repository = make_repository(journal_path)

    result = repository.list_entries(
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 3),
        include_frontmatter=True,
    )

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.source_type == "journal"
    assert entry.source_id == "daily"
    assert entry.date == date(2026, 7, 3)
    assert entry.time_range.start == date(2026, 7, 3)
    assert entry.time_range.end == date(2026, 7, 3)
    assert entry.title == "Project journal"
    assert entry.path == "wrong-date-2026-07-01.md"
    assert entry.frontmatter["tags"] == ["mcp", "local"]
    assert entry.resource_uri.startswith("journal://daily/")
    assert "Private journal body" not in entry.model_dump_json()


def test_list_entries_filters_by_filename_date_and_extension(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-02.md").write_text("included", encoding="utf-8")
    (journal_path / "2026-07-05.txt").write_text("too late", encoding="utf-8")
    (journal_path / "2026-07-02.json").write_text("not authorized", encoding="utf-8")
    repository = make_repository(journal_path)

    result = repository.list_entries(
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
    )

    assert [entry.path for entry in result.entries] == ["2026-07-02.md"]


def test_list_entries_warns_about_undated_files(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "notes.md").write_text("No date", encoding="utf-8")
    repository = make_repository(journal_path)

    result = repository.list_entries(
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
    )

    assert result.entries == []
    assert result.warnings == ["Unable to determine journal date: daily/notes.md"]


def test_entry_id_is_stable_while_content_hash_tracks_changes(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    entry_path = journal_path / "2026-07-02.md"
    entry_path.write_text("first", encoding="utf-8")
    repository = make_repository(journal_path)

    first = repository.list_entries(start_date=date(2026, 7, 2), end_date=date(2026, 7, 2)).entries[
        0
    ]
    entry_path.write_text("second", encoding="utf-8")
    second = repository.list_entries(
        start_date=date(2026, 7, 2), end_date=date(2026, 7, 2)
    ).entries[0]

    assert second.entry_id == first.entry_id
    assert second.evidence_id == first.evidence_id
    assert second.content_hash != first.content_hash


def test_read_entry_returns_the_latest_original_content(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    entry_path = journal_path / "2026-07-02.md"
    entry_path.write_text("first version", encoding="utf-8")
    repository = make_repository(journal_path)
    entry = repository.list_entries(start_date=date(2026, 7, 2), end_date=date(2026, 7, 2)).entries[
        0
    ]
    entry_path.write_text("latest version", encoding="utf-8")

    resource = repository.read_entry(entry.resource_uri)

    assert resource.resource_uri == entry.resource_uri
    assert resource.entry_id == entry.entry_id
    assert resource.title == "2026-07-02"
    assert resource.mime_type == "text/markdown"
    assert resource.content == "latest version"


@pytest.mark.parametrize(
    "resource_uri, message",
    [
        ("journal://unknown/0123456789abcdef0123456789abcdef", "Unknown journal source_id"),
        ("journal://daily/../../secret", "Invalid journal resource URI"),
        ("file://daily/0123456789abcdef0123456789abcdef", "Invalid journal resource URI"),
    ],
)
def test_read_entry_rejects_untrusted_resource_uris(
    tmp_path: Path, resource_uri: str, message: str
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    repository = make_repository(journal_path)

    with pytest.raises(JournalResourceError, match=message):
        repository.read_entry(resource_uri)


def test_symlink_outside_authorized_root_is_not_discovered(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    secret = tmp_path / "2026-07-02.md"
    secret.write_text("outside", encoding="utf-8")
    (journal_path / "2026-07-02.md").symlink_to(secret)
    repository = make_repository(journal_path)

    result = repository.list_entries(start_date=date(2026, 7, 2), end_date=date(2026, 7, 2))

    assert result.entries == []
