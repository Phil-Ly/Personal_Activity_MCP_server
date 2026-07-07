"""Local stdio MCP server entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from personal_activity_mcp.calendar import CalendarRepository, MacOSCalendarBackend
from personal_activity_mcp.config import ConfigError, load_config
from personal_activity_mcp.journal import JournalRepository
from personal_activity_mcp.resources.journal import register_journal_resources
from personal_activity_mcp.sidecar import SidecarRepository
from personal_activity_mcp.tools.calendar import register_calendar_tools
from personal_activity_mcp.tools.journal import register_journal_tools

DEFAULT_CONFIG_PATH = Path("~/.config/personal-activity-mcp/config.toml").expanduser()


def create_server(config_path: Path, calendar_backend: object | None = None) -> FastMCP:
    """Create a server whose filesystem scope is fixed by validated configuration."""
    config = load_config(config_path)
    repository = JournalRepository(config)
    sidecar = SidecarRepository(config.sidecar_path)
    sidecar.initialize()
    for source in config.journal_sources:
        sidecar.upsert_journal_source(source)
    for source in config.calendar_sources:
        sidecar.upsert_calendar_source(source)
    calendar_repository = CalendarRepository(
        config,
        calendar_backend or MacOSCalendarBackend(),
        sidecar,
    )

    server = FastMCP(
        "Personal Activity MCP",
        instructions="Read local personal activity evidence without interpreting it.",
        log_level="WARNING",
    )

    register_journal_tools(server, repository, sidecar)
    register_calendar_tools(server, calendar_repository)
    register_journal_resources(server, repository)

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
