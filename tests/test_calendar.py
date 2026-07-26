from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from personal_activity_mcp.calendar import (
    CalendarBackendError,
    CalendarEventRecord,
    CalendarRepository,
)
from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.config import AppConfig, CalendarSource
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
    return AppConfig(
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
    assert evidence.target_ref.model_dump() == {
        "resource_type": "calendar_event",
        "item_id": "event-1",
        "container_id": "Personal",
    }
    assert evidence.state_token
    assert evidence.completion_status == "unknown"
    assert evidence.status_semantics == "planned"
    assert evidence.created_by_mcp is False
    assert evidence.source_refs == []
    assert evidence.notes is None
    assert evidence.location is None


def test_list_events_excludes_backend_records_outside_selected_calendars(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarBackend(
        [
            CalendarEventRecord(
                event_id="work-event",
                calendar_id="Work",
                title="Work event",
                start=datetime(2026, 7, 8, 10, tzinfo=UTC),
                end=datetime(2026, 7, 8, 11, tzinfo=UTC),
                is_all_day=False,
            )
        ]
    )
    repository = CalendarRepository(make_config(tmp_path), backend)

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, 9, tzinfo=UTC),
        end=datetime(2026, 7, 8, 18, tzinfo=UTC),
    )

    assert result.events == []


def test_list_events_deduplicates_identical_records_and_warns_on_conflicts(
    tmp_path: Path,
) -> None:
    identical = CalendarEventRecord(
        event_id="event-1",
        calendar_id="Personal",
        title="Same event",
        start=datetime(2026, 7, 8, 10, tzinfo=UTC),
        end=datetime(2026, 7, 8, 11, tzinfo=UTC),
        is_all_day=False,
    )
    conflict_a = CalendarEventRecord(
        event_id="event-2",
        calendar_id="Personal",
        title="First title",
        start=datetime(2026, 7, 8, 12, tzinfo=UTC),
        end=datetime(2026, 7, 8, 13, tzinfo=UTC),
        is_all_day=False,
    )
    conflict_b = conflict_a.model_copy(update={"title": "Conflicting title"})
    repository = CalendarRepository(
        make_config(tmp_path),
        FakeCalendarBackend([identical, identical.model_copy(), conflict_a, conflict_b]),
    )

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, 9, tzinfo=UTC),
        end=datetime(2026, 7, 8, 18, tzinfo=UTC),
    )

    assert [event.event_id for event in result.events] == ["event-1"]
    assert [warning.model_dump() for warning in result.warnings] == [
        {
            "code": "DUPLICATE_SOURCE_ITEM",
            "message": "Conflicting Calendar records share the same source identity",
            "related_item_ids": ["Personal:event-2"],
        }
    ]


def test_list_events_uses_stable_bounded_cursor_pagination(tmp_path: Path) -> None:
    events = [
        CalendarEventRecord(
            event_id=f"event-{index}",
            calendar_id="Personal",
            title=f"Event {index}",
            start=datetime(2026, 7, 8, 9 + index, tzinfo=UTC),
            end=datetime(2026, 7, 8, 10 + index, tzinfo=UTC),
            is_all_day=False,
        )
        for index in range(3)
    ]
    repository = CalendarRepository(make_config(tmp_path), FakeCalendarBackend(events))
    query = {
        "calendar_ids": ["Personal"],
        "start": datetime(2026, 7, 8, 8, tzinfo=UTC),
        "end": datetime(2026, 7, 8, 18, tzinfo=UTC),
        "limit": 2,
    }

    first = repository.list_events(**query)
    second = repository.list_events(**query, cursor=first.next_cursor)

    assert [event.event_id for event in first.events] == ["event-0", "event-1"]
    assert first.next_cursor is not None
    assert [event.event_id for event in second.events] == ["event-2"]
    assert second.next_cursor is None


@pytest.mark.parametrize(
    ("limit", "cursor", "message"),
    [
        (0, None, "limit must be between 1 and 200"),
        (100, "invalid-cursor", "cursor is invalid"),
    ],
)
def test_list_events_rejects_invalid_pagination_before_backend(
    tmp_path: Path,
    limit: int,
    cursor: str | None,
    message: str,
) -> None:
    backend = FakeCalendarBackend()
    repository = CalendarRepository(make_config(tmp_path), backend)

    with pytest.raises(ValueError, match=message):
        repository.list_events(
            calendar_ids=["Personal"],
            start=datetime(2026, 7, 8, 9, tzinfo=UTC),
            end=datetime(2026, 7, 8, 18, tzinfo=UTC),
            limit=limit,
            cursor=cursor,
        )

    assert backend.list_calls == []


