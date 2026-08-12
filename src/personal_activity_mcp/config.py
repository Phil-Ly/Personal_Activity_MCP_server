"""Load and validate the local access configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_SIDECAR_PATH = (
    Path.home() / "Library" / "Application Support" / "pamcp" / "personal_activity.sqlite3"
).resolve()


class ConfigError(ValueError):
    """Raised when local configuration is missing or invalid."""


@dataclass(frozen=True)
class EventKitSource:
    """One explicitly authorized native EventKit account source."""

    source_id: str
    allow_calendar_write: bool
    default_calendar_source: bool
    allow_reminder_write: bool = False
    default_reminder_source: bool = False


@dataclass(frozen=True)
class AppConfig:
    """Validated application configuration."""

    sidecar_path: Path = DEFAULT_SIDECAR_PATH
    eventkit_sources: tuple[EventKitSource, ...] = ()
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

    _reject_unknown_keys(
        raw,
        {
            "sidecar_path",
            "eventkit_sources",
            "default_timezone",
        },
        "configuration",
    )

    return AppConfig(
        sidecar_path=_parse_sidecar_path(raw.get("sidecar_path")),
        eventkit_sources=_parse_eventkit_sources(raw.get("eventkit_sources")),
        default_timezone=_parse_default_timezone(raw.get("default_timezone")),
    )


def _parse_sidecar_path(raw_path: object) -> Path:
    if raw_path is None:
        return DEFAULT_SIDECAR_PATH
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError("sidecar_path must be a non-empty string")
    return Path(raw_path).expanduser().resolve()


def _parse_default_timezone(raw_timezone: object) -> str:
    if raw_timezone is None:
        return "UTC"
    if not isinstance(raw_timezone, str) or not raw_timezone.strip():
        raise ConfigError("default_timezone must be a non-empty string")
    timezone = raw_timezone.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ConfigError(f"Unknown default_timezone: {timezone}") from error
    return timezone


def _reject_unknown_keys(
    values: dict[str, object],
    allowed_keys: set[str],
    section_name: str,
) -> None:
    unknown_keys = sorted(set(values) - allowed_keys)
    if unknown_keys:
        raise ConfigError(f"Unknown {section_name} keys: {', '.join(unknown_keys)}")


def _parse_eventkit_sources(raw_sources: object) -> tuple[EventKitSource, ...]:
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, list):
        raise ConfigError("eventkit_sources must be a list")

    sources: list[EventKitSource] = []
    seen_ids: set[str] = set()
    for raw_source in raw_sources:
        source = _parse_eventkit_source(raw_source)
        if source.source_id in seen_ids:
            raise ConfigError(f"Duplicate EventKit source_id: {source.source_id}")
        seen_ids.add(source.source_id)
        sources.append(source)
    if sum(source.default_calendar_source for source in sources) > 1:
        raise ConfigError("Only one default Calendar EventKit Source may be configured")
    if sum(source.default_reminder_source for source in sources) > 1:
        raise ConfigError("Only one default Reminder EventKit Source may be configured")
    return tuple(sources)


def _parse_eventkit_source(raw_source: object) -> EventKitSource:
    if not isinstance(raw_source, dict):
        raise ConfigError("Each eventkit_sources entry must be a TOML table")
    _reject_unknown_keys(
        raw_source,
        {
            "source_id",
            "allow_calendar_write",
            "default_calendar_source",
            "allow_reminder_write",
            "default_reminder_source",
        },
        "EventKit source",
    )

    source_id = raw_source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ConfigError("EventKit source_id must be a non-empty string")
    _reject_control_characters(source_id, "EventKit source_id")

    allow_calendar_write = raw_source.get("allow_calendar_write", False)
    if not isinstance(allow_calendar_write, bool):
        raise ConfigError(f"EventKit Source allow_calendar_write must be a boolean: {source_id}")
    default_calendar_source = raw_source.get("default_calendar_source", False)
    if not isinstance(default_calendar_source, bool):
        raise ConfigError(f"EventKit Source default_calendar_source must be a boolean: {source_id}")
    if default_calendar_source and not allow_calendar_write:
        raise ConfigError("Default Calendar EventKit Source must allow Calendar writes")
    allow_reminder_write = raw_source.get("allow_reminder_write", False)
    if not isinstance(allow_reminder_write, bool):
        raise ConfigError(f"EventKit Source allow_reminder_write must be a boolean: {source_id}")
    default_reminder_source = raw_source.get("default_reminder_source", False)
    if not isinstance(default_reminder_source, bool):
        raise ConfigError(f"EventKit Source default_reminder_source must be a boolean: {source_id}")
    if default_reminder_source and not allow_reminder_write:
        raise ConfigError("Default Reminder EventKit Source must allow Reminder writes")

    return EventKitSource(
        source_id=source_id.strip(),
        allow_calendar_write=allow_calendar_write,
        default_calendar_source=default_calendar_source,
        allow_reminder_write=allow_reminder_write,
        default_reminder_source=default_reminder_source,
    )


def _reject_control_characters(value: str, field_name: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigError(f"{field_name} must not contain control characters")
