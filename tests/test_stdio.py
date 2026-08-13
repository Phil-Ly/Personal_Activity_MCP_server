import sys
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
            command=sys.executable,
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
                    "activity.review_summary",
                    {
                        "period_start": "2026-07-26T00:00:00+08:00",
                        "period_end": "2026-07-27T00:00:00+08:00",
                    },
                )

        assert [tool.name for tool in tools.tools] == [
            "calendar.list_calendars",
            "calendar.create_calendar",
            "calendar.update_calendar",
            "calendar.list_events",
            "calendar.create_event",
            "calendar.update_event",
            "reminders.list_lists",
            "reminders.create_list",
            "reminders.update_list",
            "reminders.list_reminders",
            "reminders.create_reminder",
            "reminders.complete_reminder",
        ]
        assert [prompt.name for prompt in prompts.prompts] == ["activity.review_summary"]
        assert templates.resourceTemplates == []
        assert "past Calendar events" in prompt.messages[0].content.text  # type: ignore[union-attr]
        stderr = stderr_path.read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" not in stderr
        assert str(config_path) not in stderr
        assert str(sidecar_path) not in stderr

    anyio.run(exercise_server)
