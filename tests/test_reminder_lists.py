from __future__ import annotations

from pathlib import Path

import pytest

from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.config import AppConfig, EventKitSource
from personal_activity_mcp.reminders import (
    ReminderBackendError,
    ReminderListContainerRecord,
    ReminderListRepository,
)
from personal_activity_mcp.sidecar import SidecarRepository


class FakeReminderListBackend:
    def __init__(self, records: list[ReminderListContainerRecord] | None = None) -> None:
        self.records = {record.list_id: record for record in records or []}
        self.list_calls: list[list[str]] = []
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.create_error: Exception | None = None
        self.malformed_create_readback = False

    def list_reminder_lists(
        self,
        *,
        source_ids: list[str],
    ) -> list[ReminderListContainerRecord]:
        self.list_calls.append(source_ids)
        return list(self.records.values())

    def get_reminder_list(self, *, list_id: str) -> ReminderListContainerRecord:
        record = self.records[list_id]
        if self.malformed_create_readback and list_id.startswith("created-"):
            return record.model_copy(update={"title": "unexpected"})
        return record

    def create_reminder_list(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> ReminderListContainerRecord:
        self.create_calls.append(
            {
                "source_id": source_id,
                "title": title,
                "color": color,
            }
        )
        if self.create_error is not None:
            raise self.create_error
        record = reminder_list_record(
            list_id=f"created-{len(self.create_calls)}",
            source_id=source_id,
            title=title,
            color=color,
        )
        self.records[record.list_id] = record
        return record

    def update_reminder_list(
        self,
        *,
        list_id: str,
        title: str | None,
        color: str | None,
    ) -> ReminderListContainerRecord:
        self.update_calls.append(
            {
                "list_id": list_id,
                "title": title,
                "color": color,
            }
        )
        current = self.records[list_id]
        record = current.model_copy(
            update={
                "title": title if title is not None else current.title,
                "color": color if color is not None else current.color,
            }
        )
        self.records[list_id] = record
        return record


def reminder_list_record(
    *,
    list_id: str = "list-1",
    source_id: str = "source-icloud",
    title: str = "Plan",
    color: str | None = "#3366CC",
    allows_content_modifications: bool = True,
    is_immutable: bool = False,
) -> ReminderListContainerRecord:
    return ReminderListContainerRecord(
        list_id=list_id,
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
        allow_calendar_write=False,
        default_calendar_source=False,
        allow_reminder_write=writable,
        default_reminder_source=default,
    )


def make_repository(
    tmp_path: Path,
    backend: FakeReminderListBackend,
    *sources: EventKitSource,
) -> ReminderListRepository:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    return ReminderListRepository(
        AppConfig(
            sidecar_path=tmp_path / "sidecar.sqlite3",
            eventkit_sources=tuple(sources),
        ),
        backend,
        sidecar,
    )


def test_list_reminder_lists_filters_leaks_sorts_and_paginates(tmp_path: Path) -> None:
    backend = FakeReminderListBackend(
        [
            reminder_list_record(list_id="list-2", title="Japanese Plan B"),
            reminder_list_record(list_id="list-1", title="Japanese Plan A"),
            reminder_list_record(
                list_id="list-other",
                source_id="source-unauthorized",
                title="Japanese Plan C",
            ),
            reminder_list_record(
                list_id="list-read-only",
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

    first = repository.list_lists(
        source_ids=None,
        title_query="japanese plan",
        modifiable_only=True,
        limit=1,
        cursor=None,
    )
    second = repository.list_lists(
        source_ids=None,
        title_query="japanese plan",
        modifiable_only=True,
        limit=10,
        cursor=first.next_cursor,
    )

    assert backend.list_calls == [["source-icloud"], ["source-icloud"]]
    assert [item.list_id for item in first.lists] == ["list-1"]
    assert [item.list_id for item in second.lists] == ["list-2"]
    assert first.next_cursor is not None
    assert second.next_cursor is None


def test_list_reminder_lists_rejects_unconfigured_source_before_backend(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend()
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ValueError, match="Unknown EventKit source_ids: source-other"):
        repository.list_lists(
            source_ids=["source-other"],
            title_query=None,
            modifiable_only=False,
            limit=100,
            cursor=None,
        )

    assert backend.list_calls == []


def test_create_reminder_list_uses_default_and_deduplicates(tmp_path: Path) -> None:
    backend = FakeReminderListBackend()
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    created = repository.create_list(
        title=" Japanese Plan ",
        source_id=None,
        color="#3366cc",
        idempotency_key="list-create-1",
    )
    deduplicated = repository.create_list(
        title=" Japanese Plan ",
        source_id=None,
        color="#3366cc",
        idempotency_key="list-create-1",
    )

    assert backend.create_calls == [
        {
            "source_id": "source-icloud",
            "title": "Japanese Plan",
            "color": "#3366CC",
        }
    ]
    assert created.created is True
    assert created.list.created_by_mcp is True
    assert created.list.state_token.startswith("reminder-list-state:")
    assert deduplicated.created is False
    assert deduplicated.deduplicated is True
    assert deduplicated.list.list_id == created.list.list_id
    assert deduplicated.audit_id == created.audit_id


def test_create_reminder_list_marks_bad_readback_unknown_and_blocks_replay(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend()
    backend.malformed_create_readback = True
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.create_list(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="list-create-unknown",
        )
    with pytest.raises(ToolContractError):
        repository.create_list(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="list-create-unknown",
        )

    assert captured.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert len(backend.create_calls) == 1


def test_create_reminder_list_preserves_backend_unknown_write_state(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend()
    backend.create_error = ReminderBackendError(
        "save outcome unknown",
        external_state_changed=None,
    )
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud", default=True),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.create_list(
            title="Plan",
            source_id=None,
            color=None,
            idempotency_key="list-create-backend-unknown",
        )

    assert captured.value.code == "EXTERNAL_STATE_UNKNOWN"


def test_update_reminder_list_rejects_stale_state_token_before_write(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend([reminder_list_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )
    listed = repository.list_lists(
        source_ids=None,
        title_query=None,
        modifiable_only=False,
        limit=100,
        cursor=None,
    )
    backend.records["list-1"] = reminder_list_record(title="Changed elsewhere")

    with pytest.raises(ToolContractError) as captured:
        repository.update_list(
            list_id="list-1",
            title="Agent update",
            color=None,
            expected_state_token=listed.lists[0].state_token,
            idempotency_key="list-update-stale",
        )

    assert captured.value.code == "EXTERNAL_STATE_CHANGED"
    assert backend.update_calls == []


def test_update_reminder_list_rejects_read_only_target_before_write(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend([reminder_list_record(allows_content_modifications=False)])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ToolContractError) as captured:
        repository.update_list(
            list_id="list-1",
            title="Agent update",
            color=None,
            expected_state_token=None,
            idempotency_key="list-update-read-only",
        )

    assert captured.value.code == "TARGET_READ_ONLY"
    assert backend.update_calls == []


def test_update_reminder_list_writes_requested_fields_and_deduplicates(
    tmp_path: Path,
) -> None:
    backend = FakeReminderListBackend([reminder_list_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    updated = repository.update_list(
        list_id="list-1",
        title="New title",
        color="#112233",
        expected_state_token=None,
        idempotency_key="list-update-1",
    )
    deduplicated = repository.update_list(
        list_id="list-1",
        title="New title",
        color="#112233",
        expected_state_token=None,
        idempotency_key="list-update-1",
    )

    assert backend.update_calls == [
        {
            "list_id": "list-1",
            "title": "New title",
            "color": "#112233",
        }
    ]
    assert updated.updated is True
    assert updated.updated_fields == ["title", "color"]
    assert updated.list.title == "New title"
    assert deduplicated.updated is False
    assert deduplicated.deduplicated is True
    assert deduplicated.audit_id == updated.audit_id


def test_update_reminder_list_rejects_empty_patch_before_backend(tmp_path: Path) -> None:
    backend = FakeReminderListBackend([reminder_list_record()])
    repository = make_repository(
        tmp_path,
        backend,
        eventkit_source("source-icloud"),
    )

    with pytest.raises(ValueError, match="At least one update field is required"):
        repository.update_list(
            list_id="list-1",
            title=None,
            color=None,
            expected_state_token=None,
            idempotency_key="list-update-empty",
        )

    assert backend.update_calls == []
