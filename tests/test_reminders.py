import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_activity_mcp.common import TargetRef, ToolContractError
from personal_activity_mcp.config import AppConfig, ReminderSource
from personal_activity_mcp.reminders import (
    ReminderBackendError,
    ReminderRecord,
    ReminderRepository,
)
from personal_activity_mcp.reminders import repository as reminder_repository
from personal_activity_mcp.sidecar import SidecarRepository


class FakeReminderBackend:
    def __init__(self, reminders: list[ReminderRecord] | None = None) -> None:
        self.reminders = reminders or []
        self.list_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, str]] = []
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
        record = ReminderRecord(
            reminder_id=self.next_created_reminder_id,
            list_id=list_id,
            title=title,
            notes=notes,
            due_date=due_date,
            priority=priority,
            is_completed=False,
            completion_date=None,
        )
        self.reminders.append(record)
        return record

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> ReminderRecord:
        self.get_calls.append(
            {
                "reminder_id": reminder_id,
                "list_id": list_id,
            }
        )
        existing = next(
            (
                reminder
                for reminder in self.reminders
                if reminder.reminder_id == reminder_id and reminder.list_id == list_id
            ),
            None,
        )
        return existing or ReminderRecord(
            reminder_id=reminder_id,
            list_id=list_id,
            title="MCP todo",
            notes=None,
            due_date=date(2026, 7, 9),
            priority=5,
            is_completed=False,
            completion_date=None,
        )

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date: datetime,
    ) -> ReminderRecord:
        self.complete_calls.append(
            {
                "reminder_id": reminder_id,
                "list_id": list_id,
                "completion_date": completion_date,
            }
        )
        current = next(
            (
                reminder
                for reminder in self.reminders
                if reminder.reminder_id == reminder_id and reminder.list_id == list_id
            ),
            ReminderRecord(
                reminder_id=reminder_id,
                list_id=list_id,
                title="MCP todo",
                notes=None,
                due_date=date(2026, 7, 9),
                priority=5,
                is_completed=False,
                completion_date=None,
            ),
        )
        completed = current.model_copy(
            update={
                "is_completed": True,
                "completion_date": completion_date,
            }
        )
        self.reminders = [
            completed
            if reminder.reminder_id == reminder_id and reminder.list_id == list_id
            else reminder
            for reminder in self.reminders
        ]
        if not any(
            reminder.reminder_id == reminder_id and reminder.list_id == list_id
            for reminder in self.reminders
        ):
            self.reminders.append(completed)
        return completed


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
        source_refs=[" file:b ", "file:a", "file:b", ""],
        idempotency_key="reminder:create:demo",
    )
    repeated = repository.create_reminder(
        list_id="Personal",
        title="MCP todo",
        notes="Private reminder notes",
        due_date=date(2026, 7, 9),
        priority=5,
        source_refs=["file:a", "file:b"],
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
    assert created.source_refs == ["file:a", "file:b"]
    assert repeated.source_refs == ["file:a", "file:b"]
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
    assert item["id"] == created.stable_id
    assert item["item_type"] == "reminder"
    assert item["external_id"] == "created-reminder-1"
    assert item["external_container_id"] == "Personal"
    assert json.loads(item["source_refs_json"]) == ["file:a", "file:b"]
    assert "MCP todo" not in " ".join(str(value) for value in item)
    assert idempotency["key"] == "reminder:create:demo"
    assert idempotency["result_item_id"] == created.stable_id
    assert idempotency["audit_id"] == audit["id"]
    assert audit["operation"] == "reminders.create_reminder"
    assert audit["target_item_id"] == created.stable_id
    assert audit["result_status"] == "succeeded"
    assert audit["confirmed_by_user"] == 0


def test_reminder_stable_id_depends_on_external_identity_not_idempotency_key(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    repository = ReminderRepository(
        make_config(tmp_path),
        FakeReminderBackend(),
        sidecar,
    )
    created = repository.create_reminder(
        list_id="Personal",
        title="Created reminder",
        notes=None,
        due_date=None,
        priority=None,
        source_refs=[],
        idempotency_key="Personal:reminder-2",
    )

    completed = repository.complete_reminder(
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-2",
            container_id="Personal",
        ),
        completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
        expected_state_token=None,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:2",
    )

    assert created.stable_id != completed.stable_id
    with sidecar.connect() as connection:
        identities = connection.execute(
            """
            SELECT external_id, external_container_id
            FROM mcp_item
            ORDER BY external_id
            """
        ).fetchall()
    assert [tuple(row) for row in identities] == [
        ("created-reminder-1", "Personal"),
        ("reminder-2", "Personal"),
    ]


def test_complete_reminder_normalizes_submillisecond_precision_before_write(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    result = repository.complete_reminder(
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-1",
            container_id="Personal",
        ),
        completion_date=datetime(2026, 7, 9, 12, 0, 0, 123456, tzinfo=UTC),
        expected_state_token=None,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:precision",
    )

    assert backend.complete_calls[0]["completion_date"].microsecond == 123000
    assert result.completion_date.microsecond == 123000


@pytest.mark.parametrize("priority", [-1, 2, 4, 10])
def test_create_reminder_rejects_priority_outside_apple_values_before_backend(
    tmp_path: Path,
    priority: int,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ValueError, match="priority must be one of"):
        repository.create_reminder(
            list_id="Personal",
            title="MCP todo",
            notes=None,
            due_date=None,
            priority=priority,
            source_refs=[],
            idempotency_key=f"reminder:create:priority:{priority}",
        )

    assert backend.create_calls == []


def test_create_reminder_marks_unverifiable_backend_result_unknown(
    tmp_path: Path,
) -> None:
    class WrongListBackend(FakeReminderBackend):
        def create_reminder(self, **kwargs) -> ReminderRecord:
            record = super().create_reminder(**kwargs)
            return record.model_copy(update={"list_id": "Work"})

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = WrongListBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ToolContractError) as unknown:
        repository.create_reminder(
            list_id="Personal",
            title="MCP todo",
            notes=None,
            due_date=date(2026, 7, 9),
            priority=5,
            source_refs=[],
            idempotency_key="reminder:create:wrong-result",
        )

    assert unknown.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert len(backend.create_calls) == 1
    with sidecar.connect() as connection:
        state = connection.execute(
            """
            SELECT status, error_code
            FROM idempotency_key
            WHERE key = ? AND operation = ?
            """,
            ("reminder:create:wrong-result", "reminders.create_reminder"),
        ).fetchone()
    assert tuple(state) == ("external_state_unknown", "EXTERNAL_STATE_UNKNOWN")