def test_list_events_preserves_backend_local_dates_for_all_day_events(
    tmp_path: Path,
) -> None:
    event = CalendarEventRecord(
        event_id="all-day-1",
        calendar_id="Personal",
        title="All day event",
        start=datetime(2026, 7, 7, 16, tzinfo=UTC),
        end=datetime(2026, 7, 8, 16, tzinfo=UTC),
        is_all_day=True,
        start_date=date(2026, 7, 8),
        end_date=date(2026, 7, 9),
    )
    repository = CalendarRepository(
        make_config(tmp_path),
        FakeCalendarBackend([event]),
    )

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 7, 0, tzinfo=UTC),
        end=datetime(2026, 7, 10, 0, tzinfo=UTC),
    )

    assert result.events[0].time_range.model_dump() == {
        "kind": "all_day",
        "start_date": date(2026, 7, 8),
        "end_date": date(2026, 7, 9),
    }


def test_state_token_is_stable_when_sensitive_fields_are_hidden(
    tmp_path: Path,
) -> None:
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
    repository = CalendarRepository(
        make_config(tmp_path),
        FakeCalendarBackend([event]),
    )
    query = {
        "calendar_ids": ["Personal"],
        "start": datetime(2026, 7, 8, 9, tzinfo=UTC),
        "end": datetime(2026, 7, 8, 18, tzinfo=UTC),
    }

    hidden = repository.list_events(**query)
    visible = repository.list_events(
        **query,
        include_notes=True,
        include_location=True,
    )

    assert hidden.events[0].state_token == visible.events[0].state_token
    assert hidden.events[0].notes is None
    assert visible.events[0].notes == "Private notes"


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


def test_list_events_uses_current_time_semantics_for_mcp_created_events(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    sidecar.upsert_mcp_item(
        item_id="calendar_event:stable-1",
        item_type="calendar_event",
        external_id="event-1",
        external_container_id="Personal",
        title_hash="title-hash",
        time_start="2026-07-08T10:00:00+00:00",
        time_end="2026-07-08T11:00:00+00:00",
        status_semantics="planned",
        created_by_mcp=True,
    )
    sidecar.record_source_link(
        target_item_id="calendar_event:stable-1",
        source_ref="file:daily/2026-07-26.md",
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
    repository = CalendarRepository(
        make_config(tmp_path),
        FakeCalendarBackend([event]),
        sidecar,
        clock=FixedClock(datetime(2026, 7, 8, 12, tzinfo=UTC)),
    )

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, 9, tzinfo=UTC),
        end=datetime(2026, 7, 8, 18, tzinfo=UTC),
    )

    evidence = result.events[0]
    assert evidence.created_by_mcp is True
    assert evidence.status_semantics == "probable"
    assert evidence.source_refs == ["file:daily/2026-07-26.md"]


def test_list_events_merges_completion_without_overriding_time_semantics(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    sidecar.upsert_mcp_item(
        item_id="calendar_event:stable-1",
        item_type="calendar_event",
        external_id="event-1",
        external_container_id="Personal",
        title_hash="title-hash",
        time_start="2026-07-08T09:00:00+00:00",
        time_end="2026-07-08T10:00:00+00:00",
        status_semantics="planned",
        created_by_mcp=True,
    )
    sidecar.set_calendar_completion_status(
        item_id="calendar_event:stable-1",
        completion_status="completed",
    )
    event = CalendarEventRecord(
        event_id="event-1",
        calendar_id="Personal",
        title="Past event",
        start=datetime(2026, 7, 8, 9, tzinfo=UTC),
        end=datetime(2026, 7, 8, 10, tzinfo=UTC),
        is_all_day=False,
    )
    repository = CalendarRepository(
        make_config(tmp_path),
        FakeCalendarBackend([event]),
        sidecar,
        clock=FixedClock(datetime(2026, 7, 8, 12, tzinfo=UTC)),
    )

    result = repository.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, 8, tzinfo=UTC),
        end=datetime(2026, 7, 8, 18, tzinfo=UTC),
    )

    assert result.events[0].completion_status == "completed"
    assert result.events[0].status_semantics == "probable"


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
        source_refs=[" file:b ", "file:a", "file:b", ""],
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
        source_refs=["file:a", "file:b"],
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
    assert created.source_refs == ["file:a", "file:b"]
    assert repeated.source_refs == ["file:a", "file:b"]
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        source_links = connection.execute(
            "SELECT * FROM source_link ORDER BY source_ref"
        ).fetchall()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == created.stable_id
    assert item["item_type"] == "calendar_event"
    assert item["external_id"] == "created-event-1"
    assert item["external_container_id"] == "Personal"
    assert item["title_hash"] is not None
    assert "MCP demo" not in " ".join(str(value) for value in item)
    assert idempotency["key"] == "calendar:create:demo"
    assert idempotency["result_item_id"] == created.stable_id
    assert [row["target_item_id"] for row in source_links] == [
        created.stable_id,
        created.stable_id,
    ]
    assert [row["source_ref"] for row in source_links] == ["file:a", "file:b"]
    assert audit["operation"] == "calendar.create_event"
    assert audit["target_item_id"] == created.stable_id
    assert audit["result_status"] == "succeeded"


