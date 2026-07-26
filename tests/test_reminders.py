from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_activity_mcp.config import AppConfig, ReminderSource
from personal_activity_mcp.reminders import ReminderRecord, ReminderRepository
from personal_activity_mcp.sidecar import SidecarRepository


class FakeReminderBackend:
    def __init__(self, reminders: list[ReminderRecord] | None = None) -> None:
        self.reminders = reminders or []
        self.list_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.complete_calls: list[dict[str, object]] = []
        self.next_created_reminder_id = "created-reminder-1"

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_at: datetime | None,
        end_due_at: datetime | None,
        start_completed_at: datetime | None,
        end_completed_at: datetime | None,
        include_completed: bool,
        include_notes: bool,
    ) -> list[ReminderRecord]:
        self.list_calls.append(
            {
                "list_ids": list_ids,
                "start_due_at": start_due_at,
                "end_due_at": end_due_at,
                "start_completed_at": start_completed_at,
                "end_completed_at": end_completed_at,
                "include_completed": include_completed,
                "include_notes": include_notes,
            }
        )
        return self.reminders

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> ReminderRecord:
        self.create_calls.append(
            {
                "list_id": list_id,
                "title": title,
                "notes": notes,
                "due_date": due_date,
                "priority": priority,
            }
        )
        return ReminderRecord(
            reminder_id=self.next_created_reminder_id,
            list_id=list_id,
            title=title,
            notes=notes,
            due_date=due_date,
            priority=priority,
            is_completed=False,
            completion_date=None,
        )

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_ids: list[str],
        completion_date: datetime,
    ) -> ReminderRecord:
        self.complete_calls.append(
            {
                "reminder_id": reminder_id,
                "list_ids": list_ids,
                "completion_date": completion_date,
            }
        )
        return ReminderRecord(
            reminder_id=reminder_id,
            list_id=list_ids[0],
            title="MCP todo",
            notes=None,
            due_date=date(2026, 7, 9),
            priority=5,
            is_completed=True,
            completion_date=completion_date,
        )


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        sidecar_path=tmp_path / "sidecar.sqlite3",
        reminder_sources=(
            ReminderSource("Personal", "Personal", True),
            ReminderSource("Work", "Work", False),
        ),
        default_timezone="Asia/Shanghai",
    )


def test_list_reminders_queries_only_allowed_lists_without_notes_by_default(
    tmp_path: Path,
) -> None:
    reminder = ReminderRecord(
        reminder_id="reminder-1",
        list_id="Personal",
        title="MCP todo",
        notes="Private reminder notes",
        due_date=date(2026, 7, 9),
        priority=5,
        is_completed=False,
        completion_date=None,
    )
    backend = FakeReminderBackend([reminder])
    repository = ReminderRepository(make_config(tmp_path), backend)

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=datetime(2026, 7, 8, tzinfo=UTC),
        end_due_at=datetime(2026, 7, 10, 23, 59, tzinfo=UTC),
    )

    assert backend.list_calls == [
        {
            "list_ids": ["Personal"],
            "start_due_at": datetime(2026, 7, 8, tzinfo=UTC),
            "end_due_at": datetime(2026, 7, 10, 23, 59, tzinfo=UTC),
            "start_completed_at": None,
            "end_completed_at": None,
            "include_completed": False,
            "include_notes": False,
        }
    ]
    assert len(result.reminders) == 1
    evidence = result.reminders[0]
    assert evidence.evidence_id.startswith("reminder:")
    assert evidence.source_type == "reminder"
    assert evidence.source_id == "Personal"
    assert evidence.reminder_id == "reminder-1"
    assert evidence.list_id == "Personal"
    assert evidence.target_ref.model_dump() == {
        "resource_type": "reminder",
        "item_id": "reminder-1",
        "container_id": "Personal",
    }
    assert evidence.state_token
    assert evidence.due_date == datetime(
        2026,
        7,
        9,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    assert evidence.status_semantics == "planned"
    assert evidence.created_by_mcp is False
    assert evidence.source_refs == []
    assert evidence.notes is None


def test_list_reminders_marks_completed_as_confirmed(tmp_path: Path) -> None:
    completed_at = datetime(2026, 7, 9, 12, tzinfo=UTC)
    reminder = ReminderRecord(
        reminder_id="reminder-1",
        list_id="Personal",
        title="MCP todo",
        notes=None,
        due_date=date(2026, 7, 9),
        priority=None,
        is_completed=True,
        completion_date=completed_at,
    )
    repository = ReminderRepository(make_config(tmp_path), FakeReminderBackend([reminder]))

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=None,
        end_due_at=None,
        include_completed=True,
    )

    evidence = result.reminders[0]
    assert evidence.is_completed is True
    assert evidence.completion_date == completed_at
    assert evidence.status_semantics == "confirmed"


