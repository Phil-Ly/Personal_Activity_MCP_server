from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from personal_activity_mcp.common.eventkit import (
    EventKitClient,
    EventKitClientError,
)


class FakeNSDate:
    def __init__(self, timestamp: float) -> None:
        self.timestamp = timestamp

    @classmethod
    def dateWithTimeIntervalSince1970_(cls, timestamp: float) -> FakeNSDate:
        return cls(timestamp)

    def timeIntervalSince1970(self) -> float:
        return self.timestamp


class FakeNSDateComponents:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    @classmethod
    def alloc(cls) -> type[FakeNSDateComponents]:
        return cls

    @classmethod
    def init(cls) -> FakeNSDateComponents:
        return cls()

    def setCalendar_(self, value: object) -> None:
        self.values["calendar"] = value

    def setYear_(self, value: int) -> None:
        self.values["year"] = value

    def setMonth_(self, value: int) -> None:
        self.values["month"] = value

    def setDay_(self, value: int) -> None:
        self.values["day"] = value

    def year(self) -> int:
        return int(self.values.get("year", FAKE_UNDEFINED))

    def month(self) -> int:
        return int(self.values.get("month", FAKE_UNDEFINED))

    def day(self) -> int:
        return int(self.values.get("day", FAKE_UNDEFINED))

    def hour(self) -> int:
        return int(self.values.get("hour", FAKE_UNDEFINED))

    def minute(self) -> int:
        return int(self.values.get("minute", FAKE_UNDEFINED))

    def second(self) -> int:
        return int(self.values.get("second", FAKE_UNDEFINED))

    def calendar(self) -> object | None:
        return self.values.get("calendar")

    def timeZone(self) -> object | None:
        return self.values.get("timezone")


class FakeNSCalendar:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier

    @classmethod
    def calendarWithIdentifier_(cls, identifier: str) -> FakeNSCalendar:
        return cls(identifier)

    def dateFromComponents_(self, components: FakeNSDateComponents) -> FakeNSDate:
        value = datetime(
            components.year(),
            components.month(),
            components.day(),
            0 if components.hour() == FAKE_UNDEFINED else components.hour(),
            0 if components.minute() == FAKE_UNDEFINED else components.minute(),
            0 if components.second() == FAKE_UNDEFINED else components.second(),
            tzinfo=UTC,
        )
        return FakeNSDate(value.timestamp())


class FakeNSTimeZone:
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def timeZoneWithName_(cls, name: str) -> FakeNSTimeZone:
        return cls(name)


FAKE_UNDEFINED = 2**63 - 1
FAKE_FOUNDATION = SimpleNamespace(
    NSDate=FakeNSDate,
    NSDateComponents=FakeNSDateComponents,
    NSCalendar=FakeNSCalendar,
    NSTimeZone=FakeNSTimeZone,
    NSCalendarIdentifierGregorian="gregorian",
    NSDateComponentUndefined=FAKE_UNDEFINED,
)


class FakeNSColor:
    def __init__(self, red: float, green: float, blue: float, alpha: float) -> None:
        self.red = red
        self.green = green
        self.blue = blue
        self.alpha = alpha

    @classmethod
    def colorWithSRGBRed_green_blue_alpha_(
        cls,
        red: float,
        green: float,
        blue: float,
        alpha: float,
    ) -> FakeNSColor:
        return cls(red, green, blue, alpha)

    def colorUsingColorSpace_(self, color_space: object) -> FakeNSColor:
        del color_space
        return self

    def redComponent(self) -> float:
        return self.red

    def greenComponent(self) -> float:
        return self.green

    def blueComponent(self) -> float:
        return self.blue


class FakeNSColorSpace:
    @classmethod
    def sRGBColorSpace(cls) -> str:
        return "srgb"


FAKE_APPKIT = SimpleNamespace(
    NSColor=FakeNSColor,
    NSColorSpace=FakeNSColorSpace,
)


class FakeSource:
    def __init__(self, identifier: str, title: str) -> None:
        self.identifier = identifier
        self.source_title = title

    def sourceIdentifier(self) -> str:
        return self.identifier

    def title(self) -> str:
        return self.source_title