def test_create_event_does_not_retry_after_uncertain_backend_result(
    tmp_path: Path,
) -> None:
    class UncertainBackend(FakeCalendarBackend):
        def create_event(self, **kwargs) -> CalendarEventRecord:
            self.create_calls.append(kwargs)
            raise CalendarBackendError(
                "Calendar returned an unreadable result",
                external_state_changed=None,
            )

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = UncertainBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)
    request = {
        "calendar_id": "Personal",
        "title": "MCP demo",
        "start": datetime(2026, 7, 8, 10, tzinfo=UTC),
        "end": datetime(2026, 7, 8, 11, tzinfo=UTC),
        "is_all_day": False,
        "notes": None,
        "location": None,
        "timezone": "Asia/Shanghai",
        "source_refs": [],
        "idempotency_key": "calendar:create:unknown",
    }

    with pytest.raises(ToolContractError) as first_error:
        repository.create_event(**request)
    with pytest.raises(ToolContractError) as repeated_error:
        repository.create_event(**request)

    assert first_error.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert repeated_error.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert len(backend.create_calls) == 1
    with sidecar.connect() as connection:
        status = connection.execute(
            """
            SELECT status
            FROM idempotency_key
            WHERE key = ? AND operation = ?
            """,
            ("calendar:create:unknown", "calendar.create_event"),
        ).fetchone()[0]
    assert status == "external_state_unknown"


def test_create_event_can_retry_when_backend_confirms_no_external_change(
    tmp_path: Path,
) -> None:
    class FailsOnceBackend(FakeCalendarBackend):
        def create_event(self, **kwargs) -> CalendarEventRecord:
            self.create_calls.append(kwargs)
            if len(self.create_calls) == 1:
                raise CalendarBackendError(
                    "osascript could not start",
                    external_state_changed=False,
                )
            return CalendarEventRecord(
                event_id="created-event-1",
                calendar_id=str(kwargs["calendar_id"]),
                title=str(kwargs["title"]),
                start=kwargs["start"],
                end=kwargs["end"],
                is_all_day=bool(kwargs["is_all_day"]),
                notes=kwargs["notes"],
                location=kwargs["location"],
            )

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FailsOnceBackend()
    repository = CalendarRepository(make_config(tmp_path), backend, sidecar)
    request = {
        "calendar_id": "Personal",
        "title": "MCP demo",
        "start": datetime(2026, 7, 8, 10, tzinfo=UTC),
        "end": datetime(2026, 7, 8, 11, tzinfo=UTC),
        "is_all_day": False,
        "notes": None,
        "location": None,
        "timezone": "Asia/Shanghai",
        "source_refs": [],
        "idempotency_key": "calendar:create:known-failure",
    }

    with pytest.raises(CalendarBackendError):
        repository.create_event(**request)
    result = repository.create_event(**request)

    assert result.created is True
    assert len(backend.create_calls) == 2
    with sidecar.connect() as connection:
        status = connection.execute(
            """
            SELECT status
            FROM idempotency_key
            WHERE key = ? AND operation = ?
            """,
            ("calendar:create:known-failure", "calendar.create_event"),
        ).fetchone()[0]
    assert status == "succeeded"


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
            source_refs=[],
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
            source_refs=[],
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
        source_refs=[],
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
            source_refs=[],
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
        source_refs=[" file:b ", "file:a", "file:b"],
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
        source_refs=["file:a", "file:b"],
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
    assert updated.source_refs == ["file:a", "file:b"]
    assert repeated.source_refs == ["file:a", "file:b"]
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
            source_refs=[],
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
        external_container_id="Personal",
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
            source_refs=[],
            confirmed_by_user=False,
            idempotency_key="calendar:update:confirmed-action",
        )
