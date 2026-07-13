from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_activity_mcp.activity import ActivityRepository
from personal_activity_mcp.calendar import CalendarEnsureRecord, CalendarEventRecord
from personal_activity_mcp.config import AppConfig, CalendarSource, JournalSource
from personal_activity_mcp.sidecar import SidecarRepository


class FixedClock:
    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current


class FakeActivityCalendarBackend:
    def __init__(self) -> None:
        self.ensure_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.next_created_event_id = "activity-event-1"
        self.next_ensure_created = False

    def ensure_calendar(
        self, *, calendar_title: str, create_if_missing: bool
    ) -> CalendarEnsureRecord:
        self.ensure_calls.append(
            {
                "calendar_title": calendar_title,
                "create_if_missing": create_if_missing,
            }
        )
        return CalendarEnsureRecord(
            calendar_id=calendar_title,
            calendar_title=calendar_title,
            created=self.next_ensure_created,
        )

    def create_event(
        self,
        *,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        is_all_day: bool,
        notes: str | None,
        location: str | None,
        timezone: str,
    ) -> CalendarEventRecord:
        self.create_calls.append(
            {
                "calendar_id": calendar_id,
                "title": title,
                "start": start,
                "end": end,
                "is_all_day": is_all_day,
                "notes": notes,
                "location": location,
                "timezone": timezone,
            }
        )
        return CalendarEventRecord(
            event_id=self.next_created_event_id,
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=location,
            notes=notes,
        )


def make_config(tmp_path: Path) -> AppConfig:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    return AppConfig(
        journal_sources=(JournalSource("daily", journal_path.resolve(), (".md",)),),
        sidecar_path=tmp_path / "sidecar.sqlite3",
        calendar_sources=(
            CalendarSource("Personal Activity Log", "Personal Activity Log", True),
            CalendarSource("Work", "Work", True),
        ),
        default_activity_log_calendar_id="Personal Activity Log",
        default_timezone="Asia/Shanghai",
    )


