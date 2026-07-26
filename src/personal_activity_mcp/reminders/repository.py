"""Reminder repository with allowlist and sidecar semantics."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from personal_activity_mcp.common import (
    TargetRef,
    ToolWarning,
    decode_cursor,
    paginate,
    validate_limit,
)
from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.reminders.models import (
    ReminderCompleteResult,
    ReminderCreateResult,
    ReminderEvidence,
    ReminderListResult,
    ReminderRecord,
    ReminderTimeRange,
)
from personal_activity_mcp.sidecar import ExternalItemContext, SidecarRepository
from personal_activity_mcp.time_policy import require_aware_datetime


class ReminderBackend(Protocol):
    """Backend capable of reading Apple Reminders."""

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
    ) -> list[ReminderRecord]: ...

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> ReminderRecord: ...

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_ids: list[str],
        completion_date: datetime,
    ) -> ReminderRecord: ...


class ReminderRepository:
    """Read and write configured Apple Reminders lists."""

    def __init__(
        self,
        config: AppConfig,
        backend: ReminderBackend,
        sidecar: SidecarRepository | None = None,
    ) -> None:
        self._reminder_sources = {source.list_id: source for source in config.reminder_sources}
        self._default_timezone = ZoneInfo(config.default_timezone)
        self._backend = backend
        self._sidecar = sidecar

    def list_reminders(
        self,
        *,
        list_ids: list[str] | None,
        start_due_at: datetime | None,
        end_due_at: datetime | None,
        start_completed_at: datetime | None = None,
        end_completed_at: datetime | None = None,
        include_completed: bool = False,
        include_notes: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ReminderListResult:
        """List reminder evidence from explicitly configured lists."""
        _validate_optional_range(start_due_at, end_due_at, "due")
        _validate_optional_range(start_completed_at, end_completed_at, "completion")
        validate_limit(limit)
        if cursor is not None:
            decode_cursor(cursor)
        selected_list_ids = self._select_list_ids(list_ids)
        records = self._backend.list_reminders(
            list_ids=selected_list_ids,
            start_due_at=start_due_at,
            end_due_at=end_due_at,
            start_completed_at=start_completed_at,
            end_completed_at=end_completed_at,
            include_completed=include_completed,
            include_notes=include_notes,
        )
        records = [
            record
            for record in records
            if record.list_id in set(selected_list_ids)
            and _record_matches_query(
                record,
                timezone=self._default_timezone,
                start_due_at=start_due_at,
                end_due_at=end_due_at,
                start_completed_at=start_completed_at,
                end_completed_at=end_completed_at,
                include_completed=include_completed,
            )
        ]
        records, warnings = _deduplicate_records(records)
        sidecar_contexts = self._list_sidecar_contexts(records)
        reminders = [
            self._to_evidence(
                record,
                include_notes=include_notes,
                sidecar_contexts=sidecar_contexts,
            )
            for record in records
        ]
        reminders.sort(key=_reminder_sort_key)
        page, next_cursor = paginate(
            reminders,
            key=_reminder_page_key,
            limit=limit,
            cursor=cursor,
        )
        return ReminderListResult(
            reminders=page,
            warnings=warnings,
            next_cursor=next_cursor,
        )

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
        source_refs: list[str],
        idempotency_key: str,
    ) -> ReminderCreateResult:
        """Create a Reminder with allowlist, idempotency, and audit controls."""
        source = self._reminder_sources.get(list_id)
        if source is None:
            raise ValueError(f"Unknown reminder list_ids: {list_id}")
        if not source.allow_write:
            raise ValueError(f"Reminder list is not allowed for writes: {list_id}")
        if self._sidecar is None:
            raise ValueError("sidecar is required for reminders.create_reminder")
        if not title.strip():
            raise ValueError("title must be a non-empty string")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")

        request_hash = _request_hash(
            {
                "list_id": list_id,
                "title": title,
                "notes": notes,
                "due_date": due_date.isoformat() if due_date else None,
                "priority": priority,
                "source_refs": source_refs,
            }
        )
        decision = self._sidecar.check_idempotency_key(
            key=idempotency_key,
            operation="reminders.create_reminder",
            request_hash=request_hash,
        )
        if decision.decision == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.decision == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            return ReminderCreateResult(
                reminder_id=str(item["external_id"]),
                list_id=str(item["external_calendar_or_list_id"]),
                stable_id=str(item["id"]),
                created=False,
                deduplicated=True,
                status_semantics="planned",
                source_refs=source_refs,
            )

        record = self._backend.create_reminder(
            list_id=list_id,
            title=title,
            notes=notes,
            due_date=due_date,
            priority=priority,
        )
        stable_id = _stable_reminder_item_id(idempotency_key)
        self._sidecar.upsert_mcp_item(
            item_id=stable_id,
            item_type="reminder",
            external_id=record.reminder_id,
            external_calendar_or_list_id=list_id,
            title_hash=_request_hash({"title": title}),
            time_start=due_date.isoformat() if due_date else None,
            time_end=due_date.isoformat() if due_date else None,
            status_semantics="planned",
            created_by_mcp=True,
        )
        for source_ref in source_refs:
            self._sidecar.record_source_link(
                target_item_id=stable_id,
                source_ref=source_ref,
                relation_type="created_from",
            )
        self._sidecar.record_idempotency_success(
            key=idempotency_key,
            operation="reminders.create_reminder",
            request_hash=request_hash,
            result_item_id=stable_id,
        )
        self._sidecar.record_operation_audit(
            operation="reminders.create_reminder",
            target_item_id=stable_id,
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=True,
        )
        return ReminderCreateResult(
            reminder_id=record.reminder_id,
            list_id=list_id,
            stable_id=stable_id,
            created=True,
            deduplicated=False,
            status_semantics="planned",
            source_refs=source_refs,
        )

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        completion_date: datetime,
        confirmed_by_user: bool,
        idempotency_key: str,
        list_id: str | None = None,
    ) -> ReminderCompleteResult:
        """Mark an allowed Reminder as completed."""
        if not confirmed_by_user:
            raise ValueError("confirmed_by_user is required")
        require_aware_datetime(completion_date, "completion_date")
        list_ids = self._select_write_list_ids(list_id)
        if self._sidecar is None:
            raise ValueError("sidecar is required for reminders.complete_reminder")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")

        request_payload: dict[str, object] = {
            "reminder_id": reminder_id,
            "completion_date": completion_date.isoformat(),
            "confirmed_by_user": confirmed_by_user,
        }
        if list_id is not None:
            request_payload["list_id"] = list_id
        request_hash = _request_hash(request_payload)
        decision = self._sidecar.check_idempotency_key(
            key=idempotency_key,
            operation="reminders.complete_reminder",
            request_hash=request_hash,
        )
        if decision.decision == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.decision == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            audit_id = self._sidecar.record_operation_audit(
                operation="reminders.complete_reminder",
                target_item_id=str(item["id"]),
                request_hash=request_hash,
                result_status="deduplicated",
                error_code=None,
                confirmed_by_user=True,
            )
            return ReminderCompleteResult(
                reminder_id=str(item["external_id"]),
                is_completed=True,
                completion_date=completion_date,
                status_semantics="confirmed",
                audit_id=audit_id,
            )

        record = self._backend.complete_reminder(
            reminder_id=reminder_id,
            list_ids=list_ids,
            completion_date=completion_date,
        )
        if record.list_id not in list_ids:
            raise ValueError(f"Reminder is not in a write-enabled list: {record.list_id}")
        stable_id = _stable_completed_reminder_item_id(record.list_id, reminder_id)
        self._sidecar.upsert_mcp_item(
            item_id=stable_id,
            item_type="reminder",
            external_id=record.reminder_id,
            external_calendar_or_list_id=record.list_id,
            title_hash=_request_hash({"title": record.title}),
            time_start=record.due_date.isoformat() if record.due_date else None,
            time_end=record.due_date.isoformat() if record.due_date else None,
            status_semantics="confirmed",
            created_by_mcp=False,
        )
        self._sidecar.record_idempotency_success(
            key=idempotency_key,
            operation="reminders.complete_reminder",
            request_hash=request_hash,
            result_item_id=stable_id,
        )
        audit_id = self._sidecar.record_operation_audit(
            operation="reminders.complete_reminder",
            target_item_id=stable_id,
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=True,
        )
        return ReminderCompleteResult(
            reminder_id=record.reminder_id,
            is_completed=True,
            completion_date=record.completion_date or completion_date,
            status_semantics="confirmed",
            audit_id=audit_id,
        )

    def _select_write_list_ids(self, list_id: str | None) -> list[str]:
        if list_id is not None:
            source = self._reminder_sources.get(list_id)
            if source is None:
                raise ValueError(f"Unknown reminder list_ids: {list_id}")
            if not source.allow_write:
                raise ValueError(f"Reminder list is not allowed for writes: {list_id}")
            return [list_id]

        list_ids = [
            source.list_id for source in self._reminder_sources.values() if source.allow_write
        ]
        if not list_ids:
            raise ValueError("No reminder lists are allowed for writes")
        return list_ids

    def _select_list_ids(self, list_ids: list[str] | None) -> list[str]:
        if list_ids is None:
            return list(self._reminder_sources)
        unknown = sorted(set(list_ids) - self._reminder_sources.keys())
        if unknown:
            raise ValueError(f"Unknown reminder list_ids: {', '.join(unknown)}")
        return list_ids

    def _to_evidence(
        self,
        record: ReminderRecord,
        *,
        include_notes: bool,
        sidecar_contexts: dict[tuple[str, str, str], ExternalItemContext],
    ) -> ReminderEvidence:
        due_at = _normalize_due_at(record.due_date, self._default_timezone)
        context = sidecar_contexts.get(("reminder", record.reminder_id, record.list_id))
        sidecar_item = context.item if context is not None else None
        source_refs = list(context.source_refs) if context is not None else []
        return ReminderEvidence(
            evidence_id=_reminder_evidence_id(record.list_id, record.reminder_id),
            source_id=record.list_id,
            time_range=ReminderTimeRange(start=due_at, end=due_at),
            target_ref=TargetRef(
                resource_type="reminder",
                item_id=record.reminder_id,
                container_id=record.list_id,
            ),
            state_token=_reminder_state_token(
                record,
                due_at=due_at,
            ),
            title=record.title,
            reminder_id=record.reminder_id,
            list_id=record.list_id,
            notes=record.notes if include_notes else None,
            due_date=due_at,
            priority=record.priority,
            is_completed=record.is_completed,
            completion_date=record.completion_date,
            created_by_mcp=bool(sidecar_item and sidecar_item["created_by_mcp"]),
            status_semantics="confirmed" if record.is_completed else "planned",
            source_refs=source_refs,
        )

    def _list_sidecar_contexts(
        self,
        records: list[ReminderRecord],
    ) -> dict[tuple[str, str, str], ExternalItemContext]:
        if self._sidecar is None:
            return {}
        return self._sidecar.list_external_item_contexts(
            item_types=("reminder",),
            targets=[(record.reminder_id, record.list_id) for record in records],
        )


def _validate_optional_range(
    start: datetime | None,
    end: datetime | None,
    field_name: str,
) -> None:
    if start is not None:
        require_aware_datetime(start, f"start_{field_name}_at")
    if end is not None:
        require_aware_datetime(end, f"end_{field_name}_at")
    if start is not None and end is not None and start > end:
        raise ValueError(f"start_{field_name}_at must be on or before end_{field_name}_at")


def _normalize_due_at(
    value: datetime | date | None,
    timezone: ZoneInfo,
) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        require_aware_datetime(value, "record.due_date")
        return value
    return datetime.combine(value, time.min, tzinfo=timezone)


def _record_matches_query(
    record: ReminderRecord,
    *,
    timezone: ZoneInfo,
    start_due_at: datetime | None,
    end_due_at: datetime | None,
    start_completed_at: datetime | None,
    end_completed_at: datetime | None,
    include_completed: bool,
) -> bool:
    if record.is_completed and not include_completed:
        return False

    due_at = _normalize_due_at(record.due_date, timezone)
    if start_due_at is not None or end_due_at is not None:
        if due_at is None:
            return False
        if start_due_at is not None and due_at < start_due_at:
            return False
        if end_due_at is not None and due_at > end_due_at:
            return False

    completed_at = record.completion_date
    if start_completed_at is not None or end_completed_at is not None:
        if completed_at is None:
            return False
        require_aware_datetime(completed_at, "record.completion_date")
        if start_completed_at is not None and completed_at < start_completed_at:
            return False
        if end_completed_at is not None and completed_at > end_completed_at:
            return False
    return True


def _deduplicate_records(
    records: list[ReminderRecord],
) -> tuple[list[ReminderRecord], list[ToolWarning]]:
    grouped: dict[tuple[str, str], list[ReminderRecord]] = {}
    for record in records:
        grouped.setdefault((record.list_id, record.reminder_id), []).append(record)

    unique_records: list[ReminderRecord] = []
    warnings: list[ToolWarning] = []
    for (list_id, reminder_id), matches in grouped.items():
        first = matches[0]
        if all(match == first for match in matches[1:]):
            unique_records.append(first)
            continue
        warnings.append(
            ToolWarning(
                code="DUPLICATE_SOURCE_ITEM",
                message="Conflicting Reminder records share the same source identity",
                related_item_ids=[f"{list_id}:{reminder_id}"],
            )
        )
    return unique_records, warnings


def _reminder_state_token(
    record: ReminderRecord,
    *,
    due_at: datetime | None,
) -> str:
    payload = {
        "list_id": record.list_id,
        "reminder_id": record.reminder_id,
        "title": record.title,
        "notes": record.notes,
        "due_at": due_at.isoformat() if due_at else None,
        "priority": record.priority,
        "is_completed": record.is_completed,
        "completion_date": record.completion_date.isoformat() if record.completion_date else None,
    }
    return f"reminder-state:{_request_hash(payload)}"


def _reminder_sort_key(reminder: ReminderEvidence) -> tuple[datetime, str, str]:
    return (
        reminder.due_date or datetime.max.replace(tzinfo=UTC),
        reminder.list_id,
        reminder.reminder_id,
    )


def _reminder_page_key(reminder: ReminderEvidence) -> tuple[str, ...]:
    due_key = (
        reminder.due_date.astimezone(UTC).isoformat()
        if reminder.due_date is not None
        else "9999-12-31T23:59:59.999999+00:00"
    )
    return (due_key, reminder.list_id, reminder.reminder_id)


def _reminder_evidence_id(list_id: str, reminder_id: str) -> str:
    digest = hashlib.sha256(f"{list_id}:{reminder_id}".encode()).hexdigest()
    return f"reminder:{digest[:32]}"


def _stable_reminder_item_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
    return f"reminder:{digest[:32]}"


def _stable_completed_reminder_item_id(list_id: str, reminder_id: str) -> str:
    digest = hashlib.sha256(f"{list_id}:{reminder_id}".encode()).hexdigest()
    return f"reminder:{digest[:32]}"


def _request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