def test_create_reminder_requires_exact_post_create_reread(
    tmp_path: Path,
) -> None:
    class MutatedAfterCreateBackend(FakeReminderBackend):
        def create_reminder(self, **kwargs) -> ReminderRecord:
            record = super().create_reminder(**kwargs)
            self.reminders[-1] = record.model_copy(update={"notes": "Unexpected notes"})
            return record

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = MutatedAfterCreateBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ToolContractError) as captured:
        repository.create_reminder(
            list_id="Personal",
            title="Expected reminder",
            notes="Expected notes",
            due_date=date(2026, 7, 9),
            priority=5,
            source_refs=[],
            idempotency_key="reminder:create:reread",
        )

    assert captured.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert backend.get_calls == [{"reminder_id": "created-reminder-1", "list_id": "Personal"}]


def test_create_reminder_normalizes_empty_notes_before_hash_and_write(
    tmp_path: Path,
) -> None:
    class AppleNormalizingBackend(FakeReminderBackend):
        def create_reminder(self, **kwargs) -> ReminderRecord:
            record = super().create_reminder(**kwargs)
            normalized = record.model_copy(update={"notes": None})
            self.reminders[-1] = normalized
            return normalized

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = AppleNormalizingBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    result = repository.create_reminder(
        list_id="Personal",
        title="Empty notes",
        notes="",
        due_date=None,
        priority=None,
        source_refs=[],
        idempotency_key="reminder:create:empty-notes",
    )

    assert result.created is True
    assert backend.create_calls[0]["notes"] is None


