from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_real_stdio_session_exposes_no_local_file_capability(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    sidecar_path = tmp_path / "sidecar.sqlite3"
    config_path.write_text(f'sidecar_path = "{sidecar_path}"', encoding="utf-8")

    async def exercise_server() -> None:
        stderr_path = tmp_path / "server.stderr"
        parameters = StdioServerParameters(
            command=str(Path.cwd() / ".venv/bin/python"),
            args=[
                "-m",
                "personal_activity_mcp.server",
                "--config",
                str(config_path),
            ],
            cwd=Path.cwd(),
        )
        with stderr_path.open("w", encoding="utf-8") as server_stderr:
            async with (
                stdio_client(parameters, errlog=server_stderr) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                prompts = await session.list_prompts()
                templates = await session.list_resource_templates()
                prompt = await session.get_prompt(
                    "activity.daily_review",
                    {"date": "2026-07-26"},
                )

        assert [tool.name for tool in tools.tools] == [
            "calendar.list_events",
            "calendar.create_event",
            "calendar.update_event",
            "activity.ensure_log_calendar",
            "activity.record_completed_action",
            "reminders.list_reminders",
            "reminders.create_reminder",
            "reminders.complete_reminder",
        ]
        assert [prompt.name for prompt in prompts.prompts] == [
            "activity.daily_review",
            "activity.weekly_missing_review",
            "activity.future_plan",
        ]
        assert templates.resourceTemplates == []
        assert "source_refs" in prompt.messages[0].content.text  # type: ignore[union-attr]
        assert stderr_path.read_text(encoding="utf-8") == ""

    anyio.run(exercise_server)
