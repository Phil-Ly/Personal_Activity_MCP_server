from pathlib import Path

import anyio

from personal_activity_mcp.server import create_server


def write_config(config_path: Path) -> None:
    sidecar_path = config_path.parent / "sidecar.sqlite3"
    config_path.write_text(f'sidecar_path = "{sidecar_path}"', encoding="utf-8")


def _prompt_text(result) -> str:
    return result.messages[0].content.text


def test_server_exposes_only_action_candidate_workflow_prompts(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    prompts = anyio.run(server.list_prompts)

    assert [prompt.name for prompt in prompts] == [
        "activity.daily_review",
        "activity.weekly_missing_review",
        "activity.future_plan",
    ]
    prompt_arguments = {
        prompt.name: [arg.name for arg in prompt.arguments or []] for prompt in prompts
    }
    assert prompt_arguments["activity.daily_review"] == ["date"]
    assert prompt_arguments["activity.weekly_missing_review"] == ["week_start", "week_end"]
    assert prompt_arguments["activity.future_plan"] == ["planning_horizon"]


def test_daily_review_prompt_uses_agent_context_and_action_candidate_contract(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.daily_review",
        {"date": "2026-07-07"},
    )

    text = _prompt_text(result)
    folded_text = text.casefold()
    assert "2026-07-07" in text
    assert "activity-workflow/2" in text
    assert "Agent-provided local activity records" in text
    assert "past calendar events" in folded_text
    assert "do not prove" in folded_text
    assert "completion" in folded_text
    assert "action_candidates" in text
    assert "source_refs" in text
    assert "decision: pending | confirmed | rejected" in text
    assert "route is optional" in text


def test_weekly_missing_review_prompt_keeps_confirmation_and_writes_in_agent(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.weekly_missing_review",
        {"week_start": "2026-07-01", "week_end": "2026-07-07"},
    )

    text = _prompt_text(result)
    folded_text = text.casefold()
    assert "activity-workflow/2" in text
    assert "Agent-provided local activity records" in text
    assert "completed Reminders" in text
    assert "Activity Log events" in text
    assert "action_type=record_activity" in text
    assert "do not call write tools" in text
    assert "importance and priority are agent decisions" in folded_text
    assert "action_candidates" in text


def test_future_plan_prompt_produces_provider_neutral_candidates(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.future_plan",
        {"planning_horizon": "2026-07-08..2026-07-14"},
    )

    text = _prompt_text(result)
    assert "activity-workflow/2" in text
    assert "action_type=create_event" in text
    assert "action_type=create_task" in text
    assert "route is optional" in text
    assert "provider-neutral" in text
    assert "do not call write tools" in text
    assert "action_candidates" in text