def test_reminder_identity_hashing_is_unambiguous_when_ids_contain_colons() -> None:
    first = ("a:b", "c")
    second = ("a", "b:c")

    assert reminder_repository._reminder_evidence_id(*first) != (
        reminder_repository._reminder_evidence_id(*second)
    )
    assert reminder_repository._stable_reminder_item_id(*first) != (
        reminder_repository._stable_reminder_item_id(*second)
    )


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
    with sidecar.connect() as connection:
        blocked = connection.execute(
            """
            SELECT result_status, error_code
            FROM operation_audit
            WHERE operation = 'reminders.create_reminder'
              AND result_status = 'blocked'
            """
        ).fetchone()
    assert tuple(blocked) == ("blocked", "IDEMPOTENCY_CONFLICT")


def test_complete_reminder_requires_confirmation(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ValueError, match="confirmed_by_user is required"):
        repository.complete_reminder(
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            expected_state_token=None,
            confirmed_by_user=False,
            idempotency_key="reminder:complete:demo",
        )

    assert backend.get_calls == []
    assert backend.complete_calls == []
    with sidecar.connect() as connection:
        audit = connection.execute(
            """
            SELECT error_code, result_status
            FROM operation_audit
            WHERE operation = 'reminders.complete_reminder'
            """
        ).fetchone()
    assert tuple(audit) == ("USER_CONFIRMATION_REQUIRED", "blocked")


def test_complete_reminder_sets_completed_status_and_records_audit(tmp_path: Path) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)
    completion_date = datetime(2026, 7, 9, 12, tzinfo=UTC)

    result = repository.complete_reminder(
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-1",
            container_id="Personal",
        ),
        completion_date=completion_date,
        expected_state_token=None,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:demo",
    )
    repeated = repository.complete_reminder(
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-1",
            container_id="Personal",
        ),
        completion_date=completion_date,
        expected_state_token=None,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:demo",
    )

    assert backend.complete_calls == [
        {
            "reminder_id": "reminder-1",
            "list_id": "Personal",
            "completion_date": completion_date,
        }
    ]
    assert result.reminder_id == "reminder-1"
    assert result.is_completed is True
    assert repeated.is_completed is True
    assert result.completion_date == completion_date
    assert result.status_semantics == "confirmed"
    assert result.list_id == "Personal"
    assert result.deduplicated is False
    assert repeated.deduplicated is True
    with sidecar.connect() as connection:
        item = connection.execute("SELECT * FROM mcp_item").fetchone()
        audit = connection.execute("SELECT * FROM operation_audit").fetchone()
        idempotency = connection.execute("SELECT * FROM idempotency_key").fetchone()
    assert item["item_type"] == "reminder"
    assert item["external_id"] == "reminder-1"
    assert item["status_semantics"] == "confirmed"
    assert result.stable_id == item["id"]
    assert repeated.stable_id == item["id"]
    assert audit["id"] == result.audit_id
    assert audit["operation"] == "reminders.complete_reminder"
    assert audit["confirmed_by_user"] == 1
    assert idempotency["key"] == "reminder:complete:demo"