class FakeCalendar:
    def __init__(
        self,
        identifier: str | None,
        title: str,
        *,
        source: FakeSource | None = None,
        color: FakeNSColor | None = None,
        calendar_type: int = 1,
        allows_content_modifications: bool = True,
        immutable: bool = False,
        subscribed: bool = False,
        allowed_entity_types: int = 1,
    ) -> None:
        self.identifier = identifier
        self.calendar_title = title
        self.calendar_source = source or FakeSource("source-default", "Default")
        self.calendar_color = color or FakeNSColor(0.2, 0.4, 0.6, 1.0)
        self.calendar_type = calendar_type
        self.modifiable = allows_content_modifications
        self.immutable = immutable
        self.subscribed = subscribed
        self.entity_mask = allowed_entity_types

    @classmethod
    def calendarForEntityType_eventStore_(
        cls,
        entity_type: int,
        store: FakeStore,
    ) -> FakeCalendar:
        calendar = cls(
            None,
            "",
            allowed_entity_types=1 if entity_type == 0 else 2,
        )
        store.created_calendar = calendar
        return calendar

    def calendarIdentifier(self) -> str | None:
        return self.identifier

    def title(self) -> str:
        return self.calendar_title

    def setTitle_(self, title: str) -> None:
        self.calendar_title = title

    def source(self) -> FakeSource:
        return self.calendar_source

    def setSource_(self, source: FakeSource) -> None:
        self.calendar_source = source

    def color(self) -> FakeNSColor:
        return self.calendar_color

    def setColor_(self, color: FakeNSColor) -> None:
        self.calendar_color = color

    def type(self) -> int:
        return self.calendar_type

    def allowsContentModifications(self) -> bool:
        return self.modifiable

    def isImmutable(self) -> bool:
        return self.immutable

    def isSubscribed(self) -> bool:
        return self.subscribed

    def allowedEntityTypes(self) -> int:
        return self.entity_mask


