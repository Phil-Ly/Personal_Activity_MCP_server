from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_real_stdio_session_lists_and_calls_journal_capabilities(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    (journal_path / "2026-07-03.md").write_text("stdio content", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
extensions = [".md"]
""".strip(),
        encoding="utf-8",
    )

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
                result = await session.call_tool(
                    "journal.list_entries",
                    {"start_date": "2026-07-03", "end_date": "2026-07-03"},
                )
                assert result.structuredContent is not None
                resource_uri = result.structuredContent["entries"][0]["resource_uri"]
                resource = await session.read_resource(resource_uri)
                search_result = await session.call_tool(
                    "journal.search_entries",
                    {
                        "query": "stdio",
                        "start_date": "2026-07-03",
                        "end_date": "2026-07-03",
                    },
                )

        assert [tool.name for tool in tools.tools] == [
            "journal.list_entries",
            "journal.search_entries",
            "calendar.list_events",
            "calendar.create_event",
            "calendar.update_event",
            "activity.ensure_log_calendar",
            "activity.record_completed_action",
            "reminders.list_reminders",
            "reminders.create_reminder",
            "reminders.complete_reminder",
        ]
        assert "reminders.delete_reminder" not in [tool.name for tool in tools.tools]
        assert [prompt.name for prompt in prompts.prompts] == [
            "activity.daily_review",
            "activity.weekly_missing_review",
            "activity.future_plan",
            "activity.confirm_uncertain_claims",
            "activity.write_plan",
        ]
        assert str(templates.resourceTemplates[0].uriTemplate) == (
            "journal://{source_id}/{entry_id}"
        )
        assert "stdio content" in resource.contents[0].text  # type: ignore[union-attr]
        assert search_result.structuredContent is not None
        assert search_result.structuredContent["entries"][0]["matched_terms"] == ["stdio"]
        assert "stdio content" not in stderr_path.read_text(encoding="utf-8")

    anyio.run(exercise_server)
