"""Apple Calendar query and write support."""

from personal_activity_mcp.calendar.backend import CalendarBackendError, MacOSCalendarBackend
from personal_activity_mcp.calendar.container_repository import CalendarContainerRepository
from personal_activity_mcp.calendar.models import (
    AllDayEventRange,
    CalendarContainer,
    CalendarContainerCreateResult,
    CalendarContainerListResult,
    CalendarContainerRecord,
    CalendarContainerUpdateResult,
    CalendarCreateResult,
    CalendarEventEvidence,
    CalendarEventRecord,
    CalendarListResult,
    CalendarTimeRange,
    CalendarUpdateResult,
    DescriptionUpdate,
    TimedEventRange,
)
from personal_activity_mcp.calendar.repository import CalendarRepository

__all__ = [
    "CalendarCreateResult",
    "CalendarContainer",
    "CalendarContainerCreateResult",
    "CalendarContainerListResult",
    "CalendarContainerRecord",
    "CalendarContainerRepository",
    "CalendarContainerUpdateResult",
    "AllDayEventRange",
    "CalendarEventEvidence",
    "CalendarEventRecord",
    "CalendarListResult",
    "CalendarRepository",
    "CalendarTimeRange",
    "CalendarUpdateResult",
    "DescriptionUpdate",
    "TimedEventRange",
    "CalendarBackendError",
    "MacOSCalendarBackend",
]
