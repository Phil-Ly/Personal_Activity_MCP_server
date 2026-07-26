"""Apple Calendar query and write support."""

from personal_activity_mcp.calendar.backend import CalendarBackendError, MacOSCalendarBackend
from personal_activity_mcp.calendar.models import (
    AllDayEventRange,
    CalendarCreateResult,
    CalendarEventEvidence,
    CalendarEventRecord,
    CalendarListResult,
    CalendarTimeRange,
    CalendarUpdateResult,
    TimedEventRange,
)
from personal_activity_mcp.calendar.repository import CalendarRepository

__all__ = [
    "CalendarCreateResult",
    "AllDayEventRange",
    "CalendarEventEvidence",
    "CalendarEventRecord",
    "CalendarListResult",
    "CalendarRepository",
    "CalendarTimeRange",
    "CalendarUpdateResult",
    "TimedEventRange",
    "CalendarBackendError",
    "MacOSCalendarBackend",
]
