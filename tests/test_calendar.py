from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_activity_mcp.calendar import CalendarEventRecord, CalendarRepository
from personal_activity_mcp.config import AppConfig, CalendarSource, JournalSource
from personal_activity_mcp.sidecar import SidecarRepository


class FixedClock:
    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current


class FakeCalendarBackend:
    def __init__(self, events: list[CalendarEventRecord] | None = None) -> None:
        self.events = events or []
        self.list_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.next_created_event_id = "created-event-1"

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        self.list_calls.append(
            {
                "calendar_ids": calendar_ids,
                "start": start,
                "end": end,
                "include_notes": include_notes,
                "include_location": include_location,
            }
        )
        return self.events

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

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        is_all_day: bool | None,
        notes: str | None,
        location: str | None,
        timezone: str,
    ) -> CalendarEventRecord:
        self.update_calls.append(
            {
                "event_id": event_id,
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
        existing = next(
            (
                event
                for event in self.events
                if event.event_id == event_id and event.calendar_id == calendar_id
            ),
            None,
        )
        base = existing or CalendarEventRecord(
            event_id=event_id,
            calendar_id=calendar_id,
            title="Existing event",
            start=start or datetime(2026, 7, 8, 10, tzinfo=UTC),
            end=end or datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
        )
        return CalendarEventRecord(
            event_id=event_id,
            calendar_id=calendar_id,
            title=title if title is not None else base.title,
            start=start if start is not None else base.start,
            end=end if end is not None else base.end,
            is_all_day=is_all_day if is_all_day is not None else base.is_all_day,
            location=location if location is not None else base.location,
            notes=notes if notes is not None else base.notes,
        )


def make_config(tmp_path: Path) -> AppConfig:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    return AppConfig(
        journal_sources=(JournalSource("daily", journal_path.resolve(), (".md",)),),
        sidecar_path=tmp_path / "sidecar.sqlite3",
        calendar_sources=(
            CalendarSource("Personal", "Personal", True),
            CalendarSource("Work", "Work", False),
        ),
        default_timezone="Asia/Shanghai",
    )


def test_list_events_queries_only_allowed_calendars_without_notes_by_default(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 7, 8, 9, tzinfo=UTC)
    end = datetime(2026, 7, 8, 18, tzinfo=UTC)
    event = CalendarEventRecord(
        event_id="event-1",
        calendar_id="Personal",
        title="MCP demo",
        start=datetime(2026, 7, 8, 10, tzinfo=UTC),
        end=datetime(2026, 7, 8, 11, tzinfo=UTC),
        is_all_day=False,
        location="Room 1",
        notes="Private notes",
    )
    backend = FakeCalendarBackend([event])
    repository = CalendarRepository(
        make_config(tmp_path),
        backend,
        clock=FixedClock(datetime(2026, 7, 8, 9, tzinfo=UTC)),
    )

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=start,
        end=end,
    )

    assert backend.list_calls == [
        {
            "calendar_ids": ["Personal"],
            "start": start,
            "end": end,
            "include_notes": False,
            "include_location": False,
        }
    ]
    assert len(result.events) == 1
    evidence = result.events[0]
    assert evidence.evidence_id.startswith("calendar:")
    assert evidence.source_type == "calendar"
    assert evidence.source_id == "Personal"
    assert evidence.event_id == "event-1"
    assert evidence.calendar_id == "Personal"
    assert evidence.status_semantics == "planned"
    assert evidence.created_by_mcp is False
    assert evidence.provenance_ids == []
    assert evidence.notes is None
    assert evidence.location is None


def test_list_events_rejects_unconfigured_calendar(tmp_path: Path) -> None:
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend())

    with pytest.raises(ValueError, match="Unknown calendar_ids: Secret"):
        repository.list_events(
            calendar_ids=["Secret"],
            start=datetime(2026, 7, 8, 9, tzinfo=UTC),
            end=datetime(2026, 7, 8, 18, tzinfo=UTC),
        )


