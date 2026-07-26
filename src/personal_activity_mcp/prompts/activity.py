"""Provider-neutral activity workflow MCP Prompts."""

from mcp.server.fastmcp import FastMCP

_ACTION_CANDIDATE_CONTRACT = """
Return a JSON-compatible object with exactly these top-level fields:
- prompt_contract_version: "activity-workflow/2"
- action_candidates: a list

Every ActionCandidate contains:
- candidate_id
- action_type: record_activity | create_event | update_event | create_task |
  complete_task | none
- title
- time: event start/end, task due_date, or null
- source_refs: opaque references to supporting inputs; use [] when there are none
- decision: pending | confirmed | rejected
- issues: a list of {code, message, related_item_ids?}
- route is optional; when present it may contain open-string provider and/or tool_name

Use only these issue codes: source_conflict, time_overlap, possible_duplicate,
protected_item, missing_information, routing_unavailable.

The contract is provider-neutral. Do not invent a provider or tool when route is absent.
Source references are identifiers only: do not include source bodies, hidden reasoning,
or sensitive content in source_refs.
""".strip()


def register_activity_prompts(server: FastMCP) -> None:
    """Register the three ActionCandidate workflow templates."""

    @server.prompt(
        name="activity.daily_review",
        title="Daily activity review",
        description="Organize one day of activity context into ActionCandidates.",
    )
    def daily_review(date: str) -> str:
        return f"""
Daily activity review for {date}.

Inputs may already exist in the Agent context:
- the user's current explicit statements, goals, constraints, and corrections;
- Agent-provided local activity records read with the Agent's own file capabilities;
- Calendar evidence from calendar.list_events;
- Reminder evidence from reminders.list_reminders.

Treat every source record as evidence rather than an automatically confirmed fact.
The user's current explicit statement has the highest authority for current intent, but
it does not bypass Tool authorization, confirmation, time, or idempotency checks.
Completed Reminders can support a completed action. Past Calendar events do not prove
completion. Preserve contradictions, possible duplicates, time conflicts, protected
items, and missing facts in the corresponding candidate's issues.

Create provider-neutral ActionCandidates for actions worth recording or scheduling.
Items without explicit user confirmation stay pending. Importance and priority are
Agent decisions based on the current task and user context.

This Prompt does not read files, ask the user questions, update decision state, choose a
route, call read tools, or call write tools. The Agent owns those workflow steps.

{_ACTION_CANDIDATE_CONTRACT}
""".strip()

    @server.prompt(
        name="activity.weekly_missing_review",
        title="Weekly missing activity review",
        description="Find potentially missing completed activities as ActionCandidates.",
    )
    def weekly_missing_review(week_start: str, week_end: str) -> str:
        return f"""
Weekly missing activity review for {week_start} through {week_end}.

Use inputs already present in the Agent context:
- Agent-provided local activity records for the period;
- completed Reminders for the period;
- Activity Log events returned by calendar.list_events.

Compare candidate completed actions with Activity Log events and completed Reminders.
Merge duplicate descriptions. Do not emit an ActionCandidate for an item already recorded
when no action remains. A potentially missing completed action uses
action_type=record_activity. Put possible duplicates, source conflicts, protected items,
and missing facts into issues, and keep the candidate pending until the Agent obtains any
needed user confirmation.

This Prompt identifies candidates only: do not call write tools, ask confirmation
questions, or choose the destination Tool. Importance and priority are Agent decisions.

{_ACTION_CANDIDATE_CONTRACT}
""".strip()

    @server.prompt(
        name="activity.future_plan",
        title="Future plan extraction",
        description="Organize future context into provider-neutral ActionCandidates.",
    )
    def future_plan(planning_horizon: str) -> str:
        return f"""
Future plan extraction for {planning_horizon}.

Use the user's explicit future plans and constraints, Agent-provided local activity
records, future Calendar evidence, and current incomplete Reminder evidence already
present in the Agent context. Deduplicate against existing Calendar and Reminder items.
Do not silently overwrite an existing external item when the user's current intent
differs; record the conflict in issues.

Use action_type=create_event for an item with an exact future time range.
Use action_type=create_task for an item that requires completion without an exact time
range. Use action_type=none with decision=pending for a vague idea or an item whose
business action cannot yet be determined. The output remains provider-neutral, and
route is optional so the Agent can select this Server or another external Tool later.

This Prompt produces candidates only: do not call write tools, ask the user questions,
decide personal priority, or claim that service-side validation has passed.

{_ACTION_CANDIDATE_CONTRACT}
""".strip()