def test_list_reminders_rejects_unconfigured_list(tmp_path: Path) -> None:
    repository = ReminderRepository(make_config(tmp_path), FakeReminderBackend())

    with pytest.raises(ValueError, match="Unknown reminder list_ids: Secret"):
        repository.list_reminders(
            list_ids=["Secret"],
            start_due_at=None,
            end_due_at=None,
        )


def test_list_reminders_excludes_items_without_due_date_from_due_range(
    tmp_path: Path,
) -> None:
    reminder = ReminderRecord(
        reminder_id="reminder-without-due",
        list_id="Personal",
        title="No due date",
        notes=None,
        due_date=None,
        priority=None,
        is_completed=False,
        completion_date=None,
    )
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend([reminder]),
    )

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=datetime(2026, 7, 8, tzinfo=UTC),
        end_due_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert result.reminders == []


def test_list_reminders_filters_by_completion_time_without_due_date(
    tmp_path: Path,
) -> None:
    inside = ReminderRecord(
        reminder_id="inside",
        list_id="Personal",
        title="Completed inside period",
        notes=None,
        due_date=None,
        priority=None,
        is_completed=True,
        completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
    )
    outside = inside.model_copy(
        update={
            "reminder_id": "outside",
            "completion_date": datetime(2026, 7, 11, 12, tzinfo=UTC),
        }
    )
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend([inside, outside]),
    )

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=None,
        end_due_at=None,
        start_completed_at=datetime(2026, 7, 9, tzinfo=UTC),
        end_completed_at=datetime(2026, 7, 9, 23, 59, tzinfo=UTC),
        include_completed=True,
    )

    assert [reminder.reminder_id for reminder in result.reminders] == ["inside"]


def test_list_reminders_excludes_backend_records_outside_selected_lists(
    tmp_path: Path,
) -> None:
    reminder = ReminderRecord(
        reminder_id="work-item",
        list_id="Work",
        title="Work item",
        notes=None,
        due_date=None,
        priority=None,
        is_completed=False,
        completion_date=None,
    )
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend([reminder]),
    )

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=None,
        end_due_at=None,
    )

    assert result.reminders == []


def test_list_reminders_deduplicates_identical_records_and_warns_on_conflicts(
    tmp_path: Path,
) -> None:
    identical = ReminderRecord(
        reminder_id="reminder-1",
        list_id="Personal",
        title="Same reminder",
        notes=None,
        due_date=datetime(2026, 7, 9, 9, tzinfo=UTC),
        priority=5,
        is_completed=False,
        completion_date=None,
    )
    conflict_a = identical.model_copy(update={"reminder_id": "reminder-2", "title": "First title"})
    conflict_b = conflict_a.model_copy(update={"title": "Conflicting title"})
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend([identical, identical.model_copy(), conflict_a, conflict_b]),
    )

    result = repository.list_reminders(
        list_ids=["Personal"],
        start_due_at=None,
        end_due_at=None,
    )

    assert [reminder.reminder_id for reminder in result.reminders] == ["reminder-1"]
    assert [warning.model_dump() for warning in result.warnings] == [
        {
            "code": "DUPLICATE_SOURCE_ITEM",
            "message": "Conflicting Reminder records share the same source identity",
            "related_item_ids": ["Personal:reminder-2"],
        }
    ]


def test_list_reminders_uses_stable_bounded_cursor_pagination(tmp_path: Path) -> None:
    reminders = [
        ReminderRecord(
            reminder_id=f"reminder-{index}",
            list_id="Personal",
            title=f"Reminder {index}",
            notes=None,
            due_date=datetime(2026, 7, 9 + index, 9, tzinfo=UTC),
            priority=None,
            is_completed=False,
            completion_date=None,
        )
        for index in range(3)
    ]
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend(reminders),
    )
    query = {
        "list_ids": ["Personal"],
        "start_due_at": None,
        "end_due_at": None,
        "limit": 2,
    }

    first = repository.list_reminders(**query)
    second = repository.list_reminders(**query, cursor=first.next_cursor)

    assert [reminder.reminder_id for reminder in first.reminders] == [
        "reminder-0",
        "reminder-1",
    ]
    assert first.next_cursor is not None
    assert [reminder.reminder_id for reminder in second.reminders] == ["reminder-2"]
    assert second.next_cursor is None


@pytest.mark.parametrize(
    ("limit", "cursor", "message"),
    [
        (201, None, "limit must be between 1 and 200"),
        (100, "invalid-cursor", "cursor is invalid"),
    ],
)
def test_list_reminders_rejects_invalid_pagination_before_backend(
    tmp_path: Path,
    limit: int,
    cursor: str | None,
    message: str,
) -> None:
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend)

    with pytest.raises(ValueError, match=message):
        repository.list_reminders(
            list_ids=["Personal"],
            start_due_at=None,
            end_due_at=None,
            limit=limit,
            cursor=cursor,
        )

    assert backend.list_calls == []


