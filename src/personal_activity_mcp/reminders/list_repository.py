"""Reminder List container domain operations."""

from __future__ import annotations

from typing import Protocol

from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.common.pagination import paginate
from personal_activity_mcp.config import AppConfig, EventKitSource
from personal_activity_mcp.reminders.models import (
    ReminderListContainer,
    ReminderListContainerCreateResult,
    ReminderListContainerListResult,
    ReminderListContainerRecord,
    ReminderListContainerUpdateResult,
)
from personal_activity_mcp.sidecar import (
    AuditWrite,
    ControlledWrite,
    McpItemWrite,
    SidecarRepository,
    WriteControl,
    request_hash,
)


class ReminderListBackend(Protocol):
    """Native Reminder List operations required by the repository."""

    def list_reminder_lists(
        self,
        *,
        source_ids: list[str],
    ) -> list[ReminderListContainerRecord]: ...

    def get_reminder_list(
        self,
        *,
        list_id: str,
    ) -> ReminderListContainerRecord: ...

    def create_reminder_list(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> ReminderListContainerRecord: ...

    def update_reminder_list(
        self,
        *,
        list_id: str,
        title: str | None,
        color: str | None,
    ) -> ReminderListContainerRecord: ...


class ReminderListRepository:
    """Manage EventKit Reminder Lists within configured Source scope."""

    def __init__(
        self,
        config: AppConfig,
        backend: ReminderListBackend,
        sidecar: SidecarRepository,
    ) -> None:
        self._backend = backend
        self._sidecar = sidecar
        self._write_control = WriteControl(sidecar)
        self._sources = {source.source_id: source for source in config.eventkit_sources}

    def list_lists(
        self,
        *,
        source_ids: list[str] | None,
        title_query: str | None,
        modifiable_only: bool,
        limit: int,
        cursor: str | None,
    ) -> ReminderListContainerListResult:
        """List Reminder Lists within configured EventKit Sources."""
        selected_source_ids = self._select_source_ids(source_ids)
        normalized_query = _normalize_title_query(title_query)
        records = self._backend.list_reminder_lists(source_ids=selected_source_ids)
        authorized = set(selected_source_ids)
        unique: dict[str, ReminderListContainerRecord] = {}
        for record in records:
            if record.source_id not in authorized:
                continue
            if normalized_query is not None and normalized_query not in record.title.casefold():
                continue
            if modifiable_only and (not record.allows_content_modifications or record.is_immutable):
                continue
            existing = unique.get(record.list_id)
            if existing is not None and existing != record:
                raise ToolContractError(
                    code="BACKEND_FAILURE",
                    message="Conflicting Reminder Lists share one identifier",
                    retryable=True,
                    public_message="Reminder List data is inconsistent",
                )
            unique[record.list_id] = record
        lists = [self._to_list(record) for record in unique.values()]
        lists.sort(key=_reminder_list_page_key)
        page, next_cursor = paginate(
            lists,
            key=_reminder_list_page_key,
            limit=limit,
            cursor=cursor,
        )
        return ReminderListContainerListResult(
            lists=page,
            next_cursor=next_cursor,
        )

    def create_list(
        self,
        *,
        title: str,
        source_id: str | None,
        color: str | None,
        idempotency_key: str,
    ) -> ReminderListContainerCreateResult:
        """Create and verify one EventKit Reminder List."""
        normalized_title = _normalize_title(title)
        normalized_color = _normalize_color(color)
        _require_non_empty(idempotency_key, "idempotency_key")
        source = self._select_write_source(source_id)
        request_digest = request_hash(
            {
                "source_id": source.source_id,
                "title": normalized_title,
                "color": normalized_color,
            }
        )
        flow = ControlledWrite(
            control=self._write_control,
            idempotency_key=idempotency_key,
            operation="reminders.create_list",
            request_hash=request_digest,
            confirmed_by_user=False,
            resource_name="Reminder List",
        )
        decision = flow.reserve()
        if decision.status == "deduplicated":
            return self._deduplicated_create(
                result_item_id=decision.result_item_id,
                idempotency_key=idempotency_key,
            )

        try:
            created = self._backend.create_reminder_list(
                source_id=source.source_id,
                title=normalized_title,
                color=normalized_color,
            )
        except Exception as error:
            flow.backend_failed(error)
        if not created.list_id.strip():
            flow.unverified_result()
        try:
            record = self._backend.get_reminder_list(list_id=created.list_id)
        except Exception:
            flow.unverified_result()
        if not _reminder_list_matches_create(
            record,
            source_id=source.source_id,
            title=normalized_title,
            color=normalized_color,
        ):
            flow.unverified_result()

        item_id = _stable_reminder_list_id(record.source_id, record.list_id)
        audit = AuditWrite(
            request_hash=request_digest,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=False,
        )
        try:
            self._write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation="reminders.create_list",
                item=_container_item(
                    item_id=item_id,
                    record=record,
                    created_by_mcp=True,
                ),
                source_refs=[],
                audit=audit,
                external_write_attempted=True,
            )
        except Exception as error:
            flow.finalization_failed(error, external_write_attempted=True)
        return ReminderListContainerCreateResult(
            list=self._to_list(record),
            created=True,
            deduplicated=False,
            audit_id=audit.audit_id,
        )

    def update_list(
        self,
        *,
        list_id: str,
        title: str | None,
        color: str | None,
        expected_state_token: str | None,
        idempotency_key: str,
    ) -> ReminderListContainerUpdateResult:
        """Update and verify one EventKit Reminder List."""
        _require_non_empty(list_id, "list_id")
        _require_non_empty(idempotency_key, "idempotency_key")
        normalized_title = _normalize_title(title) if title is not None else None
        normalized_color = _normalize_color(color)
        if normalized_title is None and normalized_color is None:
            raise ValueError("At least one update field is required")
        updated_fields = [
            field
            for field, value in (
                ("title", normalized_title),
                ("color", normalized_color),
            )
            if value is not None
        ]
        request_digest = request_hash(
            {
                "list_id": list_id,
                "title": normalized_title,
                "color": normalized_color,
                "expected_state_token": expected_state_token,
            }
        )
        flow = ControlledWrite(
            control=self._write_control,
            idempotency_key=idempotency_key,
            operation="reminders.update_list",
            request_hash=request_digest,
            confirmed_by_user=False,
            resource_name="Reminder List",
        )
        decision = flow.reserve()
        if decision.status == "deduplicated":
            return self._deduplicated_update(
                result_item_id=decision.result_item_id,
                idempotency_key=idempotency_key,
                updated_fields=updated_fields,
            )

        try:
            current = self._backend.get_reminder_list(list_id=list_id)
        except Exception as error:
            flow.preflight_failed(error)
        source = self._sources.get(current.source_id)
        if source is None:
            flow.preflight_failed(
                ToolContractError(
                    code="SOURCE_NOT_AUTHORIZED",
                    message="Reminder List Source is not authorized",
                    retryable=False,
                    public_message="Requested Reminder List Source is not authorized",
                )
            )
        if not source.allow_reminder_write:
            flow.preflight_failed(_read_only_error())
        if current.is_immutable or not current.allows_content_modifications:
            flow.preflight_failed(_read_only_error())
        if (
            expected_state_token is not None
            and _reminder_list_state_token(current) != expected_state_token
        ):
            flow.external_state_changed()

        try:
            self._backend.update_reminder_list(
                list_id=list_id,
                title=normalized_title,
                color=normalized_color,
            )
        except Exception as error:
            flow.backend_failed(error)
        try:
            record = self._backend.get_reminder_list(list_id=list_id)
        except Exception:
            flow.unverified_result()
        if not _reminder_list_matches_update(
            before=current,
            after=record,
            title=normalized_title,
            color=normalized_color,
        ):
            flow.unverified_result()

        existing_item = self._sidecar.find_mcp_item_by_external(
            item_type="reminder_list",
            external_id=list_id,
            external_container_id=current.source_id,
        )
        item_id = (
            str(existing_item["id"])
            if existing_item is not None
            else _stable_reminder_list_id(record.source_id, record.list_id)
        )
        audit = AuditWrite(
            request_hash=request_digest,
            result_status="succeeded",
            error_code=None,
            confirmed_by_user=False,
        )
        try:
            self._write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation="reminders.update_list",
                item=_container_item(
                    item_id=item_id,
                    record=record,
                    created_by_mcp=bool(
                        existing_item is not None and existing_item["created_by_mcp"]
                    ),
                ),
                source_refs=[],
                audit=audit,
                external_write_attempted=True,
            )
        except Exception as error:
            flow.finalization_failed(error, external_write_attempted=True)
        return ReminderListContainerUpdateResult(
            list=self._to_list(record),
            updated=True,
            deduplicated=False,
            updated_fields=updated_fields,
            audit_id=audit.audit_id,
        )

    def _select_source_ids(self, source_ids: list[str] | None) -> list[str]:
        if source_ids is None:
            return list(self._sources)
        unknown = sorted(set(source_ids) - self._sources.keys())
        if unknown:
            raise ValueError(f"Unknown EventKit source_ids: {', '.join(unknown)}")
        return list(dict.fromkeys(source_ids))

    def _select_write_source(self, source_id: str | None) -> EventKitSource:
        if source_id is not None:
            normalized_id = source_id.strip()
            source = self._sources.get(normalized_id)
            if source is None:
                raise ValueError(f"Unknown EventKit source_ids: {normalized_id}")
            if not source.allow_reminder_write:
                raise ToolContractError(
                    code="TARGET_READ_ONLY",
                    message="EventKit Source does not allow Reminder List writes",
                    retryable=False,
                    public_message="Requested EventKit Source is not write-enabled",
                )
            return source
        defaults = [source for source in self._sources.values() if source.default_reminder_source]
        if defaults:
            return defaults[0]
        writable = [source for source in self._sources.values() if source.allow_reminder_write]
        if len(writable) == 1:
            return writable[0]
        raise ValueError("source_id is required when no unique writable Reminder Source exists")

    def _to_list(self, record: ReminderListContainerRecord) -> ReminderListContainer:
        item = self._sidecar.find_mcp_item_by_external(
            item_type="reminder_list",
            external_id=record.list_id,
            external_container_id=record.source_id,
        )
        return ReminderListContainer(
            **record.model_dump(),
            state_token=_reminder_list_state_token(record),
            created_by_mcp=bool(item is not None and item["created_by_mcp"]),
        )

    def _deduplicated_create(
        self,
        *,
        result_item_id: str | None,
        idempotency_key: str,
    ) -> ReminderListContainerCreateResult:
        item = self._required_result_item(result_item_id)
        record = self._backend.get_reminder_list(list_id=str(item["external_id"]))
        _require_authorized_identity(record, item, self._sources)
        return ReminderListContainerCreateResult(
            list=self._to_list(record),
            created=False,
            deduplicated=True,
            audit_id=self._required_audit_id(
                idempotency_key=idempotency_key,
                operation="reminders.create_list",
            ),
        )

    def _deduplicated_update(
        self,
        *,
        result_item_id: str | None,
        idempotency_key: str,
        updated_fields: list[str],
    ) -> ReminderListContainerUpdateResult:
        item = self._required_result_item(result_item_id)
        record = self._backend.get_reminder_list(list_id=str(item["external_id"]))
        _require_authorized_identity(record, item, self._sources)
        return ReminderListContainerUpdateResult(
            list=self._to_list(record),
            updated=False,
            deduplicated=True,
            updated_fields=updated_fields,
            audit_id=self._required_audit_id(
                idempotency_key=idempotency_key,
                operation="reminders.update_list",
            ),
        )

    def _required_result_item(self, result_item_id: str | None) -> dict[str, object]:
        item = self._sidecar.get_mcp_item(result_item_id or "")
        if item is None or item["item_type"] != "reminder_list":
            raise ValueError("idempotency Reminder List result item is missing")
        return item

    def _required_audit_id(self, *, idempotency_key: str, operation: str) -> str:
        result = self._write_control.get_operation_result(
            idempotency_key=idempotency_key,
            operation=operation,
        )
        if result is None or result.audit_id is None:
            raise ValueError("idempotency audit is missing")
        return result.audit_id


