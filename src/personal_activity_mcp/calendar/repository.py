"""Calendar repository with allowlist and sidecar semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_activity_mcp.calendar.models import (
    AllDayEventRange,
    CalendarContainerRecord,
    CalendarCreateResult,
    CalendarEventEvidence,
    CalendarEventRecord,
    CalendarListResult,
    CalendarUpdateResult,
    DescriptionUpdate,
    TimedEventRange,
)
from personal_activity_mcp.common import (
    TargetRef,
    ToolWarning,
    decode_cursor,
    normalize_optional_text,
    normalize_source_refs,
    paginate,
    validate_limit,
)
from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.sidecar import (
    AuditWrite,
    ControlledWrite,
    ExternalItemContext,
    McpItemWrite,
    SidecarRepository,
    WriteControl,
    request_hash,
)
from personal_activity_mcp.time_policy import (
    Clock,
    SystemClock,
    normalize_external_datetime,
    require_aware_datetime,
)


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

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[CalendarContainerRecord]: ...

    def get_calendar(
        self,
        *,
        calendar_id: str,
    ) -> CalendarContainerRecord: ...

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
        description: DescriptionUpdate,
    ) -> CalendarEventRecord: ...

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEventRecord: ...


class CalendarRepository:
    """Read and write events in authorized EventKit Calendar Sources."""

    def __init__(
        self,
        config: AppConfig,
        backend: CalendarBackend,
        sidecar: SidecarRepository | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._eventkit_sources = {source.source_id: source for source in config.eventkit_sources}
        self._backend = backend
        self._sidecar = sidecar
        self._write_control = WriteControl(sidecar) if sidecar is not None else None
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
        """List event evidence from Calendars in authorized EventKit Sources."""
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
        source = self._calendar_source(calendar_id)
        if not source.allow_calendar_write:
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
        start = normalize_external_datetime(start)
        end = normalize_external_datetime(end)
        if start >= end:
            raise ValueError("start must be before end")
        notes = normalize_optional_text(notes)
        location = normalize_optional_text(location)

        normalized_source_refs = normalize_source_refs(source_refs)
        request_digest = request_hash(
            {
                "calendar_id": calendar_id,
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "is_all_day": is_all_day,
                "notes": notes,
                "location": location,
                "timezone": timezone,
                "source_refs": normalized_source_refs,
            }
        )
        write_control = self._require_write_control()
        flow = ControlledWrite(
            control=write_control,
            idempotency_key=idempotency_key,
            operation="calendar.create_event",
            request_hash=request_digest,
            confirmed_by_user=False,
            resource_name="Calendar event",
        )
        decision = flow.reserve()
        if decision.status == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            return CalendarCreateResult(
                event_id=str(item["external_id"]),
                calendar_id=str(item["external_container_id"]),
                stable_id=str(item["id"]),
                created=False,
                deduplicated=True,
                status_semantics=_sidecar_status_semantics(item),
                source_refs=normalized_source_refs,
            )

        try:
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
        except Exception as error:
            flow.backend_failed(error)
        if not record.event_id.strip() or record.calendar_id != calendar_id:
            flow.unverified_result()
        try:
            record = self._backend.get_event(
                event_id=record.event_id,
                calendar_id=calendar_id,
            )
        except Exception:
            flow.unverified_result()
        if not _created_event_matches_request(
            record,
            calendar_id=calendar_id,
            title=title,
            start=start,
            end=end,
            is_all_day=is_all_day,
            notes=notes,
            location=location,
        ):
            flow.unverified_result()
        now = self._clock.now()
        require_aware_datetime(now, "clock.now()")
        status_semantics = _status_semantics(record, now=now)
        stable_id = _stable_calendar_item_id(calendar_id, record.event_id)
        try:
            write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation="calendar.create_event",
                item=McpItemWrite(
                    item_id=stable_id,
                    item_type="calendar_event",
                    external_id=record.event_id,
                    external_container_id=calendar_id,
                    status_semantics=status_semantics,
                    created_by_mcp=True,
                    completion_status="unknown",
                ),
                source_refs=normalized_source_refs,
                audit=AuditWrite(
                    request_hash=request_digest,
                    result_status="succeeded",
                    error_code=None,
                    confirmed_by_user=False,
                ),
                external_write_attempted=True,
            )
        except Exception as error:
            flow.finalization_failed(error, external_write_attempted=True)
        return CalendarCreateResult(
            event_id=record.event_id,
            calendar_id=calendar_id,
            stable_id=stable_id,
            created=True,
            deduplicated=False,
            status_semantics=status_semantics,
            source_refs=normalized_source_refs,
        )

    def _require_write_control(self) -> WriteControl:
        if self._write_control is None:
            raise ValueError("sidecar is required for Calendar writes")
        return self._write_control

    def update_event(
        self,
        *,
        target_ref: TargetRef,
        description: DescriptionUpdate | None,
        completion_status: Literal["unknown", "incomplete", "completed"] | None,
        expected_state_token: str | None,
        source_refs: list[str],
        confirmed_by_user: bool,
        idempotency_key: str,
    ) -> CalendarUpdateResult:
        """Update a Calendar event with allowlist, safety, idempotency, and audit controls."""
        if target_ref.resource_type != "calendar_event" or target_ref.container_id is None:
            raise ValueError("target_ref must identify one calendar_event and Calendar")
        calendar_id = target_ref.container_id
        event_id = target_ref.item_id
        source = self._calendar_source(calendar_id)
        if not source.allow_calendar_write:
            raise ValueError(f"Calendar is not allowed for writes: {calendar_id}")
        if self._sidecar is None:
            raise ValueError("sidecar is required for calendar.update_event")
        if not event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if description is None and completion_status is None:
            raise ValueError("At least one update field is required")

        normalized_source_refs = normalize_source_refs(source_refs)
        request_digest = request_hash(
            {
                "target_ref": target_ref.model_dump(mode="json"),
                "description": description.model_dump(mode="json")
                if description is not None
                else None,
                "completion_status": completion_status,
                "expected_state_token": expected_state_token,
                "source_refs": normalized_source_refs,
                "confirmed_by_user": confirmed_by_user,
            }
        )
        sidecar_item = self._sidecar_item_for_calendar_event(
            external_id=event_id,
            external_container_id=calendar_id,
        )
        write_control = self._require_write_control()
        flow = ControlledWrite(
            control=write_control,
            idempotency_key=idempotency_key,
            operation="calendar.update_event",
            request_hash=request_digest,
            confirmed_by_user=confirmed_by_user,
            resource_name="Calendar event",
        )
        if completion_status is not None and not confirmed_by_user:
            flow.record_blocked(
                target_item_id=str(sidecar_item["id"]) if sidecar_item is not None else None,
                error_code="USER_CONFIRMATION_REQUIRED",
            )
            raise ValueError("USER_CONFIRMATION_REQUIRED")

        updated_fields = [
            field
            for field, value in (
                ("description", description),
                ("completion_status", completion_status),
            )
            if value is not None
        ]
        decision = flow.reserve()
        if decision.status == "deduplicated":
            item = self._sidecar.get_mcp_item(decision.result_item_id or "")
            if item is None:
                raise ValueError("idempotency result item is missing")
            operation_result = write_control.get_operation_result(
                idempotency_key=idempotency_key,
                operation="calendar.update_event",
            )
            if operation_result is None or operation_result.audit_id is None:
                raise ValueError("idempotency audit is missing")
            return CalendarUpdateResult(
                event_id=str(item["external_id"]),
                calendar_id=str(item["external_container_id"]),
                stable_id=str(item["id"]),
                updated=False,
                deduplicated=True,
                updated_fields=updated_fields,
                status_semantics=_sidecar_status_semantics(item),
                completion_status=self._calendar_completion_status(str(item["id"])),
                source_refs=normalized_source_refs,
                audit_id=operation_result.audit_id,
            )

        try:
            current = self._backend.get_event(
                event_id=event_id,
                calendar_id=calendar_id,
            )
        except Exception as error:
            flow.preflight_failed(error)
        if current.event_id != event_id or current.calendar_id != calendar_id:
            flow.external_state_changed()
        current_completion_status = (
            str(sidecar_item["completion_status"])
            if sidecar_item is not None and sidecar_item["completion_status"] is not None
            else "unknown"
        )
        if (
            expected_state_token is not None
            and _calendar_state_token(current, current_completion_status) != expected_state_token
        ):
            flow.external_state_changed()

        record = current
        if description is not None:
            try:
                self._backend.update_event(
                    event_id=event_id,
                    calendar_id=calendar_id,
                    description=description,
                )
            except Exception as error:
                flow.backend_failed(error)
            try:
                record = self._backend.get_event(
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            except Exception:
                flow.unverified_result()
            expected_notes = description.value if description.operation == "set" else None
            if (
                record.event_id != event_id
                or record.calendar_id != calendar_id
                or record.notes != expected_notes
            ):
                flow.unverified_result()

        stable_id = (
            str(sidecar_item["id"])
            if sidecar_item is not None
            else _stable_calendar_item_id(calendar_id, event_id)
        )
        now = self._clock.now()
        require_aware_datetime(now, "clock.now()")
        status_semantics = _status_semantics(record, now=now)
        audit = AuditWrite(
            request_hash=request_digest,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=confirmed_by_user,
        )
        try:
            write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation="calendar.update_event",
                item=McpItemWrite(
                    item_id=stable_id,
                    item_type="calendar_event",
                    external_id=record.event_id,
                    external_container_id=calendar_id,
                    status_semantics=status_semantics,
                    created_by_mcp=bool(sidecar_item and sidecar_item["created_by_mcp"]),
                    completion_status=completion_status,
                    expected_completion_status=(
                        current_completion_status if expected_state_token is not None else None
                    ),
                ),
                source_refs=normalized_source_refs,
                audit=audit,
                external_write_attempted=description is not None,
            )
        except Exception as error:
            flow.finalization_failed(
                error,
                external_write_attempted=description is not None,
            )
        return CalendarUpdateResult(
            event_id=record.event_id,
            calendar_id=calendar_id,
            stable_id=stable_id,
            updated=True,
            deduplicated=False,
            updated_fields=updated_fields,
            status_semantics=status_semantics,
            completion_status=completion_status
            if completion_status is not None
            else self._calendar_completion_status(stable_id),
            source_refs=normalized_source_refs,
            audit_id=audit.audit_id,
        )

    def _select_calendar_ids(self, calendar_ids: list[str] | None) -> list[str]:
        if calendar_ids is None:
            records = self._backend.list_calendars(source_ids=list(self._eventkit_sources))
            return list(
                dict.fromkeys(
                    record.calendar_id
                    for record in records
                    if record.source_id in self._eventkit_sources
                )
            )
        selected: list[str] = []
        unknown: list[str] = []
        for calendar_id in dict.fromkeys(calendar_ids):
            try:
                record = self._backend.get_calendar(calendar_id=calendar_id)
            except Exception:
                unknown.append(calendar_id)
                continue
            if record.source_id not in self._eventkit_sources:
                unknown.append(calendar_id)
                continue
            selected.append(calendar_id)
        if unknown:
            raise ValueError(f"Unknown calendar_ids: {', '.join(sorted(unknown))}")
        return selected

    def _calendar_source(self, calendar_id: str):
        try:
            record = self._backend.get_calendar(calendar_id=calendar_id)
        except Exception as error:
            raise ValueError(f"Unknown calendar_ids: {calendar_id}") from error
        source = self._eventkit_sources.get(record.source_id)
        if source is None:
            raise ValueError(f"Unknown calendar_ids: {calendar_id}")
        return source

    def _to_evidence(
        self,
        record: CalendarEventRecord,
        *,
        include_notes: bool,
        include_location: bool,
        now: datetime,
        sidecar_contexts: dict[tuple[str, str, str], ExternalItemContext],
    ) -> CalendarEventEvidence:
        context = sidecar_contexts.get(("calendar_event", record.event_id, record.calendar_id))
        sidecar_item = context.item if context is not None else None
        source_refs = list(context.source_refs) if context is not None else []
        completion_status = context.completion_status if context is not None else "unknown"
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
            state_token=_calendar_state_token(record, completion_status),
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
            completion_status=completion_status,
            source_refs=source_refs,
        )

    def _list_sidecar_contexts(
        self,
        records: list[CalendarEventRecord],
    ) -> dict[tuple[str, str, str], ExternalItemContext]:
        if self._sidecar is None:
            return {}
        return self._sidecar.list_external_item_contexts(
            item_types=("calendar_event",),
            targets=[(record.event_id, record.calendar_id) for record in records],
        )

    def _sidecar_item_for_calendar_event(
        self,
        *,
        external_id: str,
        external_container_id: str,
    ) -> dict[str, object] | None:
        if self._sidecar is None:
            return None
        return self._sidecar.find_mcp_item_by_external(
            item_type="calendar_event",
            external_id=external_id,
            external_container_id=external_container_id,
        )

    def _calendar_completion_status(
        self,
        item_id: str,
    ) -> Literal["unknown", "incomplete", "completed"]:
        if self._sidecar is None:
            return "unknown"
        item = self._sidecar.get_mcp_item(item_id)
        if item is None or item["completion_status"] is None:
            return "unknown"
        return str(item["completion_status"])  # type: ignore[return-value]


def _calendar_evidence_id(calendar_id: str, event_id: str) -> str:
    digest = request_hash(
        {
            "external_container_id": calendar_id,
            "external_id": event_id,
        }
    )
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


def _calendar_state_token(
    record: CalendarEventRecord,
    completion_status: str,
) -> str:
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
        "completion_status": completion_status,
    }
    return f"calendar-state:{request_hash(payload)}"


def _created_event_matches_request(
    record: CalendarEventRecord,
    *,
    calendar_id: str,
    title: str,
    start: datetime,
    end: datetime,
    is_all_day: bool,
    notes: str | None,
    location: str | None,
) -> bool:
    return (
        bool(record.event_id.strip())
        and record.calendar_id == calendar_id
        and record.title == title
        and record.start == start
        and record.end == end
        and record.is_all_day is is_all_day
        and record.notes == notes
        and record.location == location
    )


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


def _stable_calendar_item_id(calendar_id: str, event_id: str) -> str:
    digest = request_hash(
        {
            "external_container_id": calendar_id,
            "external_id": event_id,
        }
    )
    return f"calendar_event:{digest[:32]}"


def _validate_timezone(timezone: str) -> None:
    if not timezone.strip():
        raise ValueError("timezone must be a non-empty string")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {timezone}") from error