class FakeEvent:
    def __init__(
        self,
        *,
        event_id: str | None,
        calendar: FakeCalendar | None,
        title: str = "",
        start: datetime | None = None,
        end: datetime | None = None,
        is_all_day: bool = False,
        location: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.event_id = event_id
        self.event_calendar = calendar
        self.event_title = title
        self.event_start = _native_date(start or datetime(2026, 7, 8, 10, tzinfo=UTC))
        self.event_end = _native_date(end or datetime(2026, 7, 8, 11, tzinfo=UTC))
        self.all_day = is_all_day
        self.event_location = location
        self.event_notes = notes
        self.event_timezone: FakeNSTimeZone | None = None

    @classmethod
    def eventWithEventStore_(cls, store: FakeStore) -> FakeEvent:
        event = cls(event_id=None, calendar=None)
        store.created_event = event
        return event

    def eventIdentifier(self) -> str | None:
        return self.event_id

    def calendar(self) -> FakeCalendar | None:
        return self.event_calendar

    def title(self) -> str:
        return self.event_title

    def startDate(self) -> FakeNSDate:
        return self.event_start

    def endDate(self) -> FakeNSDate:
        return self.event_end

    def isAllDay(self) -> bool:
        return self.all_day

    def location(self) -> str | None:
        return self.event_location

    def notes(self) -> str | None:
        return self.event_notes

    def setCalendar_(self, value: FakeCalendar) -> None:
        self.event_calendar = value

    def setTitle_(self, value: str) -> None:
        self.event_title = value

    def setStartDate_(self, value: FakeNSDate) -> None:
        self.event_start = value

    def setEndDate_(self, value: FakeNSDate) -> None:
        self.event_end = value

    def setAllDay_(self, value: bool) -> None:
        self.all_day = value

    def setNotes_(self, value: str | None) -> None:
        self.event_notes = value

    def setLocation_(self, value: str | None) -> None:
        self.event_location = value

    def setTimeZone_(self, value: FakeNSTimeZone) -> None:
        self.event_timezone = value


class FakeReminder:
    def __init__(
        self,
        *,
        reminder_id: str | None,
        calendar: FakeCalendar | None,
        title: str = "",
        notes: str | None = None,
        due_components: FakeNSDateComponents | None = None,
        priority: int = 0,
        completed: bool = False,
        completion_date: datetime | None = None,
    ) -> None:
        self.reminder_id = reminder_id
        self.reminder_calendar = calendar
        self.reminder_title = title
        self.reminder_notes = notes
        self.due_components = due_components
        self.reminder_priority = priority
        self.completed = completed
        self.completed_at = _native_date(completion_date) if completion_date else None

    @classmethod
    def reminderWithEventStore_(cls, store: FakeStore) -> FakeReminder:
        reminder = cls(reminder_id=None, calendar=None)
        store.created_reminder = reminder
        return reminder

    def calendarItemIdentifier(self) -> str | None:
        return self.reminder_id

    def calendar(self) -> FakeCalendar | None:
        return self.reminder_calendar

    def title(self) -> str:
        return self.reminder_title

    def notes(self) -> str | None:
        return self.reminder_notes

    def dueDateComponents(self) -> FakeNSDateComponents | None:
        return self.due_components

    def priority(self) -> int:
        return self.reminder_priority

    def isCompleted(self) -> bool:
        return self.completed

    def completionDate(self) -> FakeNSDate | None:
        return self.completed_at

    def setCalendar_(self, value: FakeCalendar) -> None:
        self.reminder_calendar = value

    def setTitle_(self, value: str) -> None:
        self.reminder_title = value

    def setNotes_(self, value: str | None) -> None:
        self.reminder_notes = value

    def setDueDateComponents_(self, value: FakeNSDateComponents | None) -> None:
        self.due_components = value

    def setPriority_(self, value: int) -> None:
        self.reminder_priority = value

    def setCompleted_(self, value: bool) -> None:
        self.completed = value

    def setCompletionDate_(self, value: FakeNSDate) -> None:
        self.completed_at = value


class FakeStore:
    def __init__(
        self,
        *,
        event_calendars: list[FakeCalendar] | None = None,
        reminder_calendars: list[FakeCalendar] | None = None,
        events: list[FakeEvent] | None = None,
        reminders: list[FakeReminder] | None = None,
        sources: list[FakeSource] | None = None,
    ) -> None:
        self.event_calendars = event_calendars or []
        self.reminder_calendars = reminder_calendars or []
        self.events = events or []
        self.reminders = reminders or []
        derived_sources = {
            calendar.source().sourceIdentifier(): calendar.source()
            for calendar in [*self.event_calendars, *self.reminder_calendars]
        }
        self.sources = sources or list(derived_sources.values())
        self.created_event: FakeEvent | None = None
        self.created_reminder: FakeReminder | None = None
        self.created_calendar: FakeCalendar | None = None
        self.saved_events: list[tuple[FakeEvent, int, bool]] = []
        self.saved_reminders: list[tuple[FakeReminder, bool]] = []
        self.saved_calendars: list[tuple[FakeCalendar, bool]] = []
        self.save_event_result: tuple[bool, object | None] = (True, None)
        self.save_reminder_result: tuple[bool, object | None] = (True, None)
        self.save_calendar_result: tuple[bool, object | None] = (True, None)
        self.assign_event_identifier_on_save = True
        self.assign_reminder_identifier_on_save = True
        self.assign_calendar_identifier_on_save = True
        self.event_access_result: tuple[bool, object | None] = (True, None)
        self.reminder_access_result: tuple[bool, object | None] = (True, None)
        self.requested_access: list[str] = []

    def requestFullAccessToEventsWithCompletion_(self, callback: Any) -> None:
        self.requested_access.append("event")
        callback(*self.event_access_result)

    def requestFullAccessToRemindersWithCompletion_(self, callback: Any) -> None:
        self.requested_access.append("reminder")
        callback(*self.reminder_access_result)

    def calendarsForEntityType_(self, entity_type: int) -> list[FakeCalendar]:
        if entity_type == 0:
            return self.event_calendars
        return self.reminder_calendars

    def sourceWithIdentifier_(self, source_id: str) -> FakeSource | None:
        return next(
            (source for source in self.sources if source.sourceIdentifier() == source_id),
            None,
        )

    def calendarWithIdentifier_(self, calendar_id: str) -> FakeCalendar | None:
        return next(
            (
                calendar
                for calendar in [*self.event_calendars, *self.reminder_calendars]
                if calendar.calendarIdentifier() == calendar_id
            ),
            None,
        )

    def saveCalendar_commit_error_(
        self,
        calendar: FakeCalendar,
        commit: bool,
        error: None,
    ) -> tuple[bool, object | None]:
        del error
        self.saved_calendars.append((calendar, commit))
        if (
            self.save_calendar_result[0]
            and self.assign_calendar_identifier_on_save
            and calendar.calendarIdentifier() is None
        ):
            if calendar.allowedEntityTypes() == 1:
                calendar.identifier = "calendar-created"
                self.event_calendars.append(calendar)
            else:
                calendar.identifier = "list-created"
                self.reminder_calendars.append(calendar)
        return self.save_calendar_result

    def predicateForEventsWithStartDate_endDate_calendars_(
        self,
        start: FakeNSDate,
        end: FakeNSDate,
        calendars: list[FakeCalendar],
    ) -> tuple[str, FakeNSDate, FakeNSDate, list[FakeCalendar]]:
        return ("events", start, end, calendars)

    def eventsMatchingPredicate_(
        self,
        predicate: tuple[str, FakeNSDate, FakeNSDate, list[FakeCalendar]],
    ) -> list[FakeEvent]:
        calendar_ids = {calendar.calendarIdentifier() for calendar in predicate[3]}
        return [
            event
            for event in self.events
            if event.calendar() is not None
            and event.calendar().calendarIdentifier() in calendar_ids
        ]

    def eventWithIdentifier_(self, event_id: str) -> FakeEvent | None:
        return next((event for event in self.events if event.eventIdentifier() == event_id), None)

    def saveEvent_span_commit_error_(
        self,
        event: FakeEvent,
        span: int,
        commit: bool,
        error: None,
    ) -> tuple[bool, object | None]:
        del error
        self.saved_events.append((event, span, commit))
        if (
            self.save_event_result[0]
            and self.assign_event_identifier_on_save
            and event.eventIdentifier() is None
        ):
            event.event_id = "event-created"
            self.events.append(event)
        return self.save_event_result

    def predicateForRemindersInCalendars_(
        self,
        calendars: list[FakeCalendar],
    ) -> tuple[str, list[FakeCalendar]]:
        return ("reminders", calendars)

    def fetchRemindersMatchingPredicate_completion_(
        self,
        predicate: tuple[str, list[FakeCalendar]],
        callback: Any,
    ) -> str:
        calendar_ids = {calendar.calendarIdentifier() for calendar in predicate[1]}
        callback(
            [
                reminder
                for reminder in self.reminders
                if reminder.calendar() is not None
                and reminder.calendar().calendarIdentifier() in calendar_ids
            ]
        )
        return "fetch-token"

    def calendarItemWithIdentifier_(self, reminder_id: str) -> FakeReminder | None:
        return next(
            (
                reminder
                for reminder in self.reminders
                if reminder.calendarItemIdentifier() == reminder_id
            ),
            None,
        )

    def saveReminder_commit_error_(
        self,
        reminder: FakeReminder,
        commit: bool,
        error: None,
    ) -> tuple[bool, object | None]:
        del error
        self.saved_reminders.append((reminder, commit))
        if (
            self.save_reminder_result[0]
            and self.assign_reminder_identifier_on_save
            and reminder.calendarItemIdentifier() is None
        ):
            reminder.reminder_id = "reminder-created"
            self.reminders.append(reminder)
        return self.save_reminder_result


def _native_date(value: datetime) -> FakeNSDate:
    return FakeNSDate(value.timestamp())


def _due_components(value: date) -> FakeNSDateComponents:
    components = FakeNSDateComponents()
    components.setCalendar_(FakeNSCalendar("gregorian"))
    components.setYear_(value.year)
    components.setMonth_(value.month)
    components.setDay_(value.day)
    return components


def _client(
    store: object,
    *,
    authorization_status: int = 3,
) -> EventKitClient:
    class FakeEventStoreAPI:
        @classmethod
        def authorizationStatusForEntityType_(cls, entity_type: int) -> int:
            del cls, entity_type
            return authorization_status

    eventkit = SimpleNamespace(
        EKEventStore=FakeEventStoreAPI,
        EKEvent=FakeEvent,
        EKReminder=FakeReminder,
        EKCalendar=FakeCalendar,
        EKEntityTypeEvent=0,
        EKEntityTypeReminder=1,
        EKEntityMaskEvent=1,
        EKEntityMaskReminder=2,
        EKSpanThisEvent=0,
        EKCalendarTypeLocal=0,
        EKCalendarTypeCalDAV=1,
        EKCalendarTypeExchange=2,
        EKCalendarTypeSubscription=3,
        EKCalendarTypeBirthday=4,
        EKAuthorizationStatusNotDetermined=0,
        EKAuthorizationStatusRestricted=1,
        EKAuthorizationStatusDenied=2,
        EKAuthorizationStatusFullAccess=3,
        EKAuthorizationStatusAuthorized=3,
    )
    return EventKitClient(
        store=store,
        eventkit_module=eventkit,
        foundation_module=FAKE_FOUNDATION,
        appkit_module=FAKE_APPKIT,
    )


def test_list_calendars_returns_only_requested_native_sources() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    exchange = FakeSource("source-exchange", "Exchange")
    selected = FakeCalendar(
        "calendar-1",
        "Plan",
        source=icloud,
        color=FakeNSColor(0.2, 0.4, 0.8, 1.0),
    )
    other = FakeCalendar("calendar-2", "Work", source=exchange)
    store = FakeStore(
        event_calendars=[selected, other],
        sources=[icloud, exchange],
    )
    client = _client(store)

    records = client.list_calendars(source_ids=["source-icloud"])

    assert len(records) == 1
    assert records[0].calendar_id == "calendar-1"
    assert records[0].source_id == "source-icloud"
    assert records[0].source_title == "iCloud"
    assert records[0].title == "Plan"
    assert records[0].color == "#3366CC"
    assert records[0].calendar_type == "caldav"
    assert records[0].allows_content_modifications is True
    assert records[0].is_immutable is False
    assert records[0].is_subscribed is False


def test_get_calendar_rejects_a_reminder_list_entity() -> None:
    reminder_source = FakeSource("source-icloud", "iCloud")
    reminder_list = FakeCalendar(
        "list-1",
        "Tasks",
        source=reminder_source,
        allowed_entity_types=2,
    )
    store = FakeStore(
        reminder_calendars=[reminder_list],
        sources=[reminder_source],
    )
    client = _client(store)

    with pytest.raises(EventKitClientError, match="not an Event Calendar"):
        client.get_calendar(calendar_id="list-1")


def test_create_calendar_sets_source_title_and_color_before_saving() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    store = FakeStore(sources=[icloud])
    client = _client(store)

    record = client.create_calendar(
        source_id="source-icloud",
        title="Japanese plan",
        color="#3366CC",
    )

    assert record.calendar_id == "calendar-created"
    assert record.source_id == "source-icloud"
    assert record.title == "Japanese plan"
    assert record.color == "#3366CC"
    assert store.created_calendar is not None
    assert store.created_calendar.source() is icloud
    assert store.saved_calendars == [(store.created_calendar, True)]


def test_update_calendar_changes_only_requested_properties_and_keeps_source() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    calendar = FakeCalendar("calendar-1", "Old", source=icloud)
    store = FakeStore(event_calendars=[calendar], sources=[icloud])
    client = _client(store)

    record = client.update_calendar(
        calendar_id="calendar-1",
        title="New",
        color="#112233",
    )

    assert record.title == "New"
    assert record.color == "#112233"
    assert calendar.source() is icloud
    assert store.saved_calendars == [(calendar, True)]


def test_update_calendar_rejects_immutable_target_before_save() -> None:
    calendar = FakeCalendar(
        "calendar-1",
        "Read only",
        immutable=True,
    )
    store = FakeStore(event_calendars=[calendar])
    client = _client(store)

    with pytest.raises(EventKitClientError, match="cannot be modified") as captured:
        client.update_calendar(
            calendar_id="calendar-1",
            title="Changed",
            color=None,
        )

    assert captured.value.external_state_changed is False
    assert store.saved_calendars == []


def test_list_reminder_lists_returns_only_requested_native_sources() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    exchange = FakeSource("source-exchange", "Exchange")
    selected = FakeCalendar(
        "list-1",
        "Plan steps",
        source=icloud,
        color=FakeNSColor(0.2, 0.4, 0.8, 1.0),
        allowed_entity_types=2,
    )
    other = FakeCalendar(
        "list-2",
        "Work",
        source=exchange,
        allowed_entity_types=2,
    )
    store = FakeStore(
        reminder_calendars=[selected, other],
        sources=[icloud, exchange],
    )
    client = _client(store)

    records = client.list_reminder_lists(source_ids=["source-icloud"])

    assert len(records) == 1
    assert records[0].list_id == "list-1"
    assert records[0].source_id == "source-icloud"
    assert records[0].source_title == "iCloud"
    assert records[0].title == "Plan steps"
    assert records[0].color == "#3366CC"
    assert records[0].calendar_type == "caldav"
    assert records[0].allows_content_modifications is True
    assert records[0].is_immutable is False
    assert records[0].is_subscribed is False


def test_get_reminder_list_rejects_an_event_calendar_entity() -> None:
    source = FakeSource("source-icloud", "iCloud")
    event_calendar = FakeCalendar(
        "calendar-1",
        "Calendar",
        source=source,
        allowed_entity_types=1,
    )
    store = FakeStore(event_calendars=[event_calendar], sources=[source])
    client = _client(store)

    with pytest.raises(EventKitClientError, match="not a Reminder List"):
        client.get_reminder_list(list_id="calendar-1")


def test_create_reminder_list_sets_source_title_and_color_before_saving() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    store = FakeStore(sources=[icloud])
    client = _client(store)

    record = client.create_reminder_list(
        source_id="source-icloud",
        title="Japanese plan",
        color="#3366CC",
    )

    assert record.list_id == "list-created"
    assert record.source_id == "source-icloud"
    assert record.title == "Japanese plan"
    assert record.color == "#3366CC"
    assert store.created_calendar is not None
    assert store.created_calendar.allowedEntityTypes() == 2
    assert store.created_calendar.source() is icloud
    assert store.saved_calendars == [(store.created_calendar, True)]


def test_update_reminder_list_changes_only_requested_properties_and_keeps_source() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    reminder_list = FakeCalendar(
        "list-1",
        "Old",
        source=icloud,
        allowed_entity_types=2,
    )
    store = FakeStore(reminder_calendars=[reminder_list], sources=[icloud])
    client = _client(store)

    record = client.update_reminder_list(
        list_id="list-1",
        title="New",
        color="#112233",
    )

    assert record.title == "New"
    assert record.color == "#112233"
    assert reminder_list.source() is icloud
    assert store.saved_calendars == [(reminder_list, True)]


def test_update_reminder_list_rejects_immutable_target_before_save() -> None:
    reminder_list = FakeCalendar(
        "list-1",
        "Read only",
        immutable=True,
        allowed_entity_types=2,
    )
    store = FakeStore(reminder_calendars=[reminder_list])
    client = _client(store)

    with pytest.raises(EventKitClientError, match="cannot be modified") as captured:
        client.update_reminder_list(
            list_id="list-1",
            title="New",
            color=None,
        )

    assert captured.value.external_state_changed is False
    assert store.saved_calendars == []


def test_successful_reminder_list_create_without_identifier_is_unknown() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    store = FakeStore(sources=[icloud])
    store.assign_calendar_identifier_on_save = False
    client = _client(store)

    with pytest.raises(EventKitClientError, match="write succeeded") as captured:
        client.create_reminder_list(
            source_id="source-icloud",
            title="Japanese plan",
            color=None,
        )

    assert store.saved_calendars
    assert captured.value.external_state_changed is None


def test_failed_calendar_save_preserves_unknown_external_write_outcome() -> None:
    icloud = FakeSource("source-icloud", "iCloud")
    store = FakeStore(sources=[icloud])
    store.save_calendar_result = (
        False,
        SimpleNamespace(localizedDescription=lambda: "calendar save failed"),
    )
    client = _client(store)

    with pytest.raises(EventKitClientError, match="calendar save failed") as captured:
        client.create_calendar(
            source_id="source-icloud",
            title="Japanese plan",
            color=None,
        )

    assert captured.value.external_state_changed is None


def test_denied_access_fails_before_calendar_data_is_read() -> None:
    store = FakeStore(event_calendars=[FakeCalendar("calendar-1", "Personal")])
    client = _client(store, authorization_status=2)

    with pytest.raises(EventKitClientError, match="access is denied") as captured:
        client.list_events(
            calendar_ids=["Personal"],
            start=datetime(2026, 7, 8, tzinfo=UTC),
            end=datetime(2026, 7, 9, tzinfo=UTC),
            include_notes=False,
            include_location=False,
        )

    assert captured.value.external_state_changed is False


def test_not_determined_access_requests_full_event_access_once() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    store = FakeStore(event_calendars=[calendar])
    client = _client(store, authorization_status=0)

    result = client.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, tzinfo=UTC),
        end=datetime(2026, 7, 9, tzinfo=UTC),
        include_notes=False,
        include_location=False,
    )

    assert result == []
    assert store.requested_access == ["event"]


