from pathlib import Path

import anyio

from personal_activity_mcp.server import create_server


def write_config(config_path: Path, journal_path: Path) -> None:
    sidecar_path = config_path.parent / "sidecar.sqlite3"
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
extensions = [".md", ".txt"]
""".strip(),
        encoding="utf-8",
    )


def _prompt_text(result) -> str:
    return result.messages[0].content.text


def test_server_exposes_activity_workflow_prompts(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    prompts = anyio.run(server.list_prompts)

    assert [prompt.name for prompt in prompts] == [
        "activity.daily_review",
        "activity.weekly_missing_review",
        "activity.future_plan",
        "activity.confirm_uncertain_claims",
        "activity.write_plan",
    ]
    prompt_arguments = {
        prompt.name: [arg.name for arg in prompt.arguments or []] for prompt in prompts
    }
    assert prompt_arguments["activity.daily_review"] == ["date"]
    assert prompt_arguments["activity.weekly_missing_review"] == ["week_start", "week_end"]
    assert prompt_arguments["activity.future_plan"] == ["planning_horizon"]
    assert prompt_arguments["activity.confirm_uncertain_claims"] == ["confirmation_strategy"]
    assert prompt_arguments["activity.write_plan"] == ["idempotency_namespace"]


def test_daily_review_prompt_enforces_evidence_and_claim_contract(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.daily_review",
        {"date": "2026-07-07"},
    )

    text = _prompt_text(result)
    assert "2026-07-07" in text
    assert "treat Journal, Calendar, and Reminders records as evidence" in text
    assert "do not treat past Calendar events as completed actions by default" in text
    assert "claim_id" in text
    assert "needs_user_confirmation" in text
    assert "completed_actions" in text
    assert "uncertain_claims" in text


def test_weekly_missing_review_prompt_blocks_direct_writes(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.weekly_missing_review",
        {"week_start": "2026-07-01", "week_end": "2026-07-07"},
    )

    text = _prompt_text(result)
    assert "do not call write tools" in text
    assert "missing_completed_actions" in text
    assert "already_recorded_actions" in text
    assert "Activity Log events" in text
    assert "recommended_questions" in text


def test_future_plan_prompt_routes_candidates_without_writing(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.future_plan",
        {"planning_horizon": "2026-07-08..2026-07-14"},
    )

    text = _prompt_text(result)
    assert "calendar_event_candidates" in text
    assert "reminder_candidates" in text
    assert "uncertain_future_items" in text
    assert "do not call write tools" in text
    assert "exact future time range" in text


def test_confirmation_prompt_uses_short_choice_questions(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.confirm_uncertain_claims",
        {"confirmation_strategy": "high_value_only"},
    )

    text = _prompt_text(result)
    assert "high_value_only" in text
    assert "short multiple-choice questions" in text
    assert "do not ask open-ended long questions" in text
    assert "default_action" in text
    assert "do not write anything" in text


def test_write_plan_prompt_maps_confirmed_claims_to_tools(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.write_plan",
        {"idempotency_namespace": "demo"},
    )

    text = _prompt_text(result)
    assert "completed_action + confirmed -> activity.record_completed_action" in text
    assert "planned_task without an exact time range -> reminders.create_reminder" in text
    assert "scheduled_event with an exact future time range -> calendar.create_event" in text
    assert "uncertain claims must not enter write_plan" in text
    assert "idempotency_key" in text
    assert "confirmed_by_user" in text
