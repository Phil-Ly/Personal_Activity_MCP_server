"""Register Journal MCP Resources."""

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.journal import JournalRepository


def register_journal_resources(server: FastMCP, repository: JournalRepository) -> None:
    """Register Journal Resources on the provided server."""

    @server.resource(
        "journal://{source_id}/{entry_id}",
        name="journal-entry",
        description="Read the latest original content of one authorized journal entry.",
        mime_type="application/json",
    )
    def read_journal_entry(source_id: str, entry_id: str) -> dict[str, object]:
        resource = repository.read_entry(f"journal://{source_id}/{entry_id}")
        return resource.model_dump(mode="json")