def test_complete_reminder_reuses_already_completed_external_state_without_write(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    actual_completion = datetime(2026, 7, 9, 11, tzinfo=UTC)
    backend = FakeReminderBackend(
        [
            ReminderRecord(
                reminder_id="reminder-1",
                list_id="Personal",
                title="MCP todo",
                notes=None,
                due_date=date(2026, 7, 9),
                priority=5,
                is_completed=True,
                completion_date=actual_completion,
            )
        ]
    )
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    result = repository.complete_reminder(
        target_ref=TargetRef(
            resource_type="reminder",
            item_id="reminder-1",
            container_id="Personal",
        ),
        completion_date=actual_completion,
        expected_state_token=None,
        confirmed_by_user=True,
        idempotency_key="reminder:complete:already-completed",
    )

    assert backend.complete_calls == []
    assert result.is_completed is True
    assert result.completion_date == actual_completion


def test_already_completed_reminder_finalization_failure_is_retryable_local_error(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    completion_date = datetime(2026, 7, 9, 11, tzinfo=UTC)
    backend = FakeReminderBackend(
        [
            ReminderRecord(
                reminder_id="reminder-1",
                list_id="Personal",
                title="MCP todo",
                notes=None,
                due_date=None,
                priority=0,
                is_completed=True,
                completion_date=completion_date,
            )
        ]
    )
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)
    with sidecar.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_success_status
            BEFORE UPDATE OF status ON idempotency_key
            WHEN NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'injected finalization failure');
            END
            """
        )

    with pytest.raises(ToolContractError) as captured:
        repository.complete_reminder(
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=completion_date,
            expected_state_token=None,
            confirmed_by_user=True,
            idempotency_key="reminder:complete:local-fault",
        )

    assert captured.value.code == "LOCAL_PERSISTENCE_FAILURE"
    assert captured.value.retryable is True
    assert backend.complete_calls == []


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
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Work",
            ),
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            expected_state_token=None,
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
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=datetime(2026, 7, 9, 12),
            expected_state_token=None,
            confirmed_by_user=True,
            idempotency_key="reminder:complete:naive",
        )

    assert backend.complete_calls == []


def test_complete_reminder_rejects_stale_state_token_before_backend_write(
    tmp_path: Path,
) -> None:
    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = FakeReminderBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ToolContractError) as changed:
        repository.complete_reminder(
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            expected_state_token="reminder-state:stale",
            confirmed_by_user=True,
            idempotency_key="reminder:complete:stale",
        )

    assert changed.value.code == "EXTERNAL_STATE_CHANGED"
    assert len(backend.get_calls) == 1
    assert backend.complete_calls == []


def test_complete_reminder_get_failure_is_retryable_failed_state_not_unknown(
    tmp_path: Path,
) -> None:
    class GetFailureBackend(FakeReminderBackend):
        def get_reminder(self, **kwargs) -> ReminderRecord:
            self.get_calls.append(kwargs)
            raise ReminderBackendError("Reminders unavailable")

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = GetFailureBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ReminderBackendError):
        repository.complete_reminder(
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            expected_state_token=None,
            confirmed_by_user=True,
            idempotency_key="reminder:complete:get-failure",
        )

    assert backend.complete_calls == []
    with sidecar.connect() as connection:
        state = connection.execute(
            """
            SELECT status, error_code
            FROM idempotency_key
            WHERE key = 'reminder:complete:get-failure'
            """
        ).fetchone()
    assert tuple(state) == ("failed", "BACKEND_FAILURE")


def test_complete_reminder_marks_wrong_backend_result_unknown(
    tmp_path: Path,
) -> None:
    class UnverifiedCompletionBackend(FakeReminderBackend):
        def complete_reminder(self, **kwargs) -> ReminderRecord:
            record = super().complete_reminder(**kwargs)
            return record.model_copy(update={"is_completed": False})

    sidecar = SidecarRepository(tmp_path / "sidecar.sqlite3")
    sidecar.initialize()
    backend = UnverifiedCompletionBackend()
    repository = ReminderRepository(make_config(tmp_path), backend, sidecar)

    with pytest.raises(ToolContractError) as unknown:
        repository.complete_reminder(
            target_ref=TargetRef(
                resource_type="reminder",
                item_id="reminder-1",
                container_id="Personal",
            ),
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
            expected_state_token=None,
            confirmed_by_user=True,
            idempotency_key="reminder:complete:unverified",
        )

    assert unknown.value.code == "EXTERNAL_STATE_UNKNOWN"
    assert len(backend.complete_calls) == 1