def test_not_determined_access_uses_legacy_eventkit_selector_when_needed() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    wrapped = FakeStore(event_calendars=[calendar])

    class LegacyPermissionStore:
        def __init__(self) -> None:
            self.requested_entity_types: list[int] = []

        def requestAccessToEntityType_completion_(
            self,
            entity_type: int,
            callback: Any,
        ) -> None:
            self.requested_entity_types.append(entity_type)
            callback(True, None)

        def __getattr__(self, name: str) -> object:
            if name in {
                "requestFullAccessToEventsWithCompletion_",
                "requestFullAccessToRemindersWithCompletion_",
            }:
                raise AttributeError(name)
            return getattr(wrapped, name)

    store = LegacyPermissionStore()
    client = _client(store, authorization_status=0)

    records = client.list_events(
        calendar_ids=["Personal"],
        start=datetime(2026, 7, 8, tzinfo=UTC),
        end=datetime(2026, 7, 9, tzinfo=UTC),
        include_notes=False,
        include_location=False,
    )

    assert records == []
    assert store.requested_entity_types == [0]


def test_empty_calendar_allowlist_returns_without_native_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()
    monkeypatch.setattr(
        store,
        "calendarsForEntityType_",
        lambda entity_type: pytest.fail(f"unexpected EventKit query: {entity_type}"),
    )
    client = _client(store, authorization_status=0)

    records = client.list_events(
        calendar_ids=[],
        start=datetime(2026, 7, 8, tzinfo=UTC),
        end=datetime(2026, 7, 9, tzinfo=UTC),
        include_notes=False,
        include_location=False,
    )

    assert records == []
    assert store.requested_access == []