def _container_item(
    *,
    item_id: str,
    record: ReminderListContainerRecord,
    created_by_mcp: bool,
) -> McpItemWrite:
    return McpItemWrite(
        item_id=item_id,
        item_type="reminder_list",
        external_id=record.list_id,
        external_container_id=record.source_id,
        status_semantics=None,
        created_by_mcp=created_by_mcp,
        completion_status=None,
    )


def _reminder_list_matches_create(
    record: ReminderListContainerRecord,
    *,
    source_id: str,
    title: str,
    color: str | None,
) -> bool:
    return (
        bool(record.list_id.strip())
        and record.source_id == source_id
        and record.title == title
        and (color is None or record.color == color)
    )


def _reminder_list_matches_update(
    *,
    before: ReminderListContainerRecord,
    after: ReminderListContainerRecord,
    title: str | None,
    color: str | None,
) -> bool:
    return (
        after.list_id == before.list_id
        and after.source_id == before.source_id
        and after.title == (title if title is not None else before.title)
        and after.color == (color if color is not None else before.color)
    )


def _require_authorized_identity(
    record: ReminderListContainerRecord,
    item: dict[str, object],
    sources: dict[str, EventKitSource],
) -> None:
    if (
        record.list_id != item["external_id"]
        or record.source_id != item["external_container_id"]
        or record.source_id not in sources
    ):
        raise ValueError("idempotency Reminder List result identity changed")


