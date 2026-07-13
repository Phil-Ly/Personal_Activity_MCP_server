"""Activity Log repository backed by a dedicated Calendar."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_activity_mcp.activity.models import (
    ActivityLogCalendarResult,
    ActivityRecordResult,
)
from personal_activity_mcp.calendar import CalendarEnsureRecord, CalendarEventRecord
from personal_activity_mcp.config import AppConfig, CalendarSource
from personal_activity_mcp.sidecar import SidecarRepository
from personal_activity_mcp.time_policy import Clock, SystemClock, require_aware_datetime


class ActivityCalendarBackend(Protocol):
    """Calendar backend operations needed by Activity Log."""

    def ensure_calendar(
        self, *, calendar_title: str, create_if_missing: bool
    ) -> CalendarEnsureRecord: ...

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
    ) -> CalendarEventRecord: ...


class ActivityRepository:
    """Write confirmed completed actions into the configured Activity Log Calendar."""

    def __init__(
        self,
        config: AppConfig,
        backend: ActivityCalendarBackend,
        sidecar: SidecarRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._calendar_sources = {source.calendar_id: source for source in config.calendar_sources}
        self._default_activity_log_calendar_id = config.default_activity_log_calendar_id
        self._backend = backend
        self._sidecar = sidecar
        self._clock = clock or SystemClock()

    def ensure_log_calendar(
        self,
        *,
        calendar_title: str | None,
        create_if_missing: bool,
    ) -> ActivityLogCalendarResult:
        """Ensure the configured Activity Log Calendar exists and is recorded in sidecar."""
        target_title = (calendar_title or self._default_activity_log_calendar_id).strip()
        if not target_title:
            raise ValueError("calendar_title must be a non-empty string")
        if target_title != self._default_activity_log_calendar_id:
            raise ValueError(f"Calendar is not configured as Activity Log: {target_title}")

        source = self._activity_source(target_title)
        calendar = self._backend.ensure_calendar(
            calendar_title=target_title,
            create_if_missing=create_if_missing,
        )
        if calendar.calendar_id != source.calendar_id:
            raise ValueError(
                f"Calendar backend returned unexpected Activity Log id: {calendar.calendar_id}"
            )
        self._sidecar.upsert_calendar_source(source)
        return ActivityLogCalendarResult(
            calendar_id=calendar.calendar_id,
            calendar_title=calendar.calendar_title,
            created=calendar.created,
            is_default_activity_log=True,
        )

    def record_completed_action(
        self,
        *,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        is_all_day: bool,
        category: str | None,
        project: str | None,
        notes: str | None,
        location: str | None,
        timezone: str,
        provenance_ids: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> ActivityRecordResult:
        """Record one user-confirmed completed action as an action_record."""
        if not confirmed_by_user:
            request_hash = _request_hash(
                {
                    "calendar_id": calendar_id,
                    "title_hash": _request_hash({"title": title}),
                    "operation": "activity.record_completed_action",
                }
            )
            self._sidecar.record_operation_audit(
                operation="activity.record_completed_action",
                target_item_id=None,
                request_hash=request_hash,
                result_status="blocked",
                error_code="USER_CONFIRMATION_REQUIRED",
                confirmed_by_user=False,
            )
            raise ValueError("USER_CONFIRMATION_REQUIRED")
        source = self._activity_source(calendar_id)
        if not title.strip():
            raise ValueError("title must be a non-empty string")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        _validate_timezone(timezone)
        require_aware_datetime(start, "start")
        require_aware_datetime(end, "end")
        if start >= end:
            raise ValueError("start must be before end")
        now = self._clock.now()
        require_aware_datetime(now, "clock.now()")
        if end.astimezone(UTC) > now.astimezone(UTC):
            raise ValueError("completed actions must end in the past")

        request_hash = _request_hash(
            {
                "calendar_id": calendar_id,
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "is_all_day": is_all_day,
                "category": category,
                "project": project,
                "notes": notes,
                "location": location,
                "timezone": timezone,
                "provenance_ids": provenance_ids,
                "confirmed_by_user": confirmed_by_user,
            }
        )
        decision = self._sidecar.check_idempotency_key(
            key=idempotency_key,
            operation="activity.record_completed_action",
            request_hash=request_hash,
        )
        if decision.decision == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.decision == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            return ActivityRecordResult(
                action_record_id=str(item["id"]),
                event_id=str(item["external_id"]),
                stable_id=str(item["id"]),
                status_semantics="confirmed",
                created=False,
                deduplicated=True,
                provenance_ids=provenance_ids,
                audit_id=None,
            )

        record = self._backend.create_event(
            calendar_id=source.calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
            timezone=timezone,
        )
        stable_id = _stable_action_record_id(idempotency_key)
        self._sidecar.upsert_mcp_item(
            item_id=stable_id,
            item_type="action_record",
            external_id=record.event_id,
            external_calendar_or_list_id=source.calendar_id,
            title_hash=_request_hash({"title": title}),
            time_start=start.isoformat(),
            time_end=end.isoformat(),
            status_semantics="confirmed",
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
            operation="activity.record_completed_action",
            request_hash=request_hash,
            result_item_id=stable_id,
        )
        audit_id = self._sidecar.record_operation_audit(
            operation="activity.record_completed_action",
            target_item_id=stable_id,
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=True,
        )
        return ActivityRecordResult(
            action_record_id=stable_id,
            event_id=record.event_id,
            stable_id=stable_id,
            status_semantics="confirmed",
            created=True,
            deduplicated=False,
            provenance_ids=provenance_ids,
            audit_id=audit_id,
        )

    def _activity_source(self, calendar_id: str) -> CalendarSource:
        if calendar_id != self._default_activity_log_calendar_id:
            raise ValueError(f"Calendar is not configured as Activity Log: {calendar_id}")
        source = self._calendar_sources.get(calendar_id)
        if source is None:
            raise ValueError(f"Unknown calendar_ids: {calendar_id}")
        if not source.allow_write:
            raise ValueError(f"Calendar is not allowed for writes: {calendar_id}")
        return source


def _stable_action_record_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"action_record:{digest[:32]}"


def _request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_timezone(timezone: str) -> None:
    if not timezone.strip():
        raise ValueError("timezone must be a non-empty string")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {timezone}") from error


def _evidence_type_from_id(evidence_id: str):
    if evidence_id.startswith("journal:"):
        return "journal_entry"
    if evidence_id.startswith("calendar:") or evidence_id.startswith("calendar_event:"):
        return "calendar_event"
    if evidence_id.startswith("reminder:"):
        return "reminder"
    raise ValueError(f"Unsupported provenance id: {evidence_id}")