def test_ambiguous_calendar_title_is_rejected_instead_of_guessed() -> None:
    store = FakeStore(
        event_calendars=[
            FakeCalendar("calendar-1", "Personal"),
            FakeCalendar("calendar-2", "Personal"),
        ]
    )
    client = _client(store)

    with pytest.raises(EventKitClientError, match="ambiguous"):
        client.list_events(
            calendar_ids=["Personal"],
            start=datetime(2026, 7, 8, tzinfo=UTC),
            end=datetime(2026, 7, 9, tzinfo=UTC),
            include_notes=False,
            include_location=False,
        )


def test_native_calendar_identifier_wins_over_duplicate_titles() -> None:
    selected = FakeCalendar("calendar-1", "Personal")
    other = FakeCalendar("calendar-2", "Personal")
    selected_event = FakeEvent(
        event_id="event-1",
        calendar=selected,
        title="Selected",
        notes="private",
        location="Office",
    )
    store = FakeStore(
        event_calendars=[selected, other],
        events=[
            selected_event,
            FakeEvent(event_id="event-2", calendar=other, title="Other"),
        ],
    )
    client = _client(store)

    records = client.list_events(
        calendar_ids=["calendar-1"],
        start=datetime(2026, 7, 8, tzinfo=UTC),
        end=datetime(2026, 7, 9, tzinfo=UTC),
        include_notes=False,
        include_location=False,
    )

    assert [record.event_id for record in records] == ["event-1"]
    assert records[0].calendar_id == "calendar-1"
    assert records[0].notes is None
    assert records[0].location is None


