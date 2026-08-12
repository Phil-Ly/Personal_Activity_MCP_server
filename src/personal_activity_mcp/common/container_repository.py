"""Shared controlled-write flow for EventKit containers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar

from personal_activity_mcp.common.errors import ToolContractError
from personal_activity_mcp.common.pagination import paginate
from personal_activity_mcp.config import AppConfig, EventKitSource
from personal_activity_mcp.sidecar import (
    AuditWrite,
    ControlledWrite,
    McpItemWrite,
    SidecarRepository,
    WriteControl,
    request_hash,
)


class ContainerRecord(Protocol):
    source_id: str
    title: str
    color: str | None
    allows_content_modifications: bool
    is_immutable: bool

    def model_dump(self, *, mode: str = "python") -> dict[str, object]: ...


RecordT = TypeVar("RecordT", bound=ContainerRecord)


@dataclass(frozen=True)
class ContainerKind:
    item_type: Literal["calendar", "reminder_list"]
    id_field: str
    resource_name: str
    create_operation: str
    update_operation: str
    write_flag: str
    default_flag: str
    stable_prefix: str
    state_prefix: str


@dataclass(frozen=True)
class ContainerPage(Generic[RecordT]):
    records: list[RecordT]
    next_cursor: str | None


@dataclass(frozen=True)
class ContainerCreateState(Generic[RecordT]):
    record: RecordT
    created: bool
    deduplicated: bool
    audit_id: str


@dataclass(frozen=True)
class ContainerUpdateState(Generic[RecordT]):
    record: RecordT
    updated: bool
    deduplicated: bool
    updated_fields: list[str]
    audit_id: str


class ContainerRepositoryCore(Generic[RecordT]):
    """Implement one container kind without exposing generic MCP schemas."""

    def __init__(
        self,
        config: AppConfig,
        sidecar: SidecarRepository,
        *,
        kind: ContainerKind,
        list_records: Callable[[list[str]], list[RecordT]],
        get_record: Callable[[str], RecordT],
        create_record: Callable[[str, str, str | None], RecordT],
        update_record: Callable[[str, str | None, str | None], RecordT],
    ) -> None:
        self._kind = kind
        self._list_records = list_records
        self._get_record = get_record
        self._create_record = create_record
        self._update_record = update_record
        self._sidecar = sidecar
        self._write_control = WriteControl(sidecar)
        self._sources = {source.source_id: source for source in config.eventkit_sources}

    def list(
        self,
        *,
        source_ids: list[str] | None,
        title_query: str | None,
        modifiable_only: bool,
        limit: int,
        cursor: str | None,
    ) -> ContainerPage[RecordT]:
        selected_source_ids = self._select_source_ids(source_ids)
        normalized_query = _normalize_title_query(title_query)
        records = self._list_records(selected_source_ids)
        authorized = set(selected_source_ids)
        unique: dict[str, RecordT] = {}
        for record in records:
            if record.source_id not in authorized:
                continue
            if normalized_query is not None and normalized_query not in record.title.casefold():
                continue
            if modifiable_only and (not record.allows_content_modifications or record.is_immutable):
                continue
            record_id = self.record_id(record)
            existing = unique.get(record_id)
            if existing is not None and existing != record:
                raise ToolContractError(
                    code="BACKEND_FAILURE",
                    message=f"Conflicting {self._kind.resource_name} objects share one identifier",
                    retryable=True,
                    public_message=f"{self._kind.resource_name} data is inconsistent",
                )
            unique[record_id] = record
        ordered = sorted(unique.values(), key=self.page_key)
        page, next_cursor = paginate(
            ordered,
            key=self.page_key,
            limit=limit,
            cursor=cursor,
        )
        return ContainerPage(records=page, next_cursor=next_cursor)

    def create(
        self,
        *,
        title: str,
        source_id: str | None,
        color: str | None,
        idempotency_key: str,
    ) -> ContainerCreateState[RecordT]:
        normalized_title = _normalize_title(title)
        normalized_color = _normalize_color(color)
        _require_non_empty(idempotency_key, "idempotency_key")
        source = self._resolve_create_source(source_id)
        request_digest = request_hash(
            {
                "source_id": source.source_id,
                "title": normalized_title,
                "color": normalized_color,
            }
        )
        flow = self._flow(
            idempotency_key=idempotency_key,
            operation=self._kind.create_operation,
            request_digest=request_digest,
        )
        decision = flow.reserve()
        if decision.status == "deduplicated":
            return self._deduplicated_create(
                result_item_id=decision.result_item_id,
                idempotency_key=idempotency_key,
            )

        try:
            created = self._create_record(
                source.source_id,
                normalized_title,
                normalized_color,
            )
        except Exception as error:
            flow.backend_failed(error)
        if not self.record_id(created).strip():
            flow.unverified_result()
        try:
            record = self._get_record(self.record_id(created))
        except Exception:
            flow.unverified_result()
        if not self._matches_create(
            record,
            source_id=source.source_id,
            title=normalized_title,
            color=normalized_color,
        ):
            flow.unverified_result()

        item_id = self.stable_id(record)
        audit = AuditWrite(
            request_hash=request_digest,
            result_status="succeeded",
            error_code=None,
        )
        try:
            self._write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation=self._kind.create_operation,
                item=self._item(item_id=item_id, record=record, created_by_mcp=True),
                source_refs=[],
                audit=audit,
                external_write_attempted=True,
            )
        except Exception as error:
            flow.finalization_failed(error, external_write_attempted=True)
        return ContainerCreateState(
            record=record,
            created=True,
            deduplicated=False,
            audit_id=audit.audit_id,
        )

    def update(
        self,
        *,
        container_id: str,
        title: str | None,
        color: str | None,
        expected_state_token: str | None,
        idempotency_key: str,
    ) -> ContainerUpdateState[RecordT]:
        _require_non_empty(container_id, self._kind.id_field)
        _require_non_empty(idempotency_key, "idempotency_key")
        normalized_title = _normalize_title(title) if title is not None else None
        normalized_color = _normalize_color(color)
        if normalized_title is None and normalized_color is None:
            raise ValueError("At least one update field is required")
        updated_fields = [
            field
            for field, value in (("title", normalized_title), ("color", normalized_color))
            if value is not None
        ]
        request_digest = request_hash(
            {
                "container_id": container_id,
                "title": normalized_title,
                "color": normalized_color,
                "expected_state_token": expected_state_token,
            }
        )
        flow = self._flow(
            idempotency_key=idempotency_key,
            operation=self._kind.update_operation,
            request_digest=request_digest,
        )
        decision = flow.reserve()
        if decision.status == "deduplicated":
            return self._deduplicated_update(
                result_item_id=decision.result_item_id,
                idempotency_key=idempotency_key,
                updated_fields=updated_fields,
            )

        try:
            current = self._get_record(container_id)
        except Exception as error:
            flow.preflight_failed(error)
        source = self._sources.get(current.source_id)
        if source is None:
            flow.preflight_failed(
                ToolContractError(
                    code="SOURCE_NOT_AUTHORIZED",
                    message=f"{self._kind.resource_name} Source is not authorized",
                    retryable=False,
                    public_message=f"Requested {self._kind.resource_name} Source is not authorized",
                )
            )
        if not self._source_allows_write(source):
            flow.preflight_failed(self._read_only_error())
        if current.is_immutable or not current.allows_content_modifications:
            flow.preflight_failed(self._read_only_error())
        if expected_state_token is not None and self.state_token(current) != expected_state_token:
            flow.external_state_changed()

        try:
            self._update_record(container_id, normalized_title, normalized_color)
        except Exception as error:
            flow.backend_failed(error)
        try:
            record = self._get_record(container_id)
        except Exception:
            flow.unverified_result()
        if not self._matches_update(
            before=current,
            after=record,
            title=normalized_title,
            color=normalized_color,
        ):
            flow.unverified_result()

        existing_item = self._sidecar.find_mcp_item_by_external(
            item_type=self._kind.item_type,
            external_id=container_id,
            external_container_id=current.source_id,
        )
        item_id = str(existing_item["id"]) if existing_item is not None else self.stable_id(record)
        audit = AuditWrite(
            request_hash=request_digest,
            result_status="succeeded",
            error_code=None,
        )
        try:
            self._write_control.finalize_success(
                idempotency_key=idempotency_key,
                operation=self._kind.update_operation,
                item=self._item(
                    item_id=item_id,
                    record=record,
                    created_by_mcp=bool(existing_item and existing_item["created_by_mcp"]),
                ),
                source_refs=[],
                audit=audit,
                external_write_attempted=True,
            )
        except Exception as error:
            flow.finalization_failed(error, external_write_attempted=True)
        return ContainerUpdateState(
            record=record,
            updated=True,
            deduplicated=False,
            updated_fields=updated_fields,
            audit_id=audit.audit_id,
        )

    def record_id(self, record: RecordT) -> str:
        return str(getattr(record, self._kind.id_field))

    def state_token(self, record: RecordT) -> str:
        return self._kind.state_prefix + request_hash(record.model_dump(mode="json"))

    def stable_id(self, record: RecordT) -> str:
        digest = request_hash(
            {
                "source_id": record.source_id,
                "container_id": self.record_id(record),
            }
        )
        return f"{self._kind.stable_prefix}:{digest[:32]}"

    def created_by_mcp(self, record: RecordT) -> bool:
        item = self._sidecar.find_mcp_item_by_external(
            item_type=self._kind.item_type,
            external_id=self.record_id(record),
            external_container_id=record.source_id,
        )
        return bool(item is not None and item["created_by_mcp"])

    def page_key(self, record: RecordT) -> tuple[str, ...]:
        return (record.title.casefold(), record.source_id, self.record_id(record))

    def _flow(
        self,
        *,
        idempotency_key: str,
        operation: str,
        request_digest: str,
    ) -> ControlledWrite:
        return ControlledWrite(
            control=self._write_control,
            idempotency_key=idempotency_key,
            operation=operation,
            request_hash=request_digest,
            resource_name=self._kind.resource_name,
        )

    def _select_source_ids(self, source_ids: list[str] | None) -> list[str]:
        if source_ids is None:
            return list(self._sources)
        selected = list(dict.fromkeys(_normalize_id(value, "source_id") for value in source_ids))
        unknown = [source_id for source_id in selected if source_id not in self._sources]
        if unknown:
            raise ValueError(f"Unknown EventKit source_ids: {', '.join(sorted(unknown))}")
        return selected

    def _resolve_create_source(self, source_id: str | None) -> EventKitSource:
        if source_id is not None:
            normalized_id = _normalize_id(source_id, "source_id")
            source = self._sources.get(normalized_id)
            if source is None:
                raise ValueError(f"Unknown EventKit source_ids: {normalized_id}")
            if not self._source_allows_write(source):
                raise self._read_only_error()
            return source
        defaults = [
            source for source in self._sources.values() if getattr(source, self._kind.default_flag)
        ]
        if defaults:
            return defaults[0]
        writable = [
            source for source in self._sources.values() if self._source_allows_write(source)
        ]
        if len(writable) == 1:
            return writable[0]
        raise ValueError(
            f"source_id is required when no unique writable "
            f"{self._kind.resource_name} Source exists"
        )

    def _source_allows_write(self, source: EventKitSource) -> bool:
        return bool(getattr(source, self._kind.write_flag))

    def _deduplicated_create(
        self,
        *,
        result_item_id: str | None,
        idempotency_key: str,
    ) -> ContainerCreateState[RecordT]:
        item = self._required_result_item(result_item_id)
        record = self._get_record(str(item["external_id"]))
        self._require_authorized_identity(record, item)
        return ContainerCreateState(
            record=record,
            created=False,
            deduplicated=True,
            audit_id=self._required_audit_id(
                idempotency_key=idempotency_key,
                operation=self._kind.create_operation,
            ),
        )

    def _deduplicated_update(
        self,
        *,
        result_item_id: str | None,
        idempotency_key: str,
        updated_fields: list[str],
    ) -> ContainerUpdateState[RecordT]:
        item = self._required_result_item(result_item_id)
        record = self._get_record(str(item["external_id"]))
        self._require_authorized_identity(record, item)
        return ContainerUpdateState(
            record=record,
            updated=False,
            deduplicated=True,
            updated_fields=updated_fields,
            audit_id=self._required_audit_id(
                idempotency_key=idempotency_key,
                operation=self._kind.update_operation,
            ),
        )

    def _required_result_item(self, result_item_id: str | None) -> dict[str, object]:
        item = self._sidecar.get_mcp_item(result_item_id or "")
        if item is None or item["item_type"] != self._kind.item_type:
            raise ValueError(f"idempotency {self._kind.resource_name} result item is missing")
        return item

    def _required_audit_id(self, *, idempotency_key: str, operation: str) -> str:
        result = self._write_control.get_operation_result(
            idempotency_key=idempotency_key,
            operation=operation,
        )
        if result is None or result.audit_id is None:
            raise ValueError("idempotency audit is missing")
        return result.audit_id

    def _require_authorized_identity(
        self,
        record: RecordT,
        item: dict[str, object],
    ) -> None:
        if (
            self.record_id(record) != item["external_id"]
            or record.source_id != item["external_container_id"]
            or record.source_id not in self._sources
        ):
            raise ValueError(f"idempotency {self._kind.resource_name} result identity changed")

    def _item(
        self,
        *,
        item_id: str,
        record: RecordT,
        created_by_mcp: bool,
    ) -> McpItemWrite:
        return McpItemWrite(
            item_id=item_id,
            item_type=self._kind.item_type,
            external_id=self.record_id(record),
            external_container_id=record.source_id,
            status_semantics=None,
            created_by_mcp=created_by_mcp,
        )

    def _matches_create(
        self,
        record: RecordT,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> bool:
        return (
            bool(self.record_id(record).strip())
            and record.source_id == source_id
            and record.title == title
            and (color is None or record.color == color)
        )

    def _matches_update(
        self,
        *,
        before: RecordT,
        after: RecordT,
        title: str | None,
        color: str | None,
    ) -> bool:
        return (
            self.record_id(after) == self.record_id(before)
            and after.source_id == before.source_id
            and after.title == (title if title is not None else before.title)
            and after.color == (color if color is not None else before.color)
        )

    def _read_only_error(self) -> ToolContractError:
        return ToolContractError(
            code="TARGET_READ_ONLY",
            message=f"{self._kind.resource_name} cannot be modified",
            retryable=False,
            public_message=f"Requested {self._kind.resource_name} cannot be modified",
        )


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
        raise ValueError("color must use #RRGGBB format")
    return normalized


def _normalize_id(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