def test_create_reminder_writes_once_and_records_sidecar_metadata(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    created = repository.create_reminder(
        list_id="Personal",
        title="MCP todo",
        notes="Private reminder notes",
        due_date=date(2026, 7, 9),
        priority=5,
        source_refs=["file:daily/2026-07-26.md"],
        idempotency_key="reminder:create:demo",
    )
    repeated = repository.create_reminder(
        list_id="Personal",
        title="MCP todo",
        notes="Private reminder notes",
        due_date=date(2026, 7, 9),
        priority=5,
        source_refs=["file:daily/2026-07-26.md"],
        idempotency_key="reminder:create:demo",
    )

    assert len(backend.create_calls) == 1
    assert created.created is True
    assert created.deduplicated is False
    assert created.reminder_id == "created-reminder-1"
    assert repeated.created is False
    assert repeated.deduplicated is True
    assert repeated.reminder_id == "created-reminder-1"
    assert repeated.stable_id == created.stable_id
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        source_link = connection.execute("SELECT * FROM source_link").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == created.stable_id
    assert item["item_type"] == "reminder"
    assert item["external_id"] == "created-reminder-1"
    assert item["external_calendar_or_list_id"] == "Personal"
    assert "MCP todo" not in " ".join(str(value) for value in item)
    assert idempotency["key"] == "reminder:create:demo"
    assert idempotency["result_item_id"] == created.stable_id
    assert source_link["target_item_id"] == created.stable_id
    assert source_link["source_ref"] == "file:daily/2026-07-26.md"
    assert audit["operation"] == "reminders.create_reminder"
    assert audit["target_item_id"] == created.stable_id
    assert audit["result_status"] == "succeeded"


def test_create_reminder_rejects_list_without_write_permission(tmp_path: Path) -> None:
    repository = ReminderRepository(make_config(tmp_path), FakeReminderBackend())

    with pytest.raises(ValueError, match="Reminder list is not allowed for writes: Work"):
        repository.create_reminder(
            list_id="Work",
            title="MCP todo",
            notes=None,
            due_date=None,
            priority=None,
            source_refs=[],
            idempotency_key="reminder:create:demo",
        )


def test_create_reminder_rejects_idempotency_conflict(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    repository = ReminderRepository(make_config(tmp_path), FakeReminderBackend(), sidecar)
    repository.create_reminder(
        list_id="Personal",
        title="MCP todo",
        notes=None,
        due_date=None,
        priority=None,
        source_refs=[],
        idempotency_key="reminder:create:demo",
    )

    with pytest.raises(ValueError, match="idempotency_key conflicts with different request"):
        repository.create_reminder(
            list_id="Personal",
            title="Different todo",
            notes=None,
            due_date=None,
            priority=None,
            source_refs=[],
            idempotency_key="reminder:create:demo",
        )


def test_complete_reminder_requires_confirmation(tmp_path: Path) -> None:
    repository = ReminderRepository(make_config(tmp_path), FakeReminderBackend())

    with pytest.raises(ValueError, match="confirmed_by_user is required"):
        repository.complete_reminder(
            reminder_id="reminder-1",
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            confirmed_by_user=False,
            idempotency_key="reminder:complete:demo",
        )


def test_complete_reminder_sets_completed_status_and_records_audit(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)
    completion_date = datetime(2026, 7, 9, 12, tzinfo=UTC)

    result = repository.complete_reminder(
        reminder_id="reminder-1",
        completion_date=completion_date,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:demo",
    )

    assert backend.complete_calls == [
        {
            "reminder_id": "reminder-1",
            "list_ids": ["Personal"],
            "completion_date": completion_date,
        }
    ]
    assert result.reminder_id == "reminder-1"
    assert result.is_completed is True
    assert result.completion_date == completion_date
    assert result.status_semantics == "confirmed"
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
    assert item["item_type"] == "reminder"
    assert item["external_id"] == "reminder-1"
    assert item["status_semantics"] == "confirmed"
    assert audit["id"] == result.audit_id
    assert audit["operation"] == "reminders.complete_reminder"
    assert audit["confirmed_by_user"] == 1
    assert idempotency["key"] == "reminder:complete:demo"


def test_complete_reminder_rejects_read_only_target_before_backend(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(
        ValueError,
        match="Reminder list is not allowed for writes: Work",
    ):
        repository.complete_reminder(
            reminder_id="reminder-1",
            list_id="Work",
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            confirmed_by_user=True,
            idempotency_key="reminder:complete:read-only",
        )

    assert backend.complete_calls == []


def test_complete_reminder_rejects_naive_completion_date_before_backend(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(
        ValueError,
        match="completion_date must include timezone information",
    ):
        repository.complete_reminder(
            reminder_id="reminder-1",
            completion_date=datetime(2026, 7, 9, 12),
            confirmed_by_user=True,
            idempotency_key="reminder:complete:naive",
        )

    assert backend.complete_calls == []
