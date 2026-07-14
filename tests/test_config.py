from pathlib import Path

import pytest

from personal_activity_mcp.config import ConfigError, load_config


def write_config(path: Path, journal_path: Path, source_id: str = "daily") -> None:
    path.write_text(
        f"""
[[journal_sources]]
source_id = "{source_id}"
path = "{journal_path}"
extensions = [".md", ".txt"]
""".strip(),
        encoding="utf-8",
    )


def test_load_config_accepts_an_explicit_journal_directory(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)

    config = load_config(config_path)

    assert config.journal_sources[0].source_id == "daily"
    assert config.journal_sources[0].path == journal_path.resolve()
    assert config.journal_sources[0].extensions == (".md", ".txt")
    assert (
        config.sidecar_path
        == (
            Path.home()
            / "Library"
            / "Application Support"
            / "personal-activity-mcp"
            / "personal_activity.sqlite3"
        ).resolve()
    )


def test_load_config_accepts_explicit_sidecar_path(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    sidecar_path = tmp_path / "state" / "activity.sqlite3"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
sidecar_path = "{sidecar_path}"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.sidecar_path == sidecar_path.resolve()


def test_load_config_accepts_calendar_allowlist(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
default_timezone = "Asia/Shanghai"
default_activity_log_calendar_id = "Personal Activity Log"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[[calendar_sources]]
calendar_id = "Personal"
title = "Personal"
allow_write = true

[[calendar_sources]]
calendar_id = "Work"
title = "Work"
allow_write = false
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.default_timezone == "Asia/Shanghai"
    assert config.default_activity_log_calendar_id == "Personal Activity Log"
    assert config.calendar_sources[0].calendar_id == "Personal"
    assert config.calendar_sources[0].title == "Personal"
    assert config.calendar_sources[0].allow_write is True
    assert config.calendar_sources[1].calendar_id == "Work"
    assert config.calendar_sources[1].allow_write is False


def test_load_config_rejects_unknown_default_timezone(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
default_timezone = "Mars/Olympus_Mons"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Unknown default_timezone: Mars/Olympus_Mons"):
        load_config(config_path)


def test_load_config_accepts_reminder_list_allowlist(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[[reminder_sources]]
list_id = "Personal"
title = "Personal"
allow_write = true

[[reminder_sources]]
list_id = "Work"
title = "Work"
allow_write = false
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.reminder_sources[0].list_id == "Personal"
    assert config.reminder_sources[0].title == "Personal"
    assert config.reminder_sources[0].allow_write is True
    assert config.reminder_sources[1].list_id == "Work"
    assert config.reminder_sources[1].allow_write is False


def test_load_config_defaults_to_private_and_strict_local_policy(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, journal_path)

    config = load_config(config_path)

    assert config.privacy.sensitive_logging_enabled is False
    assert config.privacy.log_journal_content is False
    assert config.privacy.log_calendar_notes is False
    assert config.privacy.log_reminder_notes is False
    assert config.privacy.log_llm_outputs is False
    assert config.security.allow_remote_transport is False
    assert config.security.allow_bulk_operations is False
    assert config.security.allow_delete_operations is False
    assert config.security.require_confirmation_for_completed_actions is True
    assert config.security.require_confirmation_for_confirmed_action_updates is True


def test_load_config_accepts_explicit_privacy_and_security_policy(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[privacy]
sensitive_logging_enabled = false
log_journal_content = false
log_calendar_notes = false
log_reminder_notes = false
log_llm_outputs = false

[security]
allow_remote_transport = false
allow_bulk_operations = false
allow_delete_operations = false
require_confirmation_for_completed_actions = true
require_confirmation_for_confirmed_action_updates = true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.privacy.sensitive_logging_enabled is False
    assert config.security.allow_delete_operations is False
    assert config.security.require_confirmation_for_completed_actions is True


def test_load_config_rejects_sensitive_detail_logging_without_opt_in(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[privacy]
log_journal_content = true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="Sensitive logging detail flags require sensitive_logging_enabled = true",
    ):
        load_config(config_path)


def test_load_config_rejects_security_policy_that_enables_frozen_delete(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[security]
allow_delete_operations = true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Delete operations are frozen in v1.0"):
        load_config(config_path)


def test_load_config_rejects_security_policy_that_disables_required_confirmation(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[security]
require_confirmation_for_completed_actions = false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigError,
        match="Completed action records must require user confirmation",
    ):
        load_config(config_path)


def test_load_config_rejects_duplicate_reminder_list_ids(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[[reminder_sources]]
list_id = "Personal"

[[reminder_sources]]
list_id = "Personal"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Duplicate reminder list_id: Personal"):
        load_config(config_path)


def test_load_config_rejects_duplicate_calendar_ids(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[[calendar_sources]]
calendar_id = "Personal"

[[calendar_sources]]
calendar_id = "Personal"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Duplicate calendar_id: Personal"):
        load_config(config_path)


def test_load_config_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_rejects_a_missing_journal_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    write_config(config_path, tmp_path / "missing-journal")

    with pytest.raises(ConfigError, match="Journal directory does not exist"):
        load_config(config_path)


def test_load_config_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[journal_sources]]
source_id = "daily"
path = "{journal_path}"

[[journal_sources]]
source_id = "daily"
path = "{journal_path}"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Duplicate journal source_id: daily"):
        load_config(config_path)