def test_create_event_sets_native_fields_and_returns_persisted_identifier() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    store = FakeStore(event_calendars=[calendar])
    client = _client(store)
    start = datetime(2026, 7, 8, 10, 30, tzinfo=UTC)
    end = datetime(2026, 7, 8, 11, 45, tzinfo=UTC)

    record = client.create_event(
        calendar_id="Personal",
        title="Language lesson",
        start=start,
        end=end,
        is_all_day=False,
        notes="Chapter 1",
        location="Home",
        timezone="Asia/Shanghai",
    )

    assert record.event_id == "event-created"
    assert record.calendar_id == "Personal"
    assert record.start == start
    assert record.end == end
    assert store.created_event is not None
    assert store.created_event.event_calendar is calendar
    assert store.created_event.event_title == "Language lesson"
    assert store.created_event.event_notes == "Chapter 1"
    assert store.created_event.event_location == "Home"
    assert store.created_event.event_timezone is not None
    assert store.created_event.event_timezone.name == "Asia/Shanghai"
    assert store.saved_events == [(store.created_event, 0, True)]


def test_update_event_clear_notes_saves_only_the_selected_event() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    event = FakeEvent(
        event_id="event-1",
        calendar=calendar,
        title="Existing",
        notes="Remove this",
    )
    store = FakeStore(event_calendars=[calendar], events=[event])
    client = _client(store)

    record = client.update_event_notes(
        event_id="event-1",
        calendar_id="Personal",
        notes=None,
    )

    assert record.notes is None
    assert store.saved_events == [(event, 0, True)]