def test_list_events_rejects_naive_datetime_before_backend(tmp_path: Path) -> None:
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend)

    with pytest.raises(ValueError, match="start must include timezone information"):
        repository.list_events(
            calendar_ids=["Personal"],
            start=datetime(2026, 7, 8, 9),
            end=datetime(2026, 7, 8, 18, tzinfo=UTC),
        )

    assert backend.list_calls == []


def test_list_events_uses_sidecar_semantics_for_mcp_created_events(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    sidecar.upsert_mcp_item(
        item_id="calendar_event:stable-1",
        item_type="calendar_event",
        external_id="event-1",
        external_calendar_or_list_id="Personal",
        title_hash="title-hash",
        time_start="2026-07-08T10:00:00+00:00",
        time_end="2026-07-08T11:00:00+00:00",
        status_semantics="planned",
        created_by_mcp=True,
    )
    sidecar.record_provenance_link(
        target_item_id="calendar_event:stable-1",
        evidence_type="journal_entry",
        evidence_id="journal:entry-1",
        relation_type="created_from",
    )
    event = CalendarEventRecord(
        event_id="event-1",
        calendar_id="Personal",
        title="MCP demo",
        start=datetime(2026, 7, 8, 10, tzinfo=UTC),
        end=datetime(2026, 7, 8, 11, tzinfo=UTC),
        is_all_day=False,
    )
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend([event]), sidecar)

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, 9, tzinfo=UTC),
        end=datetime(2026, 7, 8, 18, tzinfo=UTC),
    )

    evidence = result.events[0]
    assert evidence.created_by_mcp is True
    assert evidence.status_semantics == "planned"
    assert evidence.provenance_ids == ["journal:entry-1"]


def test_create_event_writes_calendar_once_and_records_sidecar_metadata(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)
    start = datetime(2026, 7, 8, 10, tzinfo=UTC)
    end = datetime(2026, 7, 8, 11, tzinfo=UTC)

    created = repository.create_event(
        calendar_id="Personal",
        title="MCP demo",
        start=start,
        end=end,
        is_all_day=False,
        notes="Private event notes",
        location="Room 1",
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        idempotency_key="calendar:create:demo",
    )
    repeated = repository.create_event(
        calendar_id="Personal",
        title="MCP demo",
        start=start,
        end=end,
        is_all_day=False,
        notes="Private event notes",
        location="Room 1",
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        idempotency_key="calendar:create:demo",
    )

    assert len(backend.create_calls) == 1
    assert created.created is True
    assert created.deduplicated is False
    assert created.event_id == "created-event-1"
    assert repeated.created is False
    assert repeated.deduplicated is True
    assert repeated.event_id == "created-event-1"
    assert repeated.stable_id == created.stable_id
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        provenance = connection.execute("SELECT * FROM provenance_link").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == created.stable_id
    assert item["item_type"] == "calendar_event"
    assert item["external_id"] == "created-event-1"
    assert item["external_calendar_or_list_id"] == "Personal"
    assert item["title_hash"] is not None
    assert "MCP demo" not in " ".join(str(value) for value in item)
    assert idempotency["key"] == "calendar:create:demo"
    assert idempotency["result_item_id"] == created.stable_id
    assert provenance["target_item_id"] == created.stable_id
    assert provenance["evidence_type"] == "journal_entry"
    assert provenance["evidence_id"] == "journal:entry-1"
    assert audit["operation"] == "calendar.create_event"
    assert audit["target_item_id"] == created.stable_id
    assert audit["result_status"] == "succeeded"


def test_create_event_rejects_calendar_without_write_permission(tmp_path: Path) -> None:
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend())

    with pytest.raises(ValueError, match="Calendar is not allowed for writes: Work"):
        repository.create_event(
            calendar_id="Work",
            title="MCP demo",
            start=datetime(2026, 7, 8, 10, tzinfo=UTC),
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            idempotency_key="calendar:create:demo",
        )


def test_create_event_rejects_naive_datetime_before_backend(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ValueError, match="start must include timezone information"):
        repository.create_event(
            calendar_id="Personal",
            title="MCP demo",
            start=datetime(2026, 7, 8, 10),
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            idempotency_key="calendar:create:naive",
        )

    assert backend.create_calls == []


