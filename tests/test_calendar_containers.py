from __future__ import annotations

from pathlib import Path

import pytest

from personal_activity_mcp.calendar import (
    CalendarBackendError,
    CalendarContainerRecord,
    CalendarContainerRepository,
)
from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.config import AppConfig, EventKitSource
from personal_activity_mcp.sidecar import SidecarRepository


class FakeCalendarContainerBackend:
    def __init__(self, records: list[CalendarContainerRecord] | None = None) -> None:
        self.records = {record.calendar_id: record for record in records or []}
        self.list_calls: list[list[str]] = []
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.create_error: Exception | None = None
        self.update_error: Exception | None = None
        self.malformed_create_readback = False
        self.malformed_update_readback = False

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[CalendarContainerRecord]:
        self.list_calls.append(source_ids)
        return list(self.records.values())

    def get_calendar(self, *, calendar_id: str) -> CalendarContainerRecord:
        record = self.records[calendar_id]
        if self.malformed_create_readback and calendar_id.startswith("created-"):
            return record.model_copy(update={"title": "unexpected"})
        if self.malformed_update_readback:
            return record.model_copy(update={"color": "#000000"})
        return record

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> CalendarContainerRecord:
        self.create_calls.append(
            {
                "source_id": source_id,
                "title": title,
                "color": color,
            }
        )
        if self.create_error is not None:
            raise self.create_error
        record = calendar_record(
            calendar_id=f"created-{len(self.create_calls)}",
            source_id=source_id,
            title=title,
            color=color,
        )
        self.records[record.calendar_id] = record
        return record

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> CalendarContainerRecord:
        self.update_calls.append(
            {
                "calendar_id": calendar_id,
                "title": title,
                "color": color,
            }
        )
        if self.update_error is not None:
            raise self.update_error
        current = self.records[calendar_id]
        record = current.model_copy(
            update={
                "title": title if title is not None else current.title,
                "color": color if color is not None else current.color,
            }
        )
        self.records[calendar_id] = record
        return record


def calendar_record(
    *,
    calendar_id: str = "calendar-1",
    source_id: str = "source-icloud",
    title: str = "Plan",
    color: str | None = "#3366CC",
    allows_content_modifications: bool = True,
    is_immutable: bool = False,
) -> CalendarContainerRecord:
    return CalendarContainerRecord(
        calendar_id=calendar_id,
        source_id=source_id,
        source_title="iCloud",
        title=title,
        color=color,
        calendar_type="caldav",
        allows_content_modifications=allows_content_modifications,
        is_immutable=is_immutable,
        is_subscribed=False,
    )


def eventkit_source(
    source_id: str,
    *,
    writable: bool = True,
    default: bool = False,
) -> EventKitSource:
    return EventKitSource(
        source_id=source_id,
        allow_calendar_write=writable,
        default_calendar_source=default,
    )


def make_repository(
    tmp_path: Path,
    backend: FakeCalendarContainerBackend,
    *sources: EventKitSource,
) -> CalendarContainerRepository:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    return CalendarContainerRepository(
        AppConfig(
            sidecar_path=tmp_path / "sidecar.sqlite3",
            eventkit_sources=tuple(sources),
        ),
        backend,
        sidecar,
    )


