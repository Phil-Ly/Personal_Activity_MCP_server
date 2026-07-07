"""Reminder repository with allowlist and sidecar semantics."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Protocol

from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.reminders.models import (
    ReminderCompleteResult,
    ReminderCreateResult,
    ReminderEvidence,
    ReminderListResult,
    ReminderRecord,
    ReminderTimeRange,
)
from personal_activity_mcp.sidecar import SidecarRepository


class ReminderBackend(Protocol):
    """Backend capable of reading Apple Reminders."""

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_date: date | None,
        end_due_date: date | None,
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
        self._backend = backend
        self._sidecar = sidecar

    def list_reminders(
        self,
        *,
        list_ids: list[str] | None,
        start_due_date: date | None,
        end_due_date: date | None,
        include_completed: bool = False,
        include_notes: bool = False,
    ) -> ReminderListResult:
        """List reminder evidence from explicitly configured lists."""
        if (
            start_due_date is not None
            and end_due_date is not None
            and start_due_date > end_due_date
        ):
            raise ValueError("start_due_date must be on or before end_due_date")
        selected_list_ids = self._select_list_ids(list_ids)
        records = self._backend.list_reminders(
            list_ids=selected_list_ids,
            start_due_date=start_due_date,
            end_due_date=end_due_date,
            include_completed=include_completed,
            include_notes=include_notes,
        )
        reminders = [
            self._to_evidence(record, include_notes=include_notes)
            for record in records
            if record.list_id in self._reminder_sources
        ]
        reminders.sort(
            key=lambda reminder: (
                reminder.due_date or date.max,
                reminder.list_id,
                reminder.reminder_id,
            )
        )
        return ReminderListResult(reminders=reminders, warnings=[])

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
        provenance_ids: list[str],
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
                "provenance_ids": provenance_ids,
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
                provenance_ids=provenance_ids,
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
        for provenance_id in provenance_ids:
            self._sidecar.record_provenance_link(
                target_item_id=stable_id,
                evidence_type=_evidence_type_from_id(provenance_id),
                evidence_id=provenance_id,
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
            provenance_ids=provenance_ids,
        )

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        completion_date: datetime,
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ReminderCompleteResult:
        """Mark an allowed Reminder as completed."""
        if not confirmed_by_user:
            raise ValueError("confirmed_by_user is required")
        if self._sidecar is None:
            raise ValueError("sidecar is required for reminders.complete_reminder")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")

        list_ids = list(self._reminder_sources)
        request_hash = _request_hash(
            {
                "reminder_id": reminder_id,
                "completion_date": completion_date.isoformat(),
                "confirmed_by_user": confirmed_by_user,
            }
        )
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
        if record.list_id not in self._reminder_sources:
            raise ValueError(f"Reminder is not in an allowed list: {record.list_id}")
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

    def _select_list_ids(self, list_ids: list[str] | None) -> list[str]:
        if list_ids is None:
            return list(self._reminder_sources)
        unknown = sorted(set(list_ids) - self._reminder_sources.keys())
        if unknown:
            raise ValueError(f"Unknown reminder list_ids: {', '.join(unknown)}")
        return list_ids

    def _to_evidence(self, record: ReminderRecord, *, include_notes: bool) -> ReminderEvidence:
        sidecar_item = None
        provenance_ids: list[str] = []
        if self._sidecar is not None:
            sidecar_item = self._sidecar.find_mcp_item_by_external(
                item_type="reminder",
                external_id=record.reminder_id,
                external_calendar_or_list_id=record.list_id,
            )
            if sidecar_item is not None:
                provenance_ids = self._sidecar.list_provenance_ids(str(sidecar_item["id"]))
        return ReminderEvidence(
            evidence_id=_reminder_evidence_id(record.list_id, record.reminder_id),
            source_id=record.list_id,
            time_range=ReminderTimeRange(start=record.due_date, end=record.due_date),
            title=record.title,
            reminder_id=record.reminder_id,
            list_id=record.list_id,
            notes=record.notes if include_notes else None,
            due_date=record.due_date,
            priority=record.priority,
            is_completed=record.is_completed,
            completion_date=record.completion_date,
            created_by_mcp=bool(sidecar_item and sidecar_item["created_by_mcp"]),
            status_semantics="confirmed" if record.is_completed else "planned",
            provenance_ids=provenance_ids,
        )


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


def _evidence_type_from_id(evidence_id: str):
    if evidence_id.startswith("journal:"):
        return "journal_entry"
    if evidence_id.startswith("calendar:") or evidence_id.startswith("calendar_event:"):
        return "calendar_event"
    if evidence_id.startswith("reminder:"):
        return "reminder"
    raise ValueError(f"Unsupported provenance id: {evidence_id}")
