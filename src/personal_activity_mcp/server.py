"""Local stdio MCP server entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.calendar import CalendarRepository, MacOSCalendarBackend
from personal_activity_mcp.candidates import CandidateRepository
from personal_activity_mcp.config import ConfigError, load_config
from personal_activity_mcp.prompts.activity import register_review_prompt
from personal_activity_mcp.reminders import MacOSReminderBackend, ReminderRepository
from personal_activity_mcp.sidecar import SidecarRepository
from personal_activity_mcp.tools.calendar import register_calendar_tools
from personal_activity_mcp.tools.candidates import register_candidate_tools
from personal_activity_mcp.tools.reminders import register_reminder_tools

DEFAULT_CONFIG_PATH = Path("~/.config/personal-activity-mcp/config.toml").expanduser()


def create_server(
    config_path: Path,
    calendar_backend: object | None = None,
    reminder_backend: object | None = None,
) -> FastMCP:
    """Create a server whose external-data scope is fixed by validated configuration."""
    config = load_config(config_path)
    sidecar = SidecarRepository(config.sidecar_path)
    sidecar.initialize()
    for source in config.calendar_sources:
        sidecar.upsert_calendar_source(source)
    for source in config.reminder_sources:
        sidecar.upsert_reminder_source(source)
    calendar_repository = CalendarRepository(
        config,
        calendar_backend or MacOSCalendarBackend(),
        sidecar,
    )
    reminder_repository = ReminderRepository(
        config,
        reminder_backend or MacOSReminderBackend(),
        sidecar,
    )
    candidate_repository = CandidateRepository(sidecar)
    server = FastMCP(
        "Personal Activity MCP",
        instructions=(
            "Access configured Calendar and Reminders capabilities and persist "
            "provider-neutral ActionCandidates without interpreting user intent."
        ),
        log_level="WARNING",
    )

    register_calendar_tools(server, calendar_repository)
    register_reminder_tools(server, reminder_repository)
    register_candidate_tools(server, candidate_repository)
    register_review_prompt(server)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Load configuration and run the local stdio server."""
    parser = argparse.ArgumentParser(description="Personal Activity MCP Server")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("PERSONAL_ACTIVITY_MCP_CONFIG", DEFAULT_CONFIG_PATH)),
        help="Path to the local TOML configuration file",
    )
    args = parser.parse_args(argv)

    try:
        server = create_server(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
