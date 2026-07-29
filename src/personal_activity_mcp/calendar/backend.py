"""macOS Calendar backend implemented through EventKit."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from personal_activity_mcp.calendar.models import (
    CalendarContainerRecord,
    CalendarEventRecord,
    DescriptionUpdate,
)
from personal_activity_mcp.common.eventkit import (
    EventKitCalendarData,
    EventKitClient,
    EventKitClientError,
    EventKitEventData,
)


class CalendarBackendError(RuntimeError):
    """Raised when EventKit Calendar access fails."""

    def __init__(
        self,
        message: str,
        *,
        external_state_changed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.external_state_changed = external_state_changed


class CalendarEventKitClient(Protocol):
    """EventKit operations consumed by the Calendar backend."""

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[EventKitEventData]: ...

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
    ) -> EventKitEventData: ...

    def update_event_notes(
        self,
        *,
        event_id: str,
        calendar_id: str,
        notes: str | None,
    ) -> EventKitEventData: ...

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> EventKitEventData: ...

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[EventKitCalendarData]: ...

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> EventKitCalendarData: ...

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> EventKitCalendarData: ...

    def get_calendar(
        self,
        *,
        calendar_id: str,
    ) -> EventKitCalendarData: ...


class MacOSCalendarBackend:
    """Read and write Apple Calendar through a shared EventKit client."""

    def __init__(self, client: CalendarEventKitClient | None = None) -> None:
        self._client = client or EventKitClient()

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[CalendarEventRecord]:
        try:
            records = self._client.list_events(
                calendar_ids=calendar_ids,
                start=start,
                end=end,
                include_notes=include_notes,
                include_location=include_location,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return [_to_calendar_record(record) for record in records]

    def list_calendars(
        self,
        *,
        source_ids: list[str],
    ) -> list[CalendarContainerRecord]:
        try:
            records = self._client.list_calendars(source_ids=source_ids)
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return [_to_container_record(record) for record in records]

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> CalendarContainerRecord:
        try:
            record = self._client.create_calendar(
                source_id=source_id,
                title=title,
                color=color,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_container_record(record)

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> CalendarContainerRecord:
        try:
            record = self._client.update_calendar(
                calendar_id=calendar_id,
                title=title,
                color=color,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_container_record(record)

    def get_calendar(
        self,
        *,
        calendar_id: str,
    ) -> CalendarContainerRecord:
        try:
            record = self._client.get_calendar(calendar_id=calendar_id)
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_container_record(record)

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
    ) -> CalendarEventRecord:
        try:
            record = self._client.create_event(
                calendar_id=calendar_id,
                title=title,
                start=start,
                end=end,
                is_all_day=is_all_day,
                notes=notes,
                location=location,
                timezone=timezone,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_calendar_record(record)

    def update_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
        description: DescriptionUpdate,
    ) -> CalendarEventRecord:
        notes = description.value if description.operation == "set" else None
        try:
            record = self._client.update_event_notes(
                event_id=event_id,
                calendar_id=calendar_id,
                notes=notes,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_calendar_record(record)

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> CalendarEventRecord:
        try:
            record = self._client.get_event(
                event_id=event_id,
                calendar_id=calendar_id,
            )
        except EventKitClientError as error:
            raise _backend_error(error) from error
        return _to_calendar_record(record)


def _to_calendar_record(record: EventKitEventData) -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id=record.event_id,
        calendar_id=record.calendar_id,
        title=record.title,
        start=record.start,
        end=record.end,
        is_all_day=record.is_all_day,
        start_date=record.start_date,
        end_date=record.end_date,
        location=record.location,
        notes=record.notes,
    )


def _to_container_record(record: EventKitCalendarData) -> CalendarContainerRecord:
    return CalendarContainerRecord(
        calendar_id=record.calendar_id,
        source_id=record.source_id,
        source_title=record.source_title,
        title=record.title,
        color=record.color,
        calendar_type=record.calendar_type,
        allows_content_modifications=record.allows_content_modifications,
        is_immutable=record.is_immutable,
        is_subscribed=record.is_subscribed,
    )


def _backend_error(error: EventKitClientError) -> CalendarBackendError:
    return CalendarBackendError(
        str(error),
        external_state_changed=error.external_state_changed,
    )
