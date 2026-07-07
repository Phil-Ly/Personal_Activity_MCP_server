from datetime import date
from pathlib import Path

import pytest

from personal_activity_mcp.config import AppConfig, JournalSource
from personal_activity_mcp.journal import JournalRepository


def make_repository(journal_path: Path) -> JournalRepository:
    source = JournalSource("daily", journal_path.resolve(), (".md", ".txt"))
    return JournalRepository(AppConfig((source,)))


def test_search_matches_multiple_terms_case_insensitively(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-03.md").write_text(
        "Today I implemented an MCP server and reviewed EventKit.",
        encoding="utf-8",
    )
    repository = make_repository(journal_path)

    result = repository.search_entries(
        query="mcp SPEC eventkit",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
        include_snippets=True,
    )

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.source_type == "journal"
    assert entry.source_id == "daily"
    assert entry.date == date(2026, 7, 3)
    assert entry.path == "2026-07-03.md"
    assert entry.matched_terms == ["mcp", "eventkit"]
    assert "MCP server" in entry.snippets[0]
    assert entry.resource_uri.startswith("journal://daily/")


def test_search_sorts_by_matched_term_count_then_newest_date(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-01.md").write_text("MCP and EventKit", encoding="utf-8")
    (journal_path / "2026-07-03.md").write_text("MCP only", encoding="utf-8")
    (journal_path / "2026-07-02.md").write_text("MCP only", encoding="utf-8")
    repository = make_repository(journal_path)

    result = repository.search_entries(
        query="MCP EventKit",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
    )

    assert [entry.path for entry in result.entries] == [
        "2026-07-01.md",
        "2026-07-03.md",
        "2026-07-02.md",
    ]


def test_search_limit_and_disabled_snippets_do_not_return_body(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    for day in range(1, 4):
        (journal_path / f"2026-07-0{day}.md").write_text(
            f"secret body {day} MCP",
            encoding="utf-8",
        )
    repository = make_repository(journal_path)

    result = repository.search_entries(
        query="MCP",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
        limit=2,
        include_snippets=False,
    )

    assert [entry.path for entry in result.entries] == ["2026-07-03.md", "2026-07-02.md"]
    assert all(entry.snippets == [] for entry in result.entries)
    assert "secret body" not in result.model_dump_json()


def test_search_returns_at_most_three_short_snippets(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    content = "".join("x" * 300 + f" MCP marker-{index} " for index in range(5))
    (journal_path / "2026-07-03.md").write_text(content, encoding="utf-8")
    repository = make_repository(journal_path)

    result = repository.search_entries(
        query="MCP",
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 3),
        include_snippets=True,
    )

    snippets = result.entries[0].snippets
    assert len(snippets) == 3
    assert all(len(snippet) <= 240 for snippet in snippets)
    assert all("MCP" in snippet for snippet in snippets)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": "   "}, "query must contain at least one keyword"),
        ({"query": "MCP", "limit": 0}, "limit must be between 1 and 100"),
        ({"query": "MCP", "limit": 101}, "limit must be between 1 and 100"),
        (
            {"query": "MCP", "start_date": date(2026, 7, 4)},
            "start_date must be on or before end_date",
        ),
    ],
)
def test_search_rejects_invalid_inputs(
    tmp_path: Path, arguments: dict[str, object], message: str
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    repository = make_repository(journal_path)
    search_arguments: dict[str, object] = {
        "query": "MCP",
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 3),
    }
    search_arguments.update(arguments)

    with pytest.raises(ValueError, match=message):
        repository.search_entries(**search_arguments)  # type: ignore[arg-type]


def test_search_does_not_follow_symlinks_outside_authorized_root(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    secret = tmp_path / "2026-07-03.md"
    secret.write_text("MCP outside", encoding="utf-8")
    (journal_path / "2026-07-03.md").symlink_to(secret)
    repository = make_repository(journal_path)

    result = repository.search_entries(
        query="MCP",
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 3),
    )

    assert result.entries == []


def test_search_filters_by_source_and_date_range(tmp_path: Path) -> None:
    daily_path = tmp_path / "daily"
    work_path = tmp_path / "work"
    daily_path.mkdir()
    work_path.mkdir()
    (daily_path / "2026-07-02.md").write_text("MCP daily", encoding="utf-8")
    (daily_path / "2026-07-08.md").write_text("MCP outside range", encoding="utf-8")
    (work_path / "2026-07-03.md").write_text("MCP work", encoding="utf-8")
    config = AppConfig(
        (
            JournalSource("daily", daily_path.resolve(), (".md",)),
            JournalSource("work", work_path.resolve(), (".md",)),
        )
    )
    repository = JournalRepository(config)

    result = repository.search_entries(
        query="MCP",
        source_ids=["daily"],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 7),
    )

    assert [(entry.source_id, entry.path) for entry in result.entries] == [
        ("daily", "2026-07-02.md")
    ]


def test_search_rejects_unknown_source_id(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    repository = make_repository(journal_path)

    with pytest.raises(ValueError, match="Unknown journal source_ids: private"):
        repository.search_entries(
            query="MCP",
            source_ids=["private"],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 7),
        )