def test_ensure_activity_log_calendar_records_configured_source(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeActivityCalendarBackend()
    repository = ActivityRepository(make_config(tmp_path), backend, sidecar)

    result = repository.ensure_log_calendar(
        calendar_title="Personal Activity Log",
        create_if_missing=True,
    )

    assert result.calendar_id == "Personal Activity Log"
    assert result.calendar_title == "Personal Activity Log"
    assert result.created is False
    assert result.is_default_activity_log is True
    assert backend.ensure_calls == [
        {
            "calendar_title": "Personal Activity Log",
            "create_if_missing": True,
        }
    ]
    with sidecar.connect() as connection:
        source = connection.execute(
            "SELECT * FROM source WHERE id = 'calendar:Personal Activity Log'"
        ).fetchone()
    assert source["source_type"] == "calendar"
    assert source["source_name"] == "Personal Activity Log"


def test_ensure_activity_log_calendar_reports_created_calendar(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeActivityCalendarBackend()
    backend.next_ensure_created = True
    repository = ActivityRepository(make_config(tmp_path), backend, sidecar)

    result = repository.ensure_log_calendar(
        calendar_title="Personal Activity Log",
        create_if_missing=True,
    )

    assert result.created is True


def test_record_completed_action_requires_confirmation(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    repository = ActivityRepository(
        make_config(tmp_path),
        FakeActivityCalendarBackend(),
        sidecar,
    )

    with pytest.raises(ValueError, match="USER_CONFIRMATION_REQUIRED"):
        repository.record_completed_action(
            calendar_id="Personal Activity Log",
            title="整理 MCP 项目边界",
            start=datetime(2026, 7, 6, 10, tzinfo=UTC),
            end=datetime(2026, 7, 6, 11, tzinfo=UTC),
            is_all_day=False,
            category="engineering",
            project="Personal Event MCP",
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            confirmed_by_user=False,
            idempotency_key="activity:record:demo",
        )


def test_record_completed_action_writes_action_record_once(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeActivityCalendarBackend()
    repository = ActivityRepository(make_config(tmp_path), backend, sidecar)
    start = datetime(2026, 7, 6, 10, tzinfo=UTC)
    end = datetime(2026, 7, 6, 11, tzinfo=UTC)

    created = repository.record_completed_action(
        calendar_id="Personal Activity Log",
        title="整理 MCP 项目边界",
        start=start,
        end=end,
        is_all_day=False,
        category="engineering",
        project="Personal Event MCP",
        notes="只保存必要元数据",
        location=None,
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        confirmed_by_user=True,
        idempotency_key="activity:record:demo",
    )
    repeated = repository.record_completed_action(
        calendar_id="Personal Activity Log",
        title="整理 MCP 项目边界",
        start=start,
        end=end,
        is_all_day=False,
        category="engineering",
        project="Personal Event MCP",
        notes="只保存必要元数据",
        location=None,
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        confirmed_by_user=True,
        idempotency_key="activity:record:demo",
    )

    assert len(backend.create_calls) == 1
    assert created.action_record_id.startswith("action_record:")
    assert created.event_id == "activity-event-1"
    assert created.created is True
    assert created.deduplicated is False
    assert created.status_semantics == "confirmed"
    assert created.provenance_ids == ["journal:entry-1"]
    assert repeated.created is False
    assert repeated.deduplicated is True
    assert repeated.action_record_id == created.action_record_id
    with sidecar.connect() as connection:
        item = connection.execute(
            "SELECT * FROM mcp_item WHERE id = ?",
            (created.action_record_id,),
        ).fetchone()
        audit = connection.execute(
            "SELECT * FROM operation_audit WHERE operation = 'activity.record_completed_action'"
        ).fetchone()
        provenance = connection.execute("SELECT * FROM provenance_link").fetchone()
    assert item["item_type"] == "action_record"
    assert item["external_id"] == "activity-event-1"
    assert item["external_calendar_or_list_id"] == "Personal Activity Log"
    assert item["status_semantics"] == "confirmed"
    assert item["created_by_mcp"] == 1
    assert "整理 MCP 项目边界" not in " ".join(str(value) for value in item)
    assert audit["target_item_id"] == created.action_record_id
    assert audit["confirmed_by_user"] == 1
    assert provenance["target_item_id"] == created.action_record_id
    assert provenance["relation_type"] == "created_from"


def test_record_completed_action_uses_injected_clock(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeActivityCalendarBackend()
    repository = ActivityRepository(
        make_config(tmp_path),
        backend,
        sidecar,
        clock=FixedClock(datetime(2030, 1, 1, 12, tzinfo=UTC)),
    )

    result = repository.record_completed_action(
        calendar_id="Personal Activity Log",
        title="Future relative to the host clock",
        start=datetime(2030, 1, 1, 10, tzinfo=UTC),
        end=datetime(2030, 1, 1, 11, tzinfo=UTC),
        is_all_day=False,
        category=None,
        project=None,
        notes=None,
        location=None,
        timezone="UTC",
        provenance_ids=[],
        confirmed_by_user=True,
        idempotency_key="activity:record:fixed-clock",
    )

    assert result.created is True
    assert len(backend.create_calls) == 1


def test_record_completed_action_rejects_naive_datetime_before_backend(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeActivityCalendarBackend()
    repository = ActivityRepository(
        make_config(tmp_path),
        backend,
        sidecar,
        clock=FixedClock(datetime(2026, 7, 8, 12, tzinfo=UTC)),
    )

    with pytest.raises(ValueError, match="start must include timezone information"):
        repository.record_completed_action(
            calendar_id="Personal Activity Log",
            title="Naive datetime",
            start=datetime(2026, 7, 8, 10),
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            category=None,
            project=None,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            confirmed_by_user=True,
            idempotency_key="activity:record:naive",
        )

    assert backend.create_calls == []


def test_record_completed_action_rejects_non_activity_log_calendar(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    repository = ActivityRepository(
        make_config(tmp_path),
        FakeActivityCalendarBackend(),
        sidecar,
    )

    with pytest.raises(ValueError, match="Calendar is not configured as Activity Log: Work"):
        repository.record_completed_action(
            calendar_id="Work",
            title="整理 MCP 项目边界",
            start=datetime(2026, 7, 6, 10, tzinfo=UTC),
            end=datetime(2026, 7, 6, 11, tzinfo=UTC),
            is_all_day=False,
            category=None,
            project=None,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            confirmed_by_user=True,
            idempotency_key="activity:record:wrong-calendar",
        )