def test_list_calendars_filters_backend_leaks_sorts_and_paginates(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend(
        [
            calendar_record(
                calendar_id="calendar-2",
                title="Japanese Plan B",
            ),
            calendar_record(
                calendar_id="calendar-1",
                title="Japanese Plan A",
            ),
            calendar_record(
                calendar_id="calendar-other",
                source_id="source-unauthorized",
                title="Japanese Plan C",
            ),
            calendar_record(
                calendar_id="calendar-read-only",
                title="Japanese Plan Read Only",
                allows_content_modifications=False,
            ),
        ]
    )
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    first = repository.list_calendars(
        source_ids=None,
        title_query="japanese plan",
        modifiable_only=True,
        limit=1,
        cursor=None,
    )
    second = repository.list_calendars(
        source_ids=None,
        title_query="japanese plan",
        modifiable_only=True,
        limit=10,
        cursor=first.next_cursor,
    )

    assert backend.list_calls == [["source-icloud"], ["source-icloud"]]
    assert [item.calendar_id for item in first.calendars] == ["calendar-1"]
    assert [item.calendar_id for item in second.calendars] == ["calendar-2"]
    assert first.next_cursor is not None
    assert second.next_cursor is None


def test_list_calendars_rejects_unconfigured_source_before_backend(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend()
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ValueError, match="Unknown EventKit source_ids: source-other"):
        repository.list_calendars(
            source_ids=["source-other"],
            title_query=None,
            modifiable_only=False,
            limit=100,
            cursor=None,
        )

    assert backend.list_calls == []


def test_create_calendar_uses_default_source_and_deduplicates_without_second_write(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend()
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    created = repository.create_calendar(
        title=" Japanese Plan ",
        source_id=None,
        color="#3366cc",
        idempotency_key="calendar-create-1",
    )
    deduplicated = repository.create_calendar(
        title=" Japanese Plan ",
        source_id=None,
        color="#3366cc",
        idempotency_key="calendar-create-1",
    )

    assert backend.create_calls == [
        {
            "source_id": "source-icloud",
            "title": "Japanese Plan",
            "color": "#3366CC",
        }
    ]
    assert created.created is True
    assert created.deduplicated is False
    assert created.calendar.source_id == "source-icloud"
    assert created.calendar.created_by_mcp is True
    assert created.calendar.state_token.startswith("calendar-container-state:")
    assert created.audit_id is not None
    assert deduplicated.created is False
    assert deduplicated.deduplicated is True
    assert deduplicated.calendar.calendar_id == created.calendar.calendar_id
    assert deduplicated.audit_id == created.audit_id


def test_create_calendar_requires_source_when_multiple_writable_sources_have_no_default(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend()
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
        eventkit_source("source-local"),
    )

    with pytest.raises(ValueError, match="source_id is required"):
        repository.create_calendar(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="calendar-create-1",
        )

    assert backend.create_calls == []


def test_create_calendar_marks_unverified_readback_unknown_and_blocks_replay(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend()
    backend.malformed_create_readback = True
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.create_calendar(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="calendar-create-unknown",
        )
    with pytest.raises(ToolContractError):
        repository.create_calendar(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="calendar-create-unknown",
        )

    assert captured.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert len(backend.create_calls) == 1


def test_create_calendar_preserves_backend_unknown_write_state(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend()
    backend.create_error = CalendarBackendError(
        "save outcome unknown",
        external_state_changed=None,
    )
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.create_calendar(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="calendar-create-backend-unknown",
        )

    assert captured.value.code == "EXTERNAL_STATE_UNKNOWN"


def test_update_calendar_rejects_stale_state_token_before_write(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend([calendar_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )
    listed = repository.list_calendars(
        source_ids=None,
        title_query=None,
        modifiable_only=False,
        limit=100,
        cursor=None,
    )
    backend.records["calendar-1"] = calendar_record(title="Changed elsewhere")

    with pytest.raises(ToolContractError) as captured:
        repository.update_calendar(
            calendar_id="calendar-1",
            title="Agent update",
            color=None,
            expected_state_token=listed.calendars[0].state_token,
            idempotency_key="calendar-update-stale",
        )

    assert captured.value.code == "EXTERNAL_STATE_CHANGED"
    assert backend.update_calls == []


def test_update_calendar_rejects_read_only_target_before_write(tmp_path: Path) -> None:
    backend = FakeCalendarContainerBackend(
        [
            calendar_record(
                allows_content_modifications=False,
            )
        ]
    )
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.update_calendar(
            calendar_id="calendar-1",
            title="Agent update",
            color=None,
            expected_state_token=None,
            idempotency_key="calendar-update-read-only",
        )

    assert captured.value.code == "TARGET_READ_ONLY"
    assert backend.update_calls == []


def test_update_calendar_writes_requested_fields_and_deduplicates(
    tmp_path: Path,
) -> None:
    backend = FakeCalendarContainerBackend([calendar_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    updated = repository.update_calendar(
        calendar_id="calendar-1",
        title="New title",
        color="#112233",
        expected_state_token=None,
        idempotency_key="calendar-update-1",
    )
    deduplicated = repository.update_calendar(
        calendar_id="calendar-1",
        title="New title",
        color="#112233",
        expected_state_token=None,
        idempotency_key="calendar-update-1",
    )

    assert backend.update_calls == [
        {
            "calendar_id": "calendar-1",
            "title": "New title",
            "color": "#112233",
        }
    ]
    assert updated.updated is True
    assert updated.deduplicated is False
    assert updated.updated_fields == ["title", "color"]
    assert updated.calendar.title == "New title"
    assert deduplicated.updated is False
    assert deduplicated.deduplicated is True
    assert deduplicated.audit_id == updated.audit_id


def test_update_calendar_rejects_empty_patch_before_backend(tmp_path: Path) -> None:
    backend = FakeCalendarContainerBackend([calendar_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ValueError, match="At least one update field is required"):
        repository.update_calendar(
            calendar_id="calendar-1",
            title=None,
            color=None,
            expected_state_token=None,
            idempotency_key="calendar-update-empty",
        )

    assert backend.update_calls == []
