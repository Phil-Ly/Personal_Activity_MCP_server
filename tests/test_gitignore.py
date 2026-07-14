import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "config.toml",
        "config.local.toml",
        "nested/config.dev.toml",
        "personal_activity.sqlite",
        "personal_activity.sqlite-journal",
        "personal_activity.sqlite-wal",
        "personal_activity.sqlite-shm",
        "personal_activity.sqlite3",
        "personal_activity.sqlite3-journal",
        "personal_activity.sqlite3-wal",
        "personal_activity.sqlite3-shm",
        "personal_activity.db",
        "personal_activity.db-journal",
        "personal_activity.db-wal",
        "personal_activity.db-shm",
        "personal_activity.db3",
        "personal_activity.db3-journal",
        "personal_activity.db3-wal",
        "personal_activity.db3-shm",
    ],
)
def test_local_config_and_database_state_are_ignored(path: str) -> None:
    assert _is_ignored(path)


def test_example_config_remains_trackable() -> None:
    assert not _is_ignored("config.example.toml")
