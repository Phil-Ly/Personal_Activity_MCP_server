"""Activity workflow MCP Prompts."""

from mcp.server.fastmcp import FastMCP


def register_activity_prompts(server: FastMCP) -> None:
    """Register activity-analysis and write-planning prompt templates."""

    @server.prompt(
        name="activity.daily_review",
        title="Daily activity review",
        description="Analyze one day of authorized activity evidence and produce claims.",
    )
    def daily_review(date: str) -> str:
        return f"""
Daily activity review for {date}.

Core rule: treat Journal, Calendar, and Reminders records as evidence, not facts.
Use only data returned by this MCP server: Journal Tools/Resources, Calendar Tools,
and Reminders Tools. Do not request chat logs, location history, mail, browser data,
or any source outside v1.0 scope.

Inputs you should use:
- Journal evidence from journal.list_entries or journal.search_entries.
- Journal bodies only when explicitly read through journal:// Resources.
- Reminder evidence from reminders.list_reminders.
- Calendar event evidence from calendar.list_events.

Reasoning rules:
- Identify completed actions, planned tasks, scheduled events, and uncertain claims.
- do not treat past Calendar events as completed actions by default.
- Completed Reminders can support completed_action claims.
- Journal text can support completed_action, planned_task, scheduled_event, or uncertain claims.
- Mark weak, vague, or inferred items with needs_user_confirmation=true.
- Do not call write tools from this prompt.

Every activity claim must include:
- claim_id
- claim_text
- claim_type: completed_action | planned_task | scheduled_event | uncertain
- time_range
- status_semantics: confirmed | probable | planned
- source_evidence_ids
- confidence
- suggested_target: calendar | reminders | activity_log | none
- needs_user_confirmation

Output these top-level fields:
- completed_actions
- planned_tasks
- scheduled_events
- uncertain_claims
""".strip()

    @server.prompt(
        name="activity.weekly_missing_review",
        title="Weekly missing activity review",
        description="Find completed actions that may be missing from Activity Log.",
    )
    def weekly_missing_review(week_start: str, week_end: str) -> str:
        return f"""
Weekly missing activity review for {week_start} through {week_end}.

Core rule: treat all inputs as evidence. This prompt identifies candidates only;
do not call write tools and do not create Calendar or Reminder objects.

Inputs you should use:
- Journal evidence and journal:// Resource bodies for the week.
- Completed Reminders for the week.
- Activity Log events from calendar.list_events.

Workflow:
- Identify high-value completed actions from Journal evidence.
- Compare candidates with Activity Log events.
- Cross-check with completed Reminders.
- Merge duplicate descriptions.
- Do not analyze chat records, maps, location history, or other external sources.
- Do not mark vague Journal text as confirmed.
- Generate user-confirmation candidates before any write plan.

Output these top-level fields:
- missing_completed_actions
- already_recorded_actions
- possible_duplicates
- uncertain_claims
- recommended_questions
""".strip()

    @server.prompt(
        name="activity.future_plan",
        title="Future plan extraction",
        description="Extract future Calendar and Reminder candidates without writing them.",
    )
    def future_plan(planning_horizon: str) -> str:
        return f"""
Future plan extraction for {planning_horizon}.

Core rule: produce candidates only; do not call write tools.
Use only Journal evidence/resources, future Calendar events, and current incomplete Reminders.

Workflow:
- Identify future plans from Journal evidence or planning notes.
- Deduplicate against existing Calendar events and Reminders.
- Classify each item by time semantics.
- Items with an exact future time range become calendar_event_candidates.
- Items without an exact time range but requiring completion become reminder_candidates.
- Vague ideas, missing titles, missing action verbs, or unclear timing become
  uncertain_future_items.
- Do not write vague ideas into Calendar.

Output these top-level fields:
- calendar_event_candidates
- reminder_candidates
- uncertain_future_items
- duplicates
- questions_for_user
""".strip()

    @server.prompt(
        name="activity.confirm_uncertain_claims",
        title="Uncertain claim confirmation",
        description="Turn valuable uncertain claims into concise user confirmation questions.",
    )
    def confirm_uncertain_claims(confirmation_strategy: str) -> str:
        return f"""
Uncertain claim confirmation using strategy: {confirmation_strategy}.

Core rule: do not write anything from this prompt. User confirmation must happen before
any write-plan prompt or write tool call.

Workflow:
- Filter out low-value fragments.
- Keep only high-value uncertain claims with insufficient evidence.
- Ask short multiple-choice questions.
- do not ask open-ended long questions.
- Each question must point to one or more claim_id values.
- Provide recommended_options and a default_action such as "do_not_record" or "defer".
- If the user chooses not to record an item, lower the priority of similar future questions.

Output these top-level fields:
- questions
- claim_ids
- recommended_options
- default_action
- updated_claim_status
""".strip()

    @server.prompt(
        name="activity.write_plan",
        title="Activity write plan",
        description="Map confirmed claims to MCP write tools without executing them.",
    )
    def write_plan(idempotency_namespace: str) -> str:
        return f"""
Activity write plan using idempotency namespace: {idempotency_namespace}.

Core rule: this prompt creates a plan only. It must not execute tools.
MCP tools still enforce permissions, idempotency, and dangerous-operation checks.

Mapping rules:
- completed_action + confirmed -> activity.record_completed_action
- planned_task without an exact time range -> reminders.create_reminder
- scheduled_event with an exact future time range -> calendar.create_event
- uncertain claims must not enter write_plan.
- Duplicate items must not create new objects.
- Sensitive items or items whose default action is "do_not_record" must not enter write_plan.

For every write_plan item include:
- claim_id
- tool_name
- tool_input_draft
- idempotency_key, prefixed with "{idempotency_namespace}:"
- provenance_ids
- confirmed_by_user
- requires_user_confirmation

If a write would modify a confirmed past action or otherwise be dangerous, mark it as
requires_user_confirmation=true and do not execute it until separately confirmed.
""".strip()
