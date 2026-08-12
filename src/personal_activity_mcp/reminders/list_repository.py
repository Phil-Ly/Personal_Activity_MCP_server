"""Reminder List container domain operations."""

from __future__ import annotations

from typing import Protocol

from personal_activity_mcp.common.container_repository import (
    ContainerKind,
    ContainerRepositoryCore,
)
from personal_activity_mcp.config import AppConfig
from personal_activity_mcp.reminders.models import (
    ReminderListContainer,
    ReminderListContainerCreateResult,
    ReminderListContainerListResult,
    ReminderListContainerRecord,
    ReminderListContainerUpdateResult,
)
from personal_activity_mcp.sidecar import SidecarRepository


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


_REMINDER_LIST_KIND = ContainerKind(
    item_type="reminder_list",
    id_field="list_id",
    resource_name="Reminder List",
    create_operation="reminders.create_list",
    update_operation="reminders.update_list",
    write_flag="allow_reminder_write",
    default_flag="default_reminder_source",
    stable_prefix="reminder-list",
    state_prefix="reminder-list-state:",
)


class ReminderListRepository:
    """Manage EventKit Reminder Lists within configured Source scope."""

    def __init__(
        self,
        config: AppConfig,
        backend: ReminderListBackend,
        sidecar: SidecarRepository,
    ) -> None:
        self._core = ContainerRepositoryCore(
            config,
            sidecar,
            kind=_REMINDER_LIST_KIND,
            list_records=lambda source_ids: backend.list_reminder_lists(source_ids=source_ids),
            get_record=lambda list_id: backend.get_reminder_list(list_id=list_id),
            create_record=lambda source_id, title, color: backend.create_reminder_list(
                source_id=source_id,
                title=title,
                color=color,
            ),
            update_record=lambda list_id, title, color: backend.update_reminder_list(
                list_id=list_id,
                title=title,
                color=color,
            ),
        )

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
        result = self._core.list(
            source_ids=source_ids,
            title_query=title_query,
            modifiable_only=modifiable_only,
            limit=limit,
            cursor=cursor,
        )
        return ReminderListContainerListResult(
            lists=[self._to_list(record) for record in result.records],
            next_cursor=result.next_cursor,
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
        result = self._core.create(
            title=title,
            source_id=source_id,
            color=color,
            idempotency_key=idempotency_key,
        )
        return ReminderListContainerCreateResult(
            list=self._to_list(result.record),
            created=result.created,
            deduplicated=result.deduplicated,
            audit_id=result.audit_id,
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
        result = self._core.update(
            container_id=list_id,
            title=title,
            color=color,
            expected_state_token=expected_state_token,
            idempotency_key=idempotency_key,
        )
        return ReminderListContainerUpdateResult(
            list=self._to_list(result.record),
            updated=result.updated,
            deduplicated=result.deduplicated,
            updated_fields=result.updated_fields,
            audit_id=result.audit_id,
        )

    def _to_list(self, record: ReminderListContainerRecord) -> ReminderListContainer:
        return ReminderListContainer(
            **record.model_dump(),
            state_token=self._core.state_token(record),
            created_by_mcp=self._core.created_by_mcp(record),
        )
