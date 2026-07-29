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
    title: str
    allow_calendar_write: bool
    default_calendar_source: bool


@dataclass(frozen=True)
class ReminderSource:
    """A single explicitly authorized Apple Reminders list."""

    list_id: str
    title: str
    allow_write: bool


@dataclass(frozen=True)
class PrivacyConfig:
    """Privacy controls for logging and stored diagnostic data."""

    sensitive_logging_enabled: bool = False
    log_calendar_notes: bool = False
    log_reminder_notes: bool = False
    log_source_refs: bool = False


@dataclass(frozen=True)
class SecurityPolicy:
    """Local safety policy for transports and high-risk operations."""

    allow_remote_transport: bool = False
    allow_bulk_operations: bool = False
    allow_delete_operations: bool = False
    require_confirmation_for_event_completion_updates: bool = True
    require_confirmation_for_reminder_completion: bool = True


@dataclass(frozen=True)
class AppConfig:
    """Validated application configuration."""

    sidecar_path: Path = DEFAULT_SIDECAR_PATH
    eventkit_sources: tuple[EventKitSource, ...] = ()
    reminder_sources: tuple[ReminderSource, ...] = ()
    default_timezone: str = "UTC"
    privacy: PrivacyConfig = PrivacyConfig()
    security: SecurityPolicy = SecurityPolicy()


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
            "reminder_sources",
            "default_timezone",
            "privacy",
            "security",
        },
        "configuration",
    )

    return AppConfig(
        sidecar_path=_parse_sidecar_path(raw.get("sidecar_path")),
        eventkit_sources=_parse_eventkit_sources(raw.get("eventkit_sources")),
        reminder_sources=_parse_reminder_sources(raw.get("reminder_sources")),
        default_timezone=_parse_default_timezone(raw.get("default_timezone")),
        privacy=_parse_privacy_config(raw.get("privacy")),
        security=_parse_security_policy(raw.get("security")),
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


def _parse_privacy_config(raw_privacy: object) -> PrivacyConfig:
    if raw_privacy is None:
        return PrivacyConfig()
    if not isinstance(raw_privacy, dict):
        raise ConfigError("privacy must be a TOML table")
    _reject_unknown_keys(
        raw_privacy,
        {
            "sensitive_logging_enabled",
            "log_calendar_notes",
            "log_reminder_notes",
            "log_source_refs",
        },
        "privacy",
    )

    config = PrivacyConfig(
        sensitive_logging_enabled=_parse_bool(
            raw_privacy.get("sensitive_logging_enabled", False),
            "privacy.sensitive_logging_enabled",
        ),
        log_calendar_notes=_parse_bool(
            raw_privacy.get("log_calendar_notes", False),
            "privacy.log_calendar_notes",
        ),
        log_reminder_notes=_parse_bool(
            raw_privacy.get("log_reminder_notes", False),
            "privacy.log_reminder_notes",
        ),
        log_source_refs=_parse_bool(
            raw_privacy.get("log_source_refs", False),
            "privacy.log_source_refs",
        ),
    )
    if not config.sensitive_logging_enabled and (
        config.log_calendar_notes or config.log_reminder_notes or config.log_source_refs
    ):
        raise ConfigError("Sensitive logging detail flags require sensitive_logging_enabled = true")
    return config


def _parse_security_policy(raw_security: object) -> SecurityPolicy:
    if raw_security is None:
        return SecurityPolicy()
    if not isinstance(raw_security, dict):
        raise ConfigError("security must be a TOML table")
    _reject_unknown_keys(
        raw_security,
        {
            "allow_remote_transport",
            "allow_bulk_operations",
            "allow_delete_operations",
            "require_confirmation_for_event_completion_updates",
            "require_confirmation_for_reminder_completion",
        },
        "security",
    )

    policy = SecurityPolicy(
        allow_remote_transport=_parse_bool(
            raw_security.get("allow_remote_transport", False),
            "security.allow_remote_transport",
        ),
        allow_bulk_operations=_parse_bool(
            raw_security.get("allow_bulk_operations", False),
            "security.allow_bulk_operations",
        ),
        allow_delete_operations=_parse_bool(
            raw_security.get("allow_delete_operations", False),
            "security.allow_delete_operations",
        ),
        require_confirmation_for_event_completion_updates=_parse_bool(
            raw_security.get("require_confirmation_for_event_completion_updates", True),
            "security.require_confirmation_for_event_completion_updates",
        ),
        require_confirmation_for_reminder_completion=_parse_bool(
            raw_security.get("require_confirmation_for_reminder_completion", True),
            "security.require_confirmation_for_reminder_completion",
        ),
    )
    if policy.allow_remote_transport:
        raise ConfigError("Remote transport is not supported in v1.0")
    if policy.allow_bulk_operations:
        raise ConfigError("Bulk operations are not supported in v1.0")
    if policy.allow_delete_operations:
        raise ConfigError("Delete operations are frozen in v1.0")
    if not policy.require_confirmation_for_event_completion_updates:
        raise ConfigError("Calendar event completion updates must require user confirmation")
    if not policy.require_confirmation_for_reminder_completion:
        raise ConfigError("Reminder completion must require user confirmation")
    return policy


def _parse_bool(raw_value: object, field_name: str) -> bool:
    if not isinstance(raw_value, bool):
        raise ConfigError(f"{field_name} must be a boolean")
    return raw_value


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
    return tuple(sources)


def _parse_eventkit_source(raw_source: object) -> EventKitSource:
    if not isinstance(raw_source, dict):
        raise ConfigError("Each eventkit_sources entry must be a TOML table")
    _reject_unknown_keys(
        raw_source,
        {
            "source_id",
            "title",
            "allow_calendar_write",
            "default_calendar_source",
        },
        "EventKit source",
    )

    source_id = raw_source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ConfigError("EventKit source_id must be a non-empty string")
    _reject_control_characters(source_id, "EventKit source_id")

    title = raw_source.get("title", source_id)
    if not isinstance(title, str) or not title.strip():
        raise ConfigError(f"EventKit Source title must be a non-empty string: {source_id}")

    allow_calendar_write = raw_source.get("allow_calendar_write", False)
    if not isinstance(allow_calendar_write, bool):
        raise ConfigError(f"EventKit Source allow_calendar_write must be a boolean: {source_id}")
    default_calendar_source = raw_source.get("default_calendar_source", False)
    if not isinstance(default_calendar_source, bool):
        raise ConfigError(f"EventKit Source default_calendar_source must be a boolean: {source_id}")
    if default_calendar_source and not allow_calendar_write:
        raise ConfigError("Default Calendar EventKit Source must allow Calendar writes")

    return EventKitSource(
        source_id=source_id.strip(),
        title=title.strip(),
        allow_calendar_write=allow_calendar_write,
        default_calendar_source=default_calendar_source,
    )


def _parse_reminder_sources(raw_sources: object) -> tuple[ReminderSource, ...]:
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, list):
        raise ConfigError("reminder_sources must be a list")

    sources: list[ReminderSource] = []
    seen_ids: set[str] = set()
    for raw_source in raw_sources:
        source = _parse_reminder_source(raw_source)
        if source.list_id in seen_ids:
            raise ConfigError(f"Duplicate reminder list_id: {source.list_id}")
        seen_ids.add(source.list_id)
        sources.append(source)
    return tuple(sources)


def _parse_reminder_source(raw_source: object) -> ReminderSource:
    if not isinstance(raw_source, dict):
        raise ConfigError("Each reminder_sources entry must be a TOML table")
    _reject_unknown_keys(
        raw_source,
        {"list_id", "title", "allow_write"},
        "reminder source",
    )

    list_id = raw_source.get("list_id")
    if not isinstance(list_id, str) or not list_id.strip():
        raise ConfigError("Reminder list_id must be a non-empty string")
    _reject_control_characters(list_id, "Reminder list_id")

    title = raw_source.get("title", list_id)
    if not isinstance(title, str) or not title.strip():
        raise ConfigError(f"Reminder title must be a non-empty string: {list_id}")

    allow_write = raw_source.get("allow_write", False)
    if not isinstance(allow_write, bool):
        raise ConfigError(f"Reminder allow_write must be a boolean: {list_id}")

    return ReminderSource(
        list_id=list_id.strip(),
        title=title.strip(),
        allow_write=allow_write,
    )


def _reject_control_characters(value: str, field_name: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigError(f"{field_name} must not contain control characters")