def _normalize_title(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("title must be a non-empty string")
    if len(normalized) > 255:
        raise ValueError("title must not exceed 255 characters")
    return normalized


def _normalize_title_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("title_query must be a non-empty string")
    return normalized


def _normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.upper()
    if (
        len(normalized) != 7
        or not normalized.startswith("#")
        or any(character not in "0123456789ABCDEF" for character in normalized[1:])
    ):
        raise ValueError("color must use #RRGGBB")
    return normalized


def _reminder_list_state_token(record: ReminderListContainerRecord) -> str:
    return "reminder-list-state:" + request_hash(record.model_dump(mode="json"))


def _reminder_list_page_key(reminder_list: ReminderListContainer) -> tuple[str, ...]:
    return (
        reminder_list.title.casefold(),
        reminder_list.source_id,
        reminder_list.list_id,
    )


def _stable_reminder_list_id(source_id: str, list_id: str) -> str:
    digest = request_hash(
        {
            "source_id": source_id,
            "list_id": list_id,
        }
    )
    return f"reminder-list:{digest[:32]}"


def _read_only_error() -> ToolContractError:
    return ToolContractError(
        code="TARGET_READ_ONLY",
        message="Reminder List cannot be modified",
        retryable=False,
        public_message="Requested Reminder List cannot be modified",
    )


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