def test_update_event_with_matching_notes_is_a_successful_no_op() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    event = FakeEvent(
        event_id="event-1",
        calendar=calendar,
        title="Existing",
        notes="Already correct",
    )
    store = FakeStore(event_calendars=[calendar], events=[event])
    store.save_event_result = (False, None)
    client = _client(store)

    record = client.update_event_notes(
        event_id="event-1",
        calendar_id="Personal",
        notes="Already correct",
    )

    assert record.notes == "Already correct"
    assert store.saved_events == []


def test_event_in_a_different_calendar_is_rejected() -> None:
    selected = FakeCalendar("calendar-1", "Personal")
    other = FakeCalendar("calendar-2", "Work")
    store = FakeStore(
        event_calendars=[selected, other],
        events=[FakeEvent(event_id="event-1", calendar=other)],
    )
    client = _client(store)

    with pytest.raises(EventKitClientError, match="not found in Calendar"):
        client.get_event(event_id="event-1", calendar_id="Personal")


def test_failed_event_save_preserves_unknown_external_write_outcome() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    store = FakeStore(event_calendars=[calendar])
    store.save_event_result = (False, SimpleNamespace(localizedDescription=lambda: "save failed"))
    client = _client(store)

    with pytest.raises(EventKitClientError, match="save failed") as captured:
        client.create_event(
            calendar_id="Personal",
            title="Language lesson",
            start=datetime(2026, 7, 8, 10, tzinfo=UTC),
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            notes=None,
            location=None,
            timezone="UTC",
        )

    assert captured.value.external_state_changed is None


def test_successful_event_create_with_missing_identifier_is_unknown_write_outcome() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    store = FakeStore(event_calendars=[calendar])
    store.assign_event_identifier_on_save = False
    client = _client(store)

    with pytest.raises(EventKitClientError, match="write succeeded") as captured:
        client.create_event(
            calendar_id="Personal",
            title="Language lesson",
            start=datetime(2026, 7, 8, 10, tzinfo=UTC),
            end=datetime(2026, 7, 8, 11, tzinfo=UTC),
            is_all_day=False,
            notes=None,
            location=None,
            timezone="UTC",
        )

    assert store.saved_events
    assert captured.value.external_state_changed is None


def test_successful_event_update_with_malformed_result_is_unknown_write_outcome() -> None:
    calendar = FakeCalendar("calendar-1", "Personal")
    event = FakeEvent(
        event_id="event-1",
        calendar=calendar,
        title="",
        notes="Old",
    )
    store = FakeStore(event_calendars=[calendar], events=[event])
    client = _client(store)

    with pytest.raises(EventKitClientError, match="write succeeded") as captured:
        client.update_event_notes(
            event_id="event-1",
            calendar_id="Personal",
            notes="New",
        )

    assert store.saved_events
    assert captured.value.external_state_changed is None


