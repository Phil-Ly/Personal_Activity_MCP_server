"""Calendar repository with allowlist and sidecar semantics."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_activity_mcp.calendar.models import (
    AllDayEventRange,
    CalendarCreateResult,
    CalendarEventEvidence,
    CalendarEventRecord,
    CalendarListResult,
    CalendarUpdateResult,
    TimedEventRange,
)
from personal_activity_mcp.common import (
    TargetRef,
    ToolWarning,
    decode_cursor,
    paginate,
    validate_limit,
)
from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.sidecar import ExternalItemContext, SidecarRepository
from personal_activity_mcp.time_policy import Clock, SystemClock, require_aware_datetime


class CalendarBackend(Protocol):
    """Backend capable of reading Apple Calendar events."""

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]: ...

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
    ) -> CalendarEventRecord: ...


class CalendarRepository:
    """Read and write configured Apple Calendars."""

    def __init__(
        self,
        config: AppConfig,
        backend: CalendarBackend,
        sidecar: SidecarRepository | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._calendar_sources = {source.calendar_id: source for source in config.calendar_sources}
        self._backend = backend
        self._sidecar = sidecar
        self._clock = clock or SystemClock()

    def list_events(
        self,
        *,
        calendar_ids: list[str] | None,
        start: datetime,
        end: datetime,
        include_notes: bool = False,
        include_location: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> CalendarListResult:
        """List event evidence from explicitly configured Calendars."""
        require_aware_datetime(start, "start")
        require_aware_datetime(end, "end")
        if start >= end:
            raise ValueError("start must be before end")
        validate_limit(limit)
        if cursor is not None:
            decode_cursor(cursor)
        selected_calendar_ids = self._select_calendar_ids(calendar_ids)
        records = self._backend.list_events(
            calendar_ids=selected_calendar_ids,
            start=start,
            end=end,
            include_notes=include_notes,
            include_location=include_location,
        )
        records, warnings = _deduplicate_records(
            [record for record in records if record.calendar_id in set(selected_calendar_ids)]
        )
        sidecar_contexts = self._list_sidecar_contexts(records)
        now = self._clock.now()
        require_aware_datetime(now, "clock.now()")
        events = [
            self._to_evidence(
                record,
                include_notes=include_notes,
                include_location=include_location,
                now=now,
                sidecar_contexts=sidecar_contexts,
            )
            for record in records
        ]
        events.sort(key=lambda event: (event.start, event.end, event.calendar_id, event.event_id))
        page, next_cursor = paginate(
            events,
            key=_calendar_page_key,
            limit=limit,
            cursor=cursor,
        )
        return CalendarListResult(
            events=page,
            warnings=warnings,
            next_cursor=next_cursor,
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
        source_refs: list[str],
        idempotency_key: str,
    ) -> CalendarCreateResult:
        """Create a Calendar event with allowlist, idempotency, and audit controls."""
        source = self._calendar_sources.get(calendar_id)
        if source is None:
            raise ValueError(f"Unknown calendar_ids: {calendar_id}")
        if not source.allow_write:
            raise ValueError(f"Calendar is not allowed for writes: {calendar_id}")
        if self._sidecar is None:
            raise ValueError("sidecar is required for calendar.create_event")
        if not title.strip():
            raise ValueError("title must be a non-empty string")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        _validate_timezone(timezone)
        require_aware_datetime(start, "start")
        require_aware_datetime(end, "end")
        if start >= end:
            raise ValueError("start must be before end")

        request_hash = _request_hash(
            {
                "calendar_id": calendar_id,
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "is_all_day": is_all_day,
                "notes": notes,
                "location": location,
                "timezone": timezone,
                "source_refs": source_refs,
            }
        )
        decision = self._sidecar.check_idempotency_key(
            key=idempotency_key,
            operation="calendar.create_event",
            request_hash=request_hash,
        )
        if decision.decision == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.decision == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            return CalendarCreateResult(
                event_id=str(item["external_id"]),
                calendar_id=str(item["external_calendar_or_list_id"]),
                stable_id=str(item["id"]),
                created=False,
                deduplicated=True,
                status_semantics="planned",
                source_refs=source_refs,
            )

        record = self._backend.create_event(
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
            timezone=timezone,
        )
        stable_id = _stable_calendar_item_id(idempotency_key)
        self._sidecar.upsert_mcp_item(
            item_id=stable_id,
            item_type="calendar_event",
            external_id=record.event_id,
            external_calendar_or_list_id=calendar_id,
            title_hash=_request_hash({"title": title}),
            time_start=start.isoformat(),
            time_end=end.isoformat(),
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
            operation="calendar.create_event",
            request_hash=request_hash,
            result_item_id=stable_id,
        )
        self._sidecar.record_operation_audit(
            operation="calendar.create_event",
            target_item_id=stable_id,
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=True,
        )
        return CalendarCreateResult(
            event_id=record.event_id,
            calendar_id=calendar_id,
            stable_id=stable_id,
            created=True,
            deduplicated=False,
            status_semantics="planned",
            source_refs=source_refs,
        )

    def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        is_all_day: bool | None,
        notes: str | None,
        location: str | None,
        timezone: str,
        source_refs: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> CalendarUpdateResult:
        """Update a Calendar event with allowlist, safety, idempotency, and audit controls."""
        source = self._calendar_sources.get(calendar_id)
        if source is None:
            raise ValueError(f"Unknown calendar_ids: {calendar_id}")
        if not source.allow_write:
            raise ValueError(f"Calendar is not allowed for writes: {calendar_id}")
        if self._sidecar is None:
            raise ValueError("sidecar is required for calendar.update_event")
        if not event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if title is not None and not title.strip():
            raise ValueError("title must be a non-empty string when provided")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        _validate_timezone(timezone)
        if start is not None:
            require_aware_datetime(start, "start")
        if end is not None:
            require_aware_datetime(end, "end")
        if start is not None and end is not None and start >= end:
            raise ValueError("start must be before end")

        sidecar_item = self._sidecar_item_for_calendar_event(
            external_id=event_id,
            external_calendar_or_list_id=calendar_id,
        )
        if _is_confirmed_action_record(sidecar_item) and not confirmed_by_user:
            request_hash = _request_hash(
                {
                    "calendar_id": calendar_id,
                    "event_id": event_id,
                    "operation": "calendar.update_event",
                }
            )
            self._sidecar.record_operation_audit(
                operation="calendar.update_event",
                target_item_id=str(sidecar_item["id"]) if sidecar_item is not None else None,
                request_hash=request_hash,
                result_status="blocked",
                error_code="USER_CONFIRMATION_REQUIRED",
                confirmed_by_user=False,
            )
            raise ValueError("USER_CONFIRMATION_REQUIRED")

        updated_fields = _updated_fields(
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
        )
        if not updated_fields:
            raise ValueError("At least one update field is required")

        request_hash = _request_hash(
            {
                "calendar_id": calendar_id,
                "event_id": event_id,
                "title": title,
                "start": start.isoformat() if start is not None else None,
                "end": end.isoformat() if end is not None else None,
                "is_all_day": is_all_day,
                "notes": notes,
                "location": location,
                "timezone": timezone,
                "source_refs": source_refs,
                "confirmed_by_user": confirmed_by_user,
            }
        )
        decision = self._sidecar.check_idempotency_key(
            key=idempotency_key,
            operation="calendar.update_event",
            request_hash=request_hash,
        )
        if decision.decision == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.decision == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            return CalendarUpdateResult(
                event_id=str(item["external_id"]),
                calendar_id=str(item["external_calendar_or_list_id"]),
                stable_id=str(item["id"]),
                updated=False,
                deduplicated=True,
                updated_fields=updated_fields,
                requires_user_confirmation=False,
                status_semantics=_sidecar_status_semantics(item),
                source_refs=source_refs,
                audit_id=None,
            )

        record = self._backend.update_event(
            event_id=event_id,
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
            timezone=timezone,
        )
        stable_id = (
            str(sidecar_item["id"])
            if sidecar_item is not None
            else _stable_calendar_item_id(f"{calendar_id}:{event_id}")
        )
        status_semantics = (
            _sidecar_status_semantics(sidecar_item) if sidecar_item is not None else "planned"
        )
        self._sidecar.upsert_mcp_item(
            item_id=stable_id,
            item_type=str(sidecar_item["item_type"])
            if sidecar_item is not None
            else "calendar_event",
            external_id=record.event_id,
            external_calendar_or_list_id=calendar_id,
            title_hash=_request_hash({"title": record.title}),
            time_start=record.start.isoformat(),
            time_end=record.end.isoformat(),
            status_semantics=status_semantics,
            created_by_mcp=bool(sidecar_item and sidecar_item["created_by_mcp"]),
        )
        for source_ref in source_refs:
            self._sidecar.record_source_link(
                target_item_id=stable_id,
                source_ref=source_ref,
                relation_type="updated_from",
            )
        self._sidecar.record_idempotency_success(
            key=idempotency_key,
            operation="calendar.update_event",
            request_hash=request_hash,
            result_item_id=stable_id,
        )
        audit_id = self._sidecar.record_operation_audit(
            operation="calendar.update_event",
            target_item_id=stable_id,
            request_hash=request_hash,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=confirmed_by_user,
        )
        return CalendarUpdateResult(
            event_id=record.event_id,
            calendar_id=calendar_id,
            stable_id=stable_id,
            updated=True,
            deduplicated=False,
            updated_fields=updated_fields,
            requires_user_confirmation=False,
            status_semantics=status_semantics,
            source_refs=source_refs,
            audit_id=audit_id,
        )

    def _select_calendar_ids(self, calendar_ids: list[str] | None) -> list[str]:
        if calendar_ids is None:
            return list(self._calendar_sources)
        unknown = sorted(set(calendar_ids) - self._calendar_sources.keys())
        if unknown:
            raise ValueError(f"Unknown calendar_ids: {', '.join(unknown)}")
        return calendar_ids

    def _to_evidence(
        self,
        record: CalendarEventRecord,
        *,
        include_notes: bool,
        include_location: bool,
        now: datetime,
        sidecar_contexts: dict[tuple[str, str, str], ExternalItemContext],
    ) -> CalendarEventEvidence:
        context = sidecar_contexts.get(
            ("calendar_event", record.event_id, record.calendar_id)
        ) or sidecar_contexts.get(("action_record", record.event_id, record.calendar_id))
        sidecar_item = context.item if context is not None else None
        source_refs = list(context.source_refs) if context is not None else []
        status_semantics = _status_semantics(record, now=now)
        if record.is_all_day:
            time_range = AllDayEventRange(
                start_date=record.start_date or record.start.date(),
                end_date=record.end_date or record.end.date(),
            )
        else:
            time_range = TimedEventRange(start=record.start, end=record.end)
        return CalendarEventEvidence(
            evidence_id=_calendar_evidence_id(record.calendar_id, record.event_id),
            source_id=record.calendar_id,
            time_range=time_range,
            target_ref=TargetRef(
                resource_type="calendar_event",
                item_id=record.event_id,
                container_id=record.calendar_id,
            ),
            state_token=_calendar_state_token(record),
            title=record.title,
            event_id=record.event_id,
            calendar_id=record.calendar_id,
            start=record.start,
            end=record.end,
            is_all_day=record.is_all_day,
            location=record.location if include_location else None,
            notes=record.notes if include_notes else None,
            created_by_mcp=bool(sidecar_item and sidecar_item["created_by_mcp"]),
            status_semantics=status_semantics,
            completion_status="unknown",
            source_refs=source_refs,
        )

    def _list_sidecar_contexts(
        self,
        records: list[CalendarEventRecord],
    ) -> dict[tuple[str, str, str], ExternalItemContext]:
        if self._sidecar is None:
            return {}
        return self._sidecar.list_external_item_contexts(
            item_types=("calendar_event", "action_record"),
            targets=[(record.event_id, record.calendar_id) for record in records],
        )

    def _sidecar_item_for_calendar_event(
        self,
        *,
        external_id: str,
        external_calendar_or_list_id: str,
    ) -> dict[str, object] | None:
        if self._sidecar is None:
            return None
        calendar_item = self._sidecar.find_mcp_item_by_external(
            item_type="calendar_event",
            external_id=external_id,
            external_calendar_or_list_id=external_calendar_or_list_id,
        )
        if calendar_item is not None:
            return calendar_item
        return self._sidecar.find_mcp_item_by_external(
            item_type="action_record",
            external_id=external_id,
            external_calendar_or_list_id=external_calendar_or_list_id,
        )


def _calendar_evidence_id(calendar_id: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{calendar_id}:{event_id}".encode()).hexdigest()
    return f"calendar:{digest[:32]}"


def _status_semantics(
    record: CalendarEventRecord,
    *,
    now: datetime,
):
    require_aware_datetime(record.end, "record.end")
    return "probable" if record.end.astimezone(UTC) <= now.astimezone(UTC) else "planned"


def _deduplicate_records(
    records: list[CalendarEventRecord],
) -> tuple[list[CalendarEventRecord], list[ToolWarning]]:
    grouped: dict[tuple[str, str], list[CalendarEventRecord]] = {}
    for record in records:
        grouped.setdefault((record.calendar_id, record.event_id), []).append(record)

    unique_records: list[CalendarEventRecord] = []
    warnings: list[ToolWarning] = []
    for (calendar_id, event_id), matches in grouped.items():
        first = matches[0]
        if all(match == first for match in matches[1:]):
            unique_records.append(first)
            continue
        warnings.append(
            ToolWarning(
                code="DUPLICATE_SOURCE_ITEM",
                message="Conflicting Calendar records share the same source identity",
                related_item_ids=[f"{calendar_id}:{event_id}"],
            )
        )
    return unique_records, warnings


def _calendar_state_token(record: CalendarEventRecord) -> str:
    payload = {
        "calendar_id": record.calendar_id,
        "event_id": record.event_id,
        "title": record.title,
        "start": record.start.isoformat(),
        "end": record.end.isoformat(),
        "is_all_day": record.is_all_day,
        "start_date": record.start_date.isoformat() if record.start_date else None,
        "end_date": record.end_date.isoformat() if record.end_date else None,
        "location": record.location,
        "notes": record.notes,
    }
    return f"calendar-state:{_request_hash(payload)}"


def _calendar_page_key(event: CalendarEventEvidence) -> tuple[str, ...]:
    return (
        event.start.astimezone(UTC).isoformat(),
        event.end.astimezone(UTC).isoformat(),
        event.calendar_id,
        event.event_id,
    )


def _sidecar_status_semantics(sidecar_item: dict[str, object]) -> str:
    status = sidecar_item.get("status_semantics")
    if status in {"planned", "probable", "confirmed"}:
        return str(status)
    return "planned"


def _is_confirmed_action_record(sidecar_item: dict[str, object] | None) -> bool:
    if sidecar_item is None:
        return False
    return (
        sidecar_item.get("item_type") == "action_record"
        and sidecar_item.get("status_semantics") == "confirmed"
    )


def _updated_fields(
    *,
    title: str | None,
    start: datetime | None,
    end: datetime | None,
    is_all_day: bool | None,
    notes: str | None,
    location: str | None,
) -> list[str]:
    fields: list[str] = []
    if title is not None:
        fields.append("title")
    if start is not None:
        fields.append("start")
    if end is not None:
        fields.append("end")
    if is_all_day is not None:
        fields.append("is_all_day")
    if notes is not None:
        fields.append("notes")
    if location is not None:
        fields.append("location")
    return fields


def _stable_calendar_item_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"calendar_event:{digest[:32]}"


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
