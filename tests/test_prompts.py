from pathlib import Path

import anyio
import pytest

from personal_activity_mcp.server import create_server


def write_config(config_path: Path) -> None:
    sidecar_path = config_path.parent / "sidecar.sqlite3"
    config_path.write_text(f'sidecar_path = "{sidecar_path}"', encoding="utf-8")


def _prompt_text(result) -> str:
    return result.messages[0].content.text


def _folded(text: str) -> str:
    return " ".join(text.casefold().split())


def test_server_exposes_single_custom_period_review_prompt(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    prompts = anyio.run(server.list_prompts)

    assert [prompt.name for prompt in prompts] == ["activity.review_summary"]
    arguments = prompts[0].arguments or []
    assert [argument.name for argument in arguments] == [
        "period_start",
        "period_end",
        "focus",
    ]
    assert [argument.required for argument in arguments] == [True, True, False]


def test_review_summary_prompt_supports_arbitrary_period_and_optional_focus(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.review_summary",
        {
            "period_start": "2026-06-01T00:00:00+08:00",
            "period_end": "2026-07-01T00:00:00+08:00",
            "focus": "项目推进与精力变化",
        },
    )

    text = _prompt_text(result)
    folded_text = _folded(text)
    assert "2026-06-01T00:00:00+08:00" in text
    assert "2026-07-01T00:00:00+08:00" in text
    assert "项目推进与精力变化" in text
    assert "user's current explicit statements" in folded_text
    assert "agent-provided local activity records" in folded_text
    assert "calendar evidence" in folded_text
    assert "reminder evidence" in folded_text
    assert "past calendar events" in folded_text
    assert "do not prove" in folded_text
    assert "confirmed facts" in folded_text
    assert "reasonable inferences" in folded_text
    assert "unconfirmed items" in folded_text
    assert "future plans" in folded_text
    assert "action_candidates" not in folded_text


def test_review_summary_prompt_works_with_only_user_context(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    result = anyio.run(
        server.get_prompt,
        "activity.review_summary",
        {
            "period_start": "2026-07-07T00:00:00+08:00",
            "period_end": "2026-07-08T00:00:00+08:00",
        },
    )

    text = _prompt_text(result)
    folded_text = _folded(text)
    assert "available subset" in folded_text
    assert "user context alone" in folded_text
    assert "do not call tools" in folded_text
    assert "persist the generated summary" in folded_text
    assert "priority" in folded_text
    assert "agent" in folded_text


@pytest.mark.parametrize(
    ("period_start", "period_end"),
    [
        ("2026-07-07T00:00:00", "2026-07-08T00:00:00+08:00"),
        ("2026-07-08T00:00:00+08:00", "2026-07-07T00:00:00+08:00"),
    ],
)
def test_review_summary_prompt_rejects_invalid_time_ranges(
    tmp_path: Path,
    period_start: str,
    period_end: str,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)
    server = create_server(config_path)

    with pytest.raises(ValueError):
        anyio.run(
            server.get_prompt,
            "activity.review_summary",
            {
                "period_start": period_start,
                "period_end": period_end,
            },
        )