def test_list_reminders_converts_due_components_and_applies_filters() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    incomplete = FakeReminder(
        reminder_id="reminder-1",
        calendar=reminder_list,
        title="Study",
        notes="private",
        due_components=_due_components(date(2026, 7, 9)),
    )
    completed = FakeReminder(
        reminder_id="reminder-2",
        calendar=reminder_list,
        title="Done",
        completed=True,
        completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
    )
    store = FakeStore(
        reminder_calendars=[reminder_list],
        reminders=[incomplete, completed],
    )
    client = _client(store, authorization_status=0)

    records = client.list_reminders(
        list_ids=["Personal"],
        start_due_at=datetime(2026, 7, 9, tzinfo=UTC),
        end_due_at=datetime(2026, 7, 9, 23, 59, tzinfo=UTC),
        start_completed_at=None,
        end_completed_at=None,
        include_completed=False,
        include_notes=False,
    )

    assert len(records) == 1
    assert records[0].reminder_id == "reminder-1"
    assert records[0].list_id == "Personal"
    assert records[0].due_date == date(2026, 7, 9)
    assert records[0].notes is None


def test_empty_reminder_allowlist_returns_without_native_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()
    monkeypatch.setattr(
        store,
        "calendarsForEntityType_",
        lambda entity_type: pytest.fail(f"unexpected EventKit query: {entity_type}"),
    )
    client = _client(store, authorization_status=0)

    records = client.list_reminders(
        list_ids=[],
        start_due_at=None,
        end_due_at=None,
        start_completed_at=None,
        end_completed_at=None,
        include_completed=False,
        include_notes=False,
    )

    assert records == []
    assert store.requested_access == []


def test_create_reminder_uses_gregorian_date_components_without_time() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    store = FakeStore(reminder_calendars=[reminder_list])
    client = _client(store)

    record = client.create_reminder(
        list_id="Personal",
        title="Book hotel",
        notes=None,
        due_date=date(2026, 8, 2),
        priority=None,
    )

    assert record.reminder_id == "reminder-created"
    assert record.due_date == date(2026, 8, 2)
    assert store.created_reminder is not None
    assert store.created_reminder.reminder_calendar is reminder_list
    assert store.created_reminder.reminder_priority == 0
    components = store.created_reminder.due_components
    assert components is not None
    assert components.values == {
        "calendar": components.values["calendar"],
        "year": 2026,
        "month": 8,
        "day": 2,
    }
    assert isinstance(components.values["calendar"], FakeNSCalendar)
    assert components.values["calendar"].identifier == "gregorian"
    assert store.saved_reminders == [(store.created_reminder, True)]


def test_complete_reminder_sets_completion_instant_and_saves() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    reminder = FakeReminder(
        reminder_id="reminder-1",
        calendar=reminder_list,
        title="Study",
    )
    store = FakeStore(reminder_calendars=[reminder_list], reminders=[reminder])
    client = _client(store)
    completed_at = datetime(2026, 7, 9, 12, tzinfo=UTC)

    record = client.complete_reminder(
        reminder_id="reminder-1",
        list_id="Personal",
        completion_date=completed_at,
    )

    assert record.is_completed is True
    assert record.completion_date == completed_at
    assert store.saved_reminders == [(reminder, True)]


def test_failed_reminder_save_preserves_unknown_external_write_outcome() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    store = FakeStore(reminder_calendars=[reminder_list])
    store.save_reminder_result = (
        False,
        SimpleNamespace(localizedDescription=lambda: "reminder save failed"),
    )
    client = _client(store)

    with pytest.raises(EventKitClientError, match="reminder save failed") as captured:
        client.create_reminder(
            list_id="Personal",
            title="Study",
            notes=None,
            due_date=None,
            priority=5,
        )

    assert captured.value.external_state_changed is None


def test_successful_reminder_create_with_missing_identifier_is_unknown_write_outcome() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    store = FakeStore(reminder_calendars=[reminder_list])
    store.assign_reminder_identifier_on_save = False
    client = _client(store)

    with pytest.raises(EventKitClientError, match="write succeeded") as captured:
        client.create_reminder(
            list_id="Personal",
            title="Study",
            notes=None,
            due_date=None,
            priority=5,
        )

    assert store.saved_reminders
    assert captured.value.external_state_changed is None


def test_successful_reminder_update_with_malformed_result_is_unknown_write_outcome() -> None:
    reminder_list = FakeCalendar("list-1", "Personal")
    reminder = FakeReminder(
        reminder_id="reminder-1",
        calendar=reminder_list,
        title="",
    )
    store = FakeStore(reminder_calendars=[reminder_list], reminders=[reminder])
    client = _client(store)

    with pytest.raises(EventKitClientError, match="write succeeded") as captured:
        client.complete_reminder(
            reminder_id="reminder-1",
            list_id="Personal",
            completion_date=datetime(2026, 7, 9, 12, tzinfo=UTC),
        )

    assert store.saved_reminders
    assert captured.value.external_state_changed is None
