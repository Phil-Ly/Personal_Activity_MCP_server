"""Register ActionCandidate MCP Tools."""

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.candidates import (
    ActionCandidate,
    CandidateCreate,
    CandidateListResult,
    CandidateQuery,
    CandidateRepository,
    CandidateUpdate,
)
from personal_activity_mcp.common import ToolOutcome, call_safely


def register_candidate_tools(
    server: FastMCP,
    repository: CandidateRepository,
) -> None:
    """Register provider-neutral Candidate lifecycle Tools."""

    @server.tool(
        name="candidates.create",
        description="Create a provider-neutral ActionCandidate without calling external Tools.",
        structured_output=True,
    )
    def create_candidate(
        command: CandidateCreate,
    ) -> ToolOutcome[ActionCandidate]:
        return call_safely(lambda: repository.create(command))

    @server.tool(
        name="candidates.get",
        description="Read one authoritative ActionCandidate without side effects.",
        structured_output=True,
    )
    def get_candidate(
        candidate_id: str,
        include_deleted: bool = False,
    ) -> ToolOutcome[ActionCandidate]:
        return call_safely(
            lambda: repository.get(
                candidate_id,
                include_deleted=include_deleted,
            )
        )

    @server.tool(
        name="candidates.list",
        description="List authoritative ActionCandidates with stable bounded pagination.",
        structured_output=True,
    )
    def list_candidates(
        query: CandidateQuery | None = None,
    ) -> ToolOutcome[CandidateListResult]:
        return call_safely(lambda: repository.list_candidates(query or CandidateQuery()))

    @server.tool(
        name="candidates.update",
        description="Update one ActionCandidate using optimistic concurrency control.",
        structured_output=True,
    )
    def update_candidate(
        candidate_id: str,
        command: CandidateUpdate,
    ) -> ToolOutcome[ActionCandidate]:
        return call_safely(lambda: repository.update(candidate_id, command))

    @server.tool(
        name="candidates.delete",
        description="Soft-delete one ActionCandidate using its expected version.",
        structured_output=True,
    )
    def delete_candidate(
        candidate_id: str,
        expected_version: int,
    ) -> ToolOutcome[ActionCandidate]:
        return call_safely(
            lambda: repository.delete(
                candidate_id,
                expected_version=expected_version,
            )
        )
