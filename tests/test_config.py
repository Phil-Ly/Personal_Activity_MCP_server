from pathlib import Path

import pytest

from personal_activity_mcp.config import ConfigError, load_config


def write_config(path: Path, content: str = "") -> None:
    path.write_text(content.strip(), encoding="utf-8")


def test_load_config_accepts_configuration_without_local_file_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path)

    config = load_config(config_path)

    assert config.eventkit_sources == ()
    assert (
        config.sidecar_path
        == (
            Path.home() / "Library" / "Application Support" / "pamcp" / "personal_activity.sqlite3"
        ).resolve()
    )


def test_load_config_rejects_unknown_top_level_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
local_file_sources = []
""",
    )

    with pytest.raises(
        ConfigError,
        match="Unknown configuration keys: local_file_sources",
    ):
        load_config(config_path)


def test_load_config_accepts_explicit_sidecar_path(tmp_path: Path) -> None:
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    write_config(config_path, f'sidecar_path = "{sidecar_path}"')

    config = load_config(config_path)

    assert config.sidecar_path == sidecar_path.resolve()


def test_load_config_accepts_eventkit_source_container_policies(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
default_timezone = "Asia/Shanghai"

[[eventkit_sources]]
source_id = "source-icloud"
allow_calendar_write = true
default_calendar_source = true
allow_reminder_write = true
default_reminder_source = true

[[eventkit_sources]]
source_id = "source-exchange"
allow_calendar_write = false
allow_reminder_write = false
""",
    )

    config = load_config(config_path)

    assert config.default_timezone == "Asia/Shanghai"
    assert config.eventkit_sources[0].source_id == "source-icloud"
    assert config.eventkit_sources[0].allow_calendar_write is True
    assert config.eventkit_sources[0].default_calendar_source is True
    assert config.eventkit_sources[0].allow_reminder_write is True
    assert config.eventkit_sources[0].default_reminder_source is True
    assert config.eventkit_sources[1].source_id == "source-exchange"
    assert config.eventkit_sources[1].allow_calendar_write is False
    assert config.eventkit_sources[1].default_calendar_source is False
    assert config.eventkit_sources[1].allow_reminder_write is False
    assert config.eventkit_sources[1].default_reminder_source is False


def test_load_config_rejects_legacy_calendar_container_allowlist(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[calendar_sources]]
calendar_id = "Personal"
allow_write = true
""",
    )

    with pytest.raises(
        ConfigError,
        match="Unknown configuration keys: calendar_sources",
    ):
        load_config(config_path)


def test_load_config_rejects_multiple_default_calendar_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-icloud"
allow_calendar_write = true
default_calendar_source = true

[[eventkit_sources]]
source_id = "source-local"
allow_calendar_write = true
default_calendar_source = true
""",
    )

    with pytest.raises(ConfigError, match="Only one default Calendar EventKit Source"):
        load_config(config_path)


def test_load_config_rejects_read_only_default_calendar_source(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-exchange"
allow_calendar_write = false
default_calendar_source = true
""",
    )

    with pytest.raises(
        ConfigError,
        match="Default Calendar EventKit Source must allow Calendar writes",
    ):
        load_config(config_path)


def test_load_config_rejects_unknown_default_timezone(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path, 'default_timezone = "Mars/Olympus_Mons"')

    with pytest.raises(ConfigError, match="Unknown default_timezone: Mars/Olympus_Mons"):
        load_config(config_path)


def test_load_config_rejects_legacy_reminder_list_allowlist(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[reminder_sources]]
list_id = "Personal"
allow_write = true
""",
    )

    with pytest.raises(
        ConfigError,
        match="Unknown configuration keys: reminder_sources",
    ):
        load_config(config_path)


def test_load_config_rejects_multiple_default_reminder_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-icloud"
allow_reminder_write = true
default_reminder_source = true

[[eventkit_sources]]
source_id = "source-local"
allow_reminder_write = true
default_reminder_source = true
""",
    )

    with pytest.raises(ConfigError, match="Only one default Reminder EventKit Source"):
        load_config(config_path)


def test_load_config_rejects_read_only_default_reminder_source(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-exchange"
allow_reminder_write = false
default_reminder_source = true
""",
    )

    with pytest.raises(
        ConfigError,
        match="Default Reminder EventKit Source must allow Reminder writes",
    ):
        load_config(config_path)


@pytest.mark.parametrize("section", ["privacy", "security"])
def test_load_config_rejects_removed_noop_sections(tmp_path: Path, section: str) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        f"""
[{section}]
enabled = false
""",
    )

    with pytest.raises(
        ConfigError,
        match=rf"Unknown configuration keys: {section}",
    ):
        load_config(config_path)


def test_load_config_rejects_unknown_source_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-icloud"
filesystem_path = "/tmp/calendar"
""",
    )

    with pytest.raises(
        ConfigError,
        match="Unknown EventKit source keys: filesystem_path",
    ):
        load_config(config_path)


@pytest.mark.parametrize(
    ("section", "identifier_field"),
    [
        ("eventkit_sources", "source_id"),
    ],
)
def test_load_config_rejects_control_characters_in_source_identifiers(
    tmp_path: Path,
    section: str,
    identifier_field: str,
) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        f"""
[[{section}]]
{identifier_field} = "Personal\\nWork"
""",
    )

    with pytest.raises(ConfigError, match="must not contain control characters"):
        load_config(config_path)


def test_load_config_rejects_removed_activity_log_setting(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        'default_activity_log_calendar_id = "Personal Activity Log"',
    )

    with pytest.raises(
        ConfigError,
        match="Unknown configuration keys: default_activity_log_calendar_id",
    ):
        load_config(config_path)


def test_load_config_rejects_duplicate_eventkit_source_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(
        config_path,
        """
[[eventkit_sources]]
source_id = "source-icloud"

[[eventkit_sources]]
source_id = "source-icloud"
""",
    )

    with pytest.raises(ConfigError, match="Duplicate EventKit source_id: source-icloud"):
        load_config(config_path)


def test_load_config_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(tmp_path / "missing.toml")
