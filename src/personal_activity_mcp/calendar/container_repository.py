"""Calendar container domain operations."""

from __future__ import annotations

from typing import Protocol

from personal_activity_mcp.calendar.models import (
    CalendarContainer,
    CalendarContainerCreateResult,
    CalendarContainerListResult,
    CalendarContainerRecord,
    CalendarContainerUpdateResult,
)
from personal_activity_mcp.common.container_repository import (
    ContainerKind,
    ContainerRepositoryCore,
)
from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.sidecar import SidecarRepository


class CalendarContainerBackend(Protocol):
    """Native Calendar container operations required by the repository."""

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

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> CalendarContainerRecord: ...

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> CalendarContainerRecord: ...


_CALENDAR_KIND = ContainerKind(
    item_type="calendar",
    id_field="calendar_id",
    resource_name="Calendar container",
    create_operation="calendar.create_calendar",
    update_operation="calendar.update_calendar",
    write_flag="allow_calendar_write",
    default_flag="default_calendar_source",
    stable_prefix="calendar",
    state_prefix="calendar-container-state:",
)


class CalendarContainerRepository:
    """Manage EventKit Calendar containers within configured Source scope."""

    def __init__(
        self,
        config: AppConfig,
        backend: CalendarContainerBackend,
        sidecar: SidecarRepository,
    ) -> None:
        self._core = ContainerRepositoryCore(
            config,
            sidecar,
            kind=_CALENDAR_KIND,
            list_records=lambda source_ids: backend.list_calendars(source_ids=source_ids),
            get_record=lambda calendar_id: backend.get_calendar(calendar_id=calendar_id),
            create_record=lambda source_id, title, color: backend.create_calendar(
                source_id=source_id,
                title=title,
                color=color,
            ),
            update_record=lambda calendar_id, title, color: backend.update_calendar(
                calendar_id=calendar_id,
                title=title,
                color=color,
            ),
        )

    def list_calendars(
        self,
        *,
        source_ids: list[str] | None,
        title_query: str | None,
        modifiable_only: bool,
        limit: int,
        cursor: str | None,
    ) -> CalendarContainerListResult:
        """List Event Calendar containers within configured EventKit Sources."""
        result = self._core.list(
            source_ids=source_ids,
            title_query=title_query,
            modifiable_only=modifiable_only,
            limit=limit,
            cursor=cursor,
        )
        return CalendarContainerListResult(
            calendars=[self._to_calendar(record) for record in result.records],
            next_cursor=result.next_cursor,
        )

    def create_calendar(
        self,
        *,
        title: str,
        source_id: str | None,
        color: str | None,
        idempotency_key: str,
    ) -> CalendarContainerCreateResult:
        """Create and verify one Event Calendar container."""
        result = self._core.create(
            title=title,
            source_id=source_id,
            color=color,
            idempotency_key=idempotency_key,
        )
        return CalendarContainerCreateResult(
            calendar=self._to_calendar(result.record),
            created=result.created,
            deduplicated=result.deduplicated,
            audit_id=result.audit_id,
        )

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
        expected_state_token: str | None,
        idempotency_key: str,
    ) -> CalendarContainerUpdateResult:
        """Update and verify one Event Calendar container."""
        result = self._core.update(
            container_id=calendar_id,
            title=title,
            color=color,
            expected_state_token=expected_state_token,
            idempotency_key=idempotency_key,
        )
        return CalendarContainerUpdateResult(
            calendar=self._to_calendar(result.record),
            updated=result.updated,
            deduplicated=result.deduplicated,
            updated_fields=result.updated_fields,
            audit_id=result.audit_id,
        )

    def _to_calendar(self, record: CalendarContainerRecord) -> CalendarContainer:
        return CalendarContainer(
            **record.model_dump(),
            state_token=self._core.state_token(record),
            created_by_mcp=self._core.created_by_mcp(record),
        )
