"""Apple Calendar query and write support."""

from personal_activity_mcp.calendar.backend import CalendarBackendError, MacOSCalendarBackend
from personal_activity_mcp.calendar.models import (
    CalendarCreateResult,
    CalendarEnsureRecord,
    CalendarEventEvidence,
    CalendarEventRecord,
    CalendarListResult,
    CalendarTimeRange,
    CalendarUpdateResult,
)
from personal_activity_mcp.calendar.repository import CalendarRepository

__all__ = [
    "CalendarCreateResult",
    "CalendarEnsureRecord",
    "CalendarEventEvidence",
    "CalendarEventRecord",
    "CalendarListResult",
    "CalendarRepository",
    "CalendarTimeRange",
    "CalendarUpdateResult",
    "CalendarBackendError",
    "MacOSCalendarBackend",
]
