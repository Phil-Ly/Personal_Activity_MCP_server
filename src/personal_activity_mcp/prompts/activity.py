"""Provider-neutral review-summary MCP Prompt."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.time_policy import require_aware_datetime


def register_review_prompt(server: FastMCP) -> None:
    """Register the single flexible review-summary template."""

    @server.prompt(
        name="activity.review_summary",
        title="Activity review summary",
        description="Guide a review summary for any user-defined time range.",
    )
    def review_summary(
        period_start: datetime,
        period_end: datetime,
        focus: str | None = None,
    ) -> str:
        require_aware_datetime(period_start, "period_start")
        require_aware_datetime(period_end, "period_end")
        if period_start >= period_end:
            raise ValueError("period_start must be before period_end")
        focus_line = (
            f"Give special attention to: {focus.strip()}."
            if focus is not None and focus.strip()
            else "Use no additional focus beyond the user's current request."
        )
        return f"""
Prepare an activity review summary for {period_start.isoformat()} through
{period_end.isoformat()}.

{focus_line}

Use the available subset of:
- the user's current explicit statements, corrections, goals, and constraints;
- Agent-provided local activity records read with the Agent's own file capabilities;
- Calendar Evidence already returned by calendar.list_events;
- Reminder Evidence already returned by reminders.list_reminders.

The Prompt remains useful with user context alone. Do not require Calendar, Reminder,
or file data to be present.

Treat source records as evidence, not automatically confirmed business facts. Past Calendar
events show that something was scheduled; past Calendar events do not prove completion.
Completed Reminders may support completion but do not override the user's current
corrections. Clearly distinguish confirmed facts, reasonable inferences, unconfirmed
items, and future plans. Preserve material contradictions instead of silently resolving
them.

Organize the final prose around the user's purpose and the strongest supported facts.
The Agent decides importance, personal priority, whether clarification is needed, and
the final natural-language wording. Do not call Tools or ask the user questions. Do not
create domain objects or persist the generated summary from this Prompt.
""".strip()