def test_create_event_rejects_idempotency_conflict(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend(), sidecar)
    start = datetime(2026, 7, 8, 10, tzinfo=UTC)

    repository.create_event(
        calendar_id="Personal",
        title="MCP demo",
        start=start,
        end=datetime(2026, 7, 8, 11, tzinfo=UTC),
        is_all_day=False,
        notes=None,
        location=None,
        timezone="Asia/Shanghai",
        provenance_ids=[],
        idempotency_key="calendar:create:demo",
    )

    with pytest.raises(ValueError, match="idempotency_key conflicts with different request"):
        repository.create_event(
            calendar_id="Personal",
            title="Different title",
            start=start,
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            idempotency_key="calendar:create:demo",
        )


def test_update_event_writes_once_and_records_sidecar_metadata(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)
    start = datetime(2026, 7, 8, 12, tzinfo=UTC)
    end = datetime(2026, 7, 8, 13, tzinfo=UTC)

    updated = repository.update_event(
        calendar_id="Personal",
        event_id="event-1",
        title="Updated MCP demo",
        start=start,
        end=end,
        is_all_day=False,
        notes="Updated notes",
        location="Room 2",
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        confirmed_by_user=False,
        idempotency_key="calendar:update:demo",
    )
    repeated = repository.update_event(
        calendar_id="Personal",
        event_id="event-1",
        title="Updated MCP demo",
        start=start,
        end=end,
        is_all_day=False,
        notes="Updated notes",
        location="Room 2",
        timezone="Asia/Shanghai",
        provenance_ids=["journal:entry-1"],
        confirmed_by_user=False,
        idempotency_key="calendar:update:demo",
    )

    assert len(backend.update_calls) == 1
    assert updated.updated is True
    assert updated.deduplicated is False
    assert updated.event_id == "event-1"
    assert updated.updated_fields == ["title", "start", "end", "is_all_day", "notes", "location"]
    assert updated.audit_id.startswith("audit:")
    assert repeated.updated is False
    assert repeated.deduplicated is True
    assert repeated.stable_id == updated.stable_id
    with sidecar.connect() as connection:
        item = connection.execute(
            "SELECT * FROM mcp_item WHERE id = ?", (updated.stable_id,)
        ).fetchone()
        audit = connection.execute(
            "SELECT * FROM operation_audit WHERE operation = 'calendar.update_event'"
        ).fetchone()
    assert item["item_type"] == "calendar_event"
    assert item["external_id"] == "event-1"
    assert item["status_semantics"] == "planned"
    assert audit["target_item_id"] == updated.stable_id
    assert audit["confirmed_by_user"] == 0


def test_update_event_rejects_naive_datetime_before_backend(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ValueError, match="start must include timezone information"):
        repository.update_event(
            calendar_id="Personal",
            event_id="event-1",
            title=None,
            start=datetime(2026, 7, 8, 12),
            end=datetime(2026, 7, 8, 13, tzinfo=UTC),
            is_all_day=None,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            confirmed_by_user=False,
            idempotency_key="calendar:update:naive",
        )

    assert backend.update_calls == []


def test_update_confirmed_action_requires_user_confirmation(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    sidecar.upsert_mcp_item(
        item_id="action_record:stable-1",
        item_type="action_record",
        external_id="event-1",
        external_calendar_or_list_id="Personal",
        title_hash="title-hash",
        time_start="2026-07-06T10:00:00+00:00",
        time_end="2026-07-06T11:00:00+00:00",
        status_semantics="confirmed",
        created_by_mcp=True,
    )
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend(), sidecar)

    with pytest.raises(ValueError, match="USER_CONFIRMATION_REQUIRED"):
        repository.update_event(
            calendar_id="Personal",
            event_id="event-1",
            title="Unsafe update",
            start=None,
            end=None,
            is_all_day=None,
            notes=None,
            location=None,
            timezone="Asia/Shanghai",
            provenance_ids=[],
            confirmed_by_user=False,
            idempotency_key="calendar:update:confirmed-action",
        )
