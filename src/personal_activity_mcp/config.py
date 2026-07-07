"""Load and validate the local access configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when local configuration is missing or invalid."""


@dataclass(frozen=True)
class JournalSource:
    """A single explicitly authorized journal directory."""

    source_id: str
    path: Path
    extensions: tuple[str, ...]


@dataclass(frozen=True)
class CalendarSource:
    """A single explicitly authorized Apple Calendar."""

    calendar_id: str
    title: str
    allow_write: bool


@dataclass(frozen=True)
class AppConfig:
    """Validated application configuration."""

    journal_sources: tuple[JournalSource, ...]
    sidecar_path: Path = Path(":memory:")
    calendar_sources: tuple[CalendarSource, ...] = ()
    default_timezone: str = "UTC"


def load_config(config_path: Path) -> AppConfig:
    """Load a TOML configuration without granting implicit filesystem access."""
    path = config_path.expanduser()
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Unable to load configuration: {error}") from error

    raw_sources = raw.get("journal_sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("At least one journal_sources entry is required")

    sources: list[JournalSource] = []
    seen_ids: set[str] = set()
    for raw_source in raw_sources:
        source = _parse_journal_source(raw_source)
        if source.source_id in seen_ids:
            raise ConfigError(f"Duplicate journal source_id: {source.source_id}")
        seen_ids.add(source.source_id)
        sources.append(source)

    return AppConfig(
        journal_sources=tuple(sources),
        sidecar_path=_parse_sidecar_path(raw.get("sidecar_path"), path.parent),
        calendar_sources=_parse_calendar_sources(raw.get("calendar_sources")),
        default_timezone=_parse_default_timezone(raw.get("default_timezone")),
    )


def _parse_sidecar_path(raw_path: object, config_dir: Path) -> Path:
    if raw_path is None:
        return (config_dir / "personal_activity.sqlite3").resolve()
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError("sidecar_path must be a non-empty string")
    return Path(raw_path).expanduser().resolve()


def _parse_default_timezone(raw_timezone: object) -> str:
    if raw_timezone is None:
        return "UTC"
    if not isinstance(raw_timezone, str) or not raw_timezone.strip():
        raise ConfigError("default_timezone must be a non-empty string")
    return raw_timezone.strip()


def _parse_calendar_sources(raw_sources: object) -> tuple[CalendarSource, ...]:
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, list):
        raise ConfigError("calendar_sources must be a list")

    sources: list[CalendarSource] = []
    seen_ids: set[str] = set()
    for raw_source in raw_sources:
        source = _parse_calendar_source(raw_source)
        if source.calendar_id in seen_ids:
            raise ConfigError(f"Duplicate calendar_id: {source.calendar_id}")
        seen_ids.add(source.calendar_id)
        sources.append(source)
    return tuple(sources)


def _parse_calendar_source(raw_source: object) -> CalendarSource:
    if not isinstance(raw_source, dict):
        raise ConfigError("Each calendar_sources entry must be a TOML table")

    calendar_id = raw_source.get("calendar_id")
    if not isinstance(calendar_id, str) or not calendar_id.strip():
        raise ConfigError("Calendar calendar_id must be a non-empty string")

    title = raw_source.get("title", calendar_id)
    if not isinstance(title, str) or not title.strip():
        raise ConfigError(f"Calendar title must be a non-empty string: {calendar_id}")

    allow_write = raw_source.get("allow_write", False)
    if not isinstance(allow_write, bool):
        raise ConfigError(f"Calendar allow_write must be a boolean: {calendar_id}")

    return CalendarSource(
        calendar_id=calendar_id.strip(),
        title=title.strip(),
        allow_write=allow_write,
    )


def _parse_journal_source(raw_source: object) -> JournalSource:
    if not isinstance(raw_source, dict):
        raise ConfigError("Each journal_sources entry must be a TOML table")

    source_id = raw_source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ConfigError("Journal source_id must be a non-empty string")

    raw_path = raw_source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError(f"Journal path is required for source: {source_id}")
    journal_path = Path(raw_path).expanduser().resolve()
    if not journal_path.exists():
        raise ConfigError(f"Journal directory does not exist: {journal_path}")
    if not journal_path.is_dir():
        raise ConfigError(f"Journal path is not a directory: {journal_path}")

    raw_extensions = raw_source.get("extensions", [".md", ".txt"])
    if not isinstance(raw_extensions, list) or not raw_extensions:
        raise ConfigError(f"Journal extensions must be a non-empty list: {source_id}")
    if not all(isinstance(value, str) and value.startswith(".") for value in raw_extensions):
        raise ConfigError(f"Journal extensions must start with '.': {source_id}")

    return JournalSource(
        source_id=source_id.strip(),
        path=journal_path,
        extensions=tuple(extension.lower() for extension in raw_extensions),
    )
