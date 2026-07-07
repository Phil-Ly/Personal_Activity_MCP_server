"""Register Journal MCP Tools."""

from datetime import date

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.journal import (
    JournalListResult,
    JournalRepository,
    JournalSearchResult,
)
from personal_activity_mcp.sidecar import SidecarRepository


def register_journal_tools(
    server: FastMCP,
    repository: JournalRepository,
    sidecar: SidecarRepository | None = None,
) -> None:
    """Register Journal Tools on the provided server."""

    @server.tool(
        name="journal.list_entries",
        description=(
            "List journal entry metadata in an inclusive date range without returning bodies."
        ),
        structured_output=True,
    )
    def list_journal_entries(
        start_date: date,
        end_date: date,
        source_ids: list[str] | None = None,
        extensions: list[str] | None = None,
        include_frontmatter: bool = False,
    ) -> JournalListResult:
        result = repository.list_entries(
            start_date=start_date,
            end_date=end_date,
            source_ids=source_ids,
            extensions=extensions,
            include_frontmatter=include_frontmatter,
        )
        if sidecar is not None:
            for entry in result.entries:
                sidecar.upsert_journal_entry(entry)
        return result

    @server.tool(
        name="journal.search_entries",
        description=("Search authorized journal entries by keyword without returning full bodies."),
        structured_output=True,
    )
    def search_journal_entries(
        query: str,
        start_date: date,
        end_date: date,
        source_ids: list[str] | None = None,
        limit: int = 20,
        include_snippets: bool = False,
    ) -> JournalSearchResult:
        return repository.search_entries(
            query=query,
            start_date=start_date,
            end_date=end_date,
            source_ids=source_ids,
            limit=limit,
            include_snippets=include_snippets,
        )
