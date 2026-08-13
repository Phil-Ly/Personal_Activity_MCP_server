"""Shared PyObjC/EventKit adapter for Calendar and Reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from threading import Event, RLock


class EventKitClientError(RuntimeError):
    """EventKit failure carrying whether an external write could have happened."""

    def __init__(
        self,
        message: str,
        *,
        external_state_changed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.external_state_changed = external_state_changed


@dataclass(frozen=True, slots=True)
class EventKitEventData:
    """Python representation of one EventKit event."""

    event_id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    is_all_day: bool
    start_date: date | None
    end_date: date | None
    location: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class EventKitReminderData:
    """Python representation of one EventKit reminder."""

    reminder_id: str
    list_id: str
    title: str
    notes: str | None
    due_date: datetime | date | None
    priority: int | None
    is_completed: bool
    completion_date: datetime | None


@dataclass(frozen=True, slots=True)
class EventKitCalendarData:
    """Python representation of one EventKit Calendar container."""

    calendar_id: str
    source_id: str
    source_title: str
    title: str
    color: str | None
    calendar_type: str
    allows_content_modifications: bool
    is_immutable: bool
    is_subscribed: bool


@dataclass(frozen=True, slots=True)
class EventKitReminderListData:
    """Python representation of one EventKit Reminder List container."""

    list_id: str
    source_id: str
    source_title: str
    title: str
    color: str | None
    calendar_type: str
    allows_content_modifications: bool
    is_immutable: bool
    is_subscribed: bool


class EventKitClient:
    """Use one native Event Store for Calendar and Reminders operations."""

    def __init__(
        self,
        *,
        store: object | None = None,
        eventkit_module: object | None = None,
        foundation_module: object | None = None,
        appkit_module: object | None = None,
        permission_timeout: float = 30,
        reminder_fetch_timeout: float = 30,
    ) -> None:
        if eventkit_module is None or foundation_module is None or appkit_module is None:
            loaded_eventkit, loaded_foundation, loaded_appkit = _load_eventkit_modules()
            eventkit_module = eventkit_module or loaded_eventkit
            foundation_module = foundation_module or loaded_foundation
            appkit_module = appkit_module or loaded_appkit
        self._eventkit = eventkit_module
        self._foundation = foundation_module
        self._appkit = appkit_module
        self._store = store or self._eventkit.EKEventStore.alloc().init()
        self._permission_timeout = permission_timeout
        self._reminder_fetch_timeout = reminder_fetch_timeout
        self._lock = RLock()

    def list_calendars(self, *, source_ids: list[str]) -> list[EventKitCalendarData]:
        if not source_ids:
            return []
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
            try:
                authorized = set(source_ids)
                calendars = self._store.calendarsForEntityType_(self._eventkit.EKEntityTypeEvent)
                return [
                    self._calendar_data(calendar)
                    for calendar in calendars
                    if _source_identifier(calendar.source()) in authorized
                ]
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"EventKit Calendar container read failed: {error}",
                    external_state_changed=False,
                ) from error

    def get_calendar(self, *, calendar_id: str) -> EventKitCalendarData:
        with self._lock:
            calendar = self._get_native_calendar(calendar_id)
            return self._calendar_data(calendar)

    def create_calendar(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> EventKitCalendarData:
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
            source = self._get_native_source(source_id)
            try:
                calendar = self._eventkit.EKCalendar.calendarForEntityType_eventStore_(
                    self._eventkit.EKEntityTypeEvent,
                    self._store,
                )
                calendar.setSource_(source)
                calendar.setTitle_(title)
                if color is not None:
                    calendar.setColor_(self._to_native_color(color))
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit Calendar container: {error}",
                    external_state_changed=False,
                ) from error
            self._save_calendar(calendar)
            return self._calendar_data_after_write(calendar)

    def update_calendar(
        self,
        *,
        calendar_id: str,
        title: str | None,
        color: str | None,
    ) -> EventKitCalendarData:
        with self._lock:
            calendar = self._get_native_calendar(calendar_id)
            current = self._calendar_data(calendar)
            if current.is_immutable or not current.allows_content_modifications:
                raise EventKitClientError(
                    f"EventKit Calendar cannot be modified: {calendar_id}",
                    external_state_changed=False,
                )
            if title is None and color is None:
                raise EventKitClientError(
                    "Calendar update requires title or color",
                    external_state_changed=False,
                )
            try:
                changed = False
                if title is not None and title != current.title:
                    calendar.setTitle_(title)
                    changed = True
                if color is not None and color != current.color:
                    calendar.setColor_(self._to_native_color(color))
                    changed = True
                if not changed:
                    return current
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit Calendar update: {error}",
                    external_state_changed=False,
                ) from error
            self._save_calendar(calendar)
            return self._calendar_data_after_write(calendar)

    def list_reminder_lists(
        self,
        *,
        source_ids: list[str],
    ) -> list[EventKitReminderListData]:
        if not source_ids:
            return []
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
            try:
                authorized = set(source_ids)
                reminder_lists = self._store.calendarsForEntityType_(
                    self._eventkit.EKEntityTypeReminder
                )
                return [
                    self._reminder_list_data(reminder_list)
                    for reminder_list in reminder_lists
                    if _source_identifier(reminder_list.source()) in authorized
                ]
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"EventKit Reminder List read failed: {error}",
                    external_state_changed=False,
                ) from error

    def get_reminder_list(self, *, list_id: str) -> EventKitReminderListData:
        with self._lock:
            reminder_list = self._get_native_reminder_list(list_id)
            return self._reminder_list_data(reminder_list)

    def create_reminder_list(
        self,
        *,
        source_id: str,
        title: str,
        color: str | None,
    ) -> EventKitReminderListData:
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
            source = self._get_native_source(source_id)
            try:
                reminder_list = self._eventkit.EKCalendar.calendarForEntityType_eventStore_(
                    self._eventkit.EKEntityTypeReminder,
                    self._store,
                )
                reminder_list.setSource_(source)
                reminder_list.setTitle_(title)
                if color is not None:
                    reminder_list.setColor_(self._to_native_color(color))
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit Reminder List: {error}",
                    external_state_changed=False,
                ) from error
            self._save_calendar(reminder_list)
            return self._reminder_list_data_after_write(reminder_list)

    def update_reminder_list(
        self,
        *,
        list_id: str,
        title: str | None,
        color: str | None,
    ) -> EventKitReminderListData:
        with self._lock:
            reminder_list = self._get_native_reminder_list(list_id)
            current = self._reminder_list_data(reminder_list)
            if current.is_immutable or not current.allows_content_modifications:
                raise EventKitClientError(
                    f"EventKit Reminder List cannot be modified: {list_id}",
                    external_state_changed=False,
                )
            if title is None and color is None:
                raise EventKitClientError(
                    "Reminder List update requires title or color",
                    external_state_changed=False,
                )
            try:
                changed = False
                if title is not None and title != current.title:
                    reminder_list.setTitle_(title)
                    changed = True
                if color is not None and color != current.color:
                    reminder_list.setColor_(self._to_native_color(color))
                    changed = True
                if not changed:
                    return current
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit Reminder List update: {error}",
                    external_state_changed=False,
                ) from error
            self._save_calendar(reminder_list)
            return self._reminder_list_data_after_write(reminder_list)

    def list_events(
        self,
        *,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        include_notes: bool,
        include_location: bool,
    ) -> list[EventKitEventData]:
        if not calendar_ids:
            return []
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
            try:
                calendars, references = self._resolve_calendars(
                    calendar_ids,
                    self._eventkit.EKEntityTypeEvent,
                    container_name="Calendar",
                )
                predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
                    self._to_native_date(start),
                    self._to_native_date(end),
                    calendars,
                )
                events = self._store.eventsMatchingPredicate_(predicate)
                return [
                    self._event_data(
                        event,
                        calendar_id=self._reference_for_item(
                            event,
                            references,
                            container_name="Calendar",
                        ),
                        include_notes=include_notes,
                        include_location=include_location,
                    )
                    for event in events
                ]
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"EventKit Calendar read failed: {error}",
                    external_state_changed=False,
                ) from error

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
    ) -> EventKitEventData:
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
            calendar = self._resolve_one_calendar(
                calendar_id,
                self._eventkit.EKEntityTypeEvent,
                container_name="Calendar",
            )
            try:
                native_timezone = self._foundation.NSTimeZone.timeZoneWithName_(timezone)
                if native_timezone is None:
                    raise EventKitClientError(
                        f"EventKit does not recognize timezone: {timezone}",
                        external_state_changed=False,
                    )
                event = self._eventkit.EKEvent.eventWithEventStore_(self._store)
                event.setCalendar_(calendar)
                event.setTitle_(title)
                event.setStartDate_(self._to_native_date(start))
                event.setEndDate_(self._to_native_date(end))
                event.setTimeZone_(native_timezone)
                event.setAllDay_(is_all_day)
                event.setNotes_(notes)
                event.setLocation_(location)
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit event: {error}",
                    external_state_changed=False,
                ) from error
            self._save_event(event)
            return self._event_data_after_write(
                event,
                calendar_id=calendar_id,
                include_notes=True,
                include_location=True,
            )

    def update_event_notes(
        self,
        *,
        event_id: str,
        calendar_id: str,
        notes: str | None,
    ) -> EventKitEventData:
        with self._lock:
            event = self._get_native_event(event_id=event_id, calendar_id=calendar_id)
            try:
                if _optional_native_string(event.notes()) == _optional_native_string(notes):
                    return self._event_data(
                        event,
                        calendar_id=calendar_id,
                        include_notes=True,
                        include_location=True,
                    )
                event.setNotes_(notes)
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit event update: {error}",
                    external_state_changed=False,
                ) from error
            self._save_event(event)
            return self._event_data_after_write(
                event,
                calendar_id=calendar_id,
                include_notes=True,
                include_location=True,
            )

    def get_event(
        self,
        *,
        event_id: str,
        calendar_id: str,
    ) -> EventKitEventData:
        with self._lock:
            event = self._get_native_event(event_id=event_id, calendar_id=calendar_id)
            return self._event_data(
                event,
                calendar_id=calendar_id,
                include_notes=True,
                include_location=True,
            )

    def list_reminders(
        self,
        *,
        list_ids: list[str],
        start_due_at: datetime | None,
        end_due_at: datetime | None,
        start_completed_at: datetime | None,
        end_completed_at: datetime | None,
        include_completed: bool,
        include_notes: bool,
    ) -> list[EventKitReminderData]:
        if not list_ids:
            return []
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
            try:
                calendars, references = self._resolve_calendars(
                    list_ids,
                    self._eventkit.EKEntityTypeReminder,
                    container_name="Reminder List",
                )
                predicate = self._store.predicateForRemindersInCalendars_(calendars)
                reminders = self._fetch_reminders(predicate)
                records = [
                    self._reminder_data(
                        reminder,
                        list_id=self._reference_for_item(
                            reminder,
                            references,
                            container_name="Reminder List",
                        ),
                        include_notes=include_notes,
                    )
                    for reminder in reminders
                ]
                return [
                    record
                    for record in records
                    if _reminder_matches_query(
                        record,
                        start_due_at=start_due_at,
                        end_due_at=end_due_at,
                        start_completed_at=start_completed_at,
                        end_completed_at=end_completed_at,
                        include_completed=include_completed,
                    )
                ]
            except EventKitClientError:
                raise
            except Exception as error:
                raise EventKitClientError(
                    f"EventKit Reminders read failed: {error}",
                    external_state_changed=False,
                ) from error

    def create_reminder(
        self,
        *,
        list_id: str,
        title: str,
        notes: str | None,
        due_date: date | None,
        priority: int | None,
    ) -> EventKitReminderData:
        with self._lock:
            self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
            reminder_list = self._resolve_one_calendar(
                list_id,
                self._eventkit.EKEntityTypeReminder,
                container_name="Reminder List",
            )
            try:
                reminder = self._eventkit.EKReminder.reminderWithEventStore_(self._store)
                reminder.setCalendar_(reminder_list)
                reminder.setTitle_(title)
                reminder.setNotes_(notes)
                reminder.setDueDateComponents_(
                    self._date_to_components(due_date) if due_date is not None else None
                )
                reminder.setPriority_(priority or 0)
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit reminder: {error}",
                    external_state_changed=False,
                ) from error
            self._save_reminder(reminder)
            return self._reminder_data_after_write(
                reminder,
                list_id=list_id,
                include_notes=True,
            )

    def complete_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
        completion_date: datetime,
    ) -> EventKitReminderData:
        with self._lock:
            reminder = self._get_native_reminder(reminder_id=reminder_id, list_id=list_id)
            try:
                reminder.setCompleted_(True)
                reminder.setCompletionDate_(self._to_native_date(completion_date))
            except Exception as error:
                raise EventKitClientError(
                    f"Unable to prepare EventKit reminder update: {error}",
                    external_state_changed=False,
                ) from error
            self._save_reminder(reminder)
            return self._reminder_data_after_write(
                reminder,
                list_id=list_id,
                include_notes=True,
            )

    def get_reminder(
        self,
        *,
        reminder_id: str,
        list_id: str,
    ) -> EventKitReminderData:
        with self._lock:
            reminder = self._get_native_reminder(reminder_id=reminder_id, list_id=list_id)
            return self._reminder_data(reminder, list_id=list_id, include_notes=True)

    def _ensure_full_access(self, entity_type: int) -> None:
        try:
            status = self._eventkit.EKEventStore.authorizationStatusForEntityType_(entity_type)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit authorization status: {error}",
                external_state_changed=False,
            ) from error
        authorized_statuses = {
            self._eventkit.EKAuthorizationStatusFullAccess,
            self._eventkit.EKAuthorizationStatusAuthorized,
        }
        if status in authorized_statuses:
            return
        if status == self._eventkit.EKAuthorizationStatusNotDetermined:
            self._request_full_access(entity_type)
            return
        if status == self._eventkit.EKAuthorizationStatusDenied:
            reason = "denied"
        elif status == self._eventkit.EKAuthorizationStatusRestricted:
            reason = "restricted"
        else:
            reason = "not full access"
        raise EventKitClientError(
            f"EventKit access is {reason}",
            external_state_changed=False,
        )

    def _request_full_access(self, entity_type: int) -> None:
        completed = Event()
        result: dict[str, object] = {}

        def callback(granted: bool, error: object | None) -> None:
            result["granted"] = bool(granted)
            result["error"] = error
            completed.set()

        try:
            if entity_type == self._eventkit.EKEntityTypeEvent:
                request = getattr(
                    self._store,
                    "requestFullAccessToEventsWithCompletion_",
                    None,
                )
            else:
                request = getattr(
                    self._store,
                    "requestFullAccessToRemindersWithCompletion_",
                    None,
                )
            if request is not None:
                request(callback)
            else:
                legacy_request = getattr(
                    self._store,
                    "requestAccessToEntityType_completion_",
                    None,
                )
                if legacy_request is None:
                    raise AttributeError("EventKit full-access request selector is unavailable")
                legacy_request(entity_type, callback)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to request EventKit full access: {error}",
                external_state_changed=False,
            ) from error
        if not completed.wait(self._permission_timeout):
            raise EventKitClientError(
                "Timed out waiting for EventKit full access",
                external_state_changed=False,
            )
        if result.get("granted") is not True:
            description = _native_error_description(result.get("error"))
            raise EventKitClientError(
                f"EventKit full access was not granted: {description}",
                external_state_changed=False,
            )

    def _resolve_one_calendar(
        self,
        reference: str,
        entity_type: int,
        *,
        container_name: str,
    ) -> object:
        calendars, _ = self._resolve_calendars(
            [reference],
            entity_type,
            container_name=container_name,
        )
        return calendars[0]

    def _resolve_calendars(
        self,
        references: list[str],
        entity_type: int,
        *,
        container_name: str,
    ) -> tuple[list[object], dict[str, str]]:
        try:
            available = list(self._store.calendarsForEntityType_(entity_type))
        except Exception as error:
            raise EventKitClientError(
                f"Unable to list EventKit {container_name} containers: {error}",
                external_state_changed=False,
            ) from error
        by_identifier = {_calendar_identifier(calendar): calendar for calendar in available}
        selected: list[object] = []
        reference_by_identifier: dict[str, str] = {}
        for reference in references:
            calendar = by_identifier.get(reference)
            if calendar is None:
                raise EventKitClientError(
                    f"EventKit {container_name} not found: {reference}",
                    external_state_changed=False,
                )
            identifier = _calendar_identifier(calendar)
            existing_reference = reference_by_identifier.get(identifier)
            if existing_reference is not None and existing_reference != reference:
                raise EventKitClientError(
                    f"Multiple configured references identify the same {container_name}: "
                    f"{existing_reference}, {reference}",
                    external_state_changed=False,
                )
            if existing_reference is None:
                selected.append(calendar)
                reference_by_identifier[identifier] = reference
        return selected, reference_by_identifier

    def _reference_for_item(
        self,
        item: object,
        references: dict[str, str],
        *,
        container_name: str,
    ) -> str:
        calendar = item.calendar()
        if calendar is None:
            raise EventKitClientError(
                f"EventKit item has no {container_name}",
                external_state_changed=False,
            )
        identifier = _calendar_identifier(calendar)
        reference = references.get(identifier)
        if reference is None:
            raise EventKitClientError(
                f"EventKit item belongs to an unexpected {container_name}",
                external_state_changed=False,
            )
        return reference

    def _get_native_event(self, *, event_id: str, calendar_id: str) -> object:
        self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
        calendar = self._resolve_one_calendar(
            calendar_id,
            self._eventkit.EKEntityTypeEvent,
            container_name="Calendar",
        )
        try:
            event = self._store.eventWithIdentifier_(event_id)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit event: {error}",
                external_state_changed=False,
            ) from error
        if event is None or not _item_belongs_to_calendar(event, calendar):
            raise EventKitClientError(
                f"EventKit event not found in Calendar: {event_id}",
                external_state_changed=False,
            )
        return event

    def _get_native_source(self, source_id: str) -> object:
        try:
            source = self._store.sourceWithIdentifier_(source_id)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit Source: {error}",
                external_state_changed=False,
            ) from error
        if source is None:
            raise EventKitClientError(
                f"EventKit Source not found: {source_id}",
                external_state_changed=False,
            )
        return source

    def _get_native_calendar(self, calendar_id: str) -> object:
        self._ensure_full_access(self._eventkit.EKEntityTypeEvent)
        try:
            calendar = self._store.calendarWithIdentifier_(calendar_id)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit Calendar container: {error}",
                external_state_changed=False,
            ) from error
        if calendar is None:
            raise EventKitClientError(
                f"EventKit Calendar not found: {calendar_id}",
                external_state_changed=False,
            )
        if not int(calendar.allowedEntityTypes()) & int(self._eventkit.EKEntityMaskEvent):
            raise EventKitClientError(
                f"EventKit object is not an Event Calendar: {calendar_id}",
                external_state_changed=False,
            )
        return calendar

    def _get_native_reminder_list(self, list_id: str) -> object:
        self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
        try:
            reminder_list = self._store.calendarWithIdentifier_(list_id)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit Reminder List: {error}",
                external_state_changed=False,
            ) from error
        if reminder_list is None:
            raise EventKitClientError(
                f"EventKit Reminder List not found: {list_id}",
                external_state_changed=False,
            )
        if not int(reminder_list.allowedEntityTypes()) & int(self._eventkit.EKEntityMaskReminder):
            raise EventKitClientError(
                f"EventKit object is not a Reminder List: {list_id}",
                external_state_changed=False,
            )
        return reminder_list

    def _get_native_reminder(self, *, reminder_id: str, list_id: str) -> object:
        self._ensure_full_access(self._eventkit.EKEntityTypeReminder)
        reminder_list = self._resolve_one_calendar(
            list_id,
            self._eventkit.EKEntityTypeReminder,
            container_name="Reminder List",
        )
        try:
            reminder = self._store.calendarItemWithIdentifier_(reminder_id)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to read EventKit reminder: {error}",
                external_state_changed=False,
            ) from error
        if (
            reminder is None
            or not hasattr(reminder, "dueDateComponents")
            or not _item_belongs_to_calendar(reminder, reminder_list)
        ):
            raise EventKitClientError(
                f"EventKit reminder not found in Reminder List: {reminder_id}",
                external_state_changed=False,
            )
        return reminder

    def _save_event(self, event: object) -> None:
        try:
            saved, error = self._store.saveEvent_span_commit_error_(
                event,
                self._eventkit.EKSpanThisEvent,
                True,
                None,
            )
        except Exception as error:
            raise EventKitClientError(
                f"EventKit event save failed: {error}",
            ) from error
        if not saved:
            raise EventKitClientError(
                f"EventKit event save failed: {_native_error_description(error)}",
            )

    def _save_calendar(self, calendar: object) -> None:
        try:
            saved, error = self._store.saveCalendar_commit_error_(
                calendar,
                True,
                None,
            )
        except Exception as error:
            raise EventKitClientError(
                f"EventKit Calendar save failed: {error}",
            ) from error
        if not saved:
            raise EventKitClientError(
                f"EventKit Calendar save failed: {_native_error_description(error)}",
            )

    def _save_reminder(self, reminder: object) -> None:
        try:
            saved, error = self._store.saveReminder_commit_error_(
                reminder,
                True,
                None,
            )
        except Exception as error:
            raise EventKitClientError(
                f"EventKit reminder save failed: {error}",
            ) from error
        if not saved:
            raise EventKitClientError(
                f"EventKit reminder save failed: {_native_error_description(error)}",
            )

    def _fetch_reminders(self, predicate: object) -> list[object]:
        completed = Event()
        result: dict[str, object] = {}

        def callback(reminders: list[object] | None) -> None:
            result["reminders"] = reminders
            completed.set()

        try:
            self._store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        except Exception as error:
            raise EventKitClientError(
                f"Unable to fetch EventKit reminders: {error}",
                external_state_changed=False,
            ) from error
        if not completed.wait(self._reminder_fetch_timeout):
            raise EventKitClientError(
                "Timed out waiting for EventKit reminders",
                external_state_changed=False,
            )
        reminders = result.get("reminders")
        if reminders is None:
            raise EventKitClientError(
                "EventKit reminder query returned no result",
                external_state_changed=False,
            )
        return list(reminders)

    def _event_data(
        self,
        event: object,
        *,
        calendar_id: str,
        include_notes: bool,
        include_location: bool,
    ) -> EventKitEventData:
        event_id = _required_native_string(event.eventIdentifier(), "event identifier")
        start = self._from_native_date(event.startDate())
        end = self._from_native_date(event.endDate())
        is_all_day = bool(event.isAllDay())
        return EventKitEventData(
            event_id=event_id,
            calendar_id=calendar_id,
            title=_required_native_string(event.title(), "event title"),
            start=start,
            end=end,
            is_all_day=is_all_day,
            start_date=_native_local_date(event.startDate()) if is_all_day else None,
            end_date=_native_all_day_end_date(event.endDate()) if is_all_day else None,
            location=_optional_native_string(event.location()) if include_location else None,
            notes=_optional_native_string(event.notes()) if include_notes else None,
        )

    def _calendar_data(self, calendar: object) -> EventKitCalendarData:
        source = calendar.source()
        if source is None:
            raise EventKitClientError(
                "EventKit Calendar has no Source",
                external_state_changed=False,
            )
        return EventKitCalendarData(
            calendar_id=_calendar_identifier(calendar),
            source_id=_source_identifier(source),
            source_title=_required_native_string(source.title(), "source title"),
            title=_calendar_title(calendar),
            color=self._from_native_color(calendar.color()),
            calendar_type=_calendar_type_name(
                int(calendar.type()),
                self._eventkit,
            ),
            allows_content_modifications=bool(calendar.allowsContentModifications()),
            is_immutable=bool(calendar.isImmutable()),
            is_subscribed=bool(calendar.isSubscribed()),
        )

    def _calendar_data_after_write(self, calendar: object) -> EventKitCalendarData:
        try:
            return self._calendar_data(calendar)
        except Exception as error:
            raise EventKitClientError(
                f"EventKit Calendar write succeeded but returned invalid data: {error}",
            ) from error

    def _reminder_list_data(self, reminder_list: object) -> EventKitReminderListData:
        source = reminder_list.source()
        if source is None:
            raise EventKitClientError(
                "EventKit Reminder List has no Source",
                external_state_changed=False,
            )
        return EventKitReminderListData(
            list_id=_calendar_identifier(reminder_list),
            source_id=_source_identifier(source),
            source_title=_required_native_string(source.title(), "source title"),
            title=_calendar_title(reminder_list),
            color=self._from_native_color(reminder_list.color()),
            calendar_type=_calendar_type_name(
                int(reminder_list.type()),
                self._eventkit,
            ),
            allows_content_modifications=bool(reminder_list.allowsContentModifications()),
            is_immutable=bool(reminder_list.isImmutable()),
            is_subscribed=bool(reminder_list.isSubscribed()),
        )

    def _reminder_list_data_after_write(
        self,
        reminder_list: object,
    ) -> EventKitReminderListData:
        try:
            return self._reminder_list_data(reminder_list)
        except Exception as error:
            raise EventKitClientError(
                f"EventKit Reminder List write succeeded but returned invalid data: {error}",
            ) from error

    def _event_data_after_write(
        self,
        event: object,
        *,
        calendar_id: str,
        include_notes: bool,
        include_location: bool,
    ) -> EventKitEventData:
        try:
            return self._event_data(
                event,
                calendar_id=calendar_id,
                include_notes=include_notes,
                include_location=include_location,
            )
        except Exception as error:
            raise EventKitClientError(
                f"EventKit event write succeeded but returned invalid data: {error}",
            ) from error

    def _reminder_data(
        self,
        reminder: object,
        *,
        list_id: str,
        include_notes: bool,
    ) -> EventKitReminderData:
        completion_date = reminder.completionDate()
        return EventKitReminderData(
            reminder_id=_required_native_string(
                reminder.calendarItemIdentifier(),
                "reminder identifier",
            ),
            list_id=list_id,
            title=_required_native_string(reminder.title(), "reminder title"),
            notes=_optional_native_string(reminder.notes()) if include_notes else None,
            due_date=self._components_to_date_value(reminder.dueDateComponents()),
            priority=int(reminder.priority()),
            is_completed=bool(reminder.isCompleted()),
            completion_date=(
                self._from_native_date(completion_date) if completion_date is not None else None
            ),
        )

    def _reminder_data_after_write(
        self,
        reminder: object,
        *,
        list_id: str,
        include_notes: bool,
    ) -> EventKitReminderData:
        try:
            return self._reminder_data(
                reminder,
                list_id=list_id,
                include_notes=include_notes,
            )
        except Exception as error:
            raise EventKitClientError(
                f"EventKit reminder write succeeded but returned invalid data: {error}",
            ) from error

    def _to_native_date(self, value: datetime) -> object:
        if value.tzinfo is None or value.utcoffset() is None:
            raise EventKitClientError(
                "EventKit datetime must include a timezone",
                external_state_changed=False,
            )
        return self._foundation.NSDate.dateWithTimeIntervalSince1970_(value.timestamp())

    def _from_native_date(self, value: object) -> datetime:
        if value is None:
            raise EventKitClientError(
                "EventKit returned a missing date",
                external_state_changed=False,
            )
        return datetime.fromtimestamp(float(value.timeIntervalSince1970()), tz=UTC)

    def _date_to_components(self, value: date) -> object:
        components = self._foundation.NSDateComponents.alloc().init()
        calendar = self._foundation.NSCalendar.calendarWithIdentifier_(
            self._foundation.NSCalendarIdentifierGregorian
        )
        components.setCalendar_(calendar)
        components.setYear_(value.year)
        components.setMonth_(value.month)
        components.setDay_(value.day)
        return components

    def _components_to_date_value(self, components: object | None) -> datetime | date | None:
        if components is None:
            return None
        undefined = self._foundation.NSDateComponentUndefined
        year = int(components.year())
        month = int(components.month())
        day = int(components.day())
        if undefined in {year, month, day}:
            raise EventKitClientError(
                "EventKit reminder due date is missing date components",
                external_state_changed=False,
            )
        hour = int(components.hour())
        minute = int(components.minute())
        second = int(components.second())
        if hour == minute == second == undefined:
            return date(year, month, day)
        calendar = components.calendar() or self._foundation.NSCalendar.calendarWithIdentifier_(
            self._foundation.NSCalendarIdentifierGregorian
        )
        native_date = calendar.dateFromComponents_(components)
        return self._from_native_date(native_date)

    def _to_native_color(self, color: str) -> object:
        red, green, blue = _parse_hex_color(color)
        return self._appkit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            red / 255,
            green / 255,
            blue / 255,
            1.0,
        )

    def _from_native_color(self, color: object | None) -> str | None:
        if color is None:
            return None
        converted = color.colorUsingColorSpace_(self._appkit.NSColorSpace.sRGBColorSpace())
        if converted is None:
            raise EventKitClientError(
                "EventKit Calendar color cannot be converted to sRGB",
                external_state_changed=False,
            )
        values = (
            round(float(converted.redComponent()) * 255),
            round(float(converted.greenComponent()) * 255),
            round(float(converted.blueComponent()) * 255),
        )
        if any(value < 0 or value > 255 for value in values):
            raise EventKitClientError(
                "EventKit Calendar color is outside the sRGB range",
                external_state_changed=False,
            )
        return "#" + "".join(f"{value:02X}" for value in values)


def _load_eventkit_modules() -> tuple[object, object, object]:
    try:
        import AppKit
        import EventKit
        import Foundation
    except ImportError as error:
        raise EventKitClientError(
            "PyObjC EventKit is required on macOS",
            external_state_changed=False,
        ) from error
    return EventKit, Foundation, AppKit


def _calendar_identifier(calendar: object) -> str:
    return _required_native_string(calendar.calendarIdentifier(), "calendar identifier")


def _calendar_title(calendar: object) -> str:
    return _required_native_string(calendar.title(), "calendar title")


def _source_identifier(source: object) -> str:
    return _required_native_string(source.sourceIdentifier(), "source identifier")


def _calendar_type_name(value: int, eventkit: object) -> str:
    names = {
        int(eventkit.EKCalendarTypeLocal): "local",
        int(eventkit.EKCalendarTypeCalDAV): "caldav",
        int(eventkit.EKCalendarTypeExchange): "exchange",
        int(eventkit.EKCalendarTypeSubscription): "subscription",
        int(eventkit.EKCalendarTypeBirthday): "birthday",
    }
    return names.get(value, "unknown")


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise EventKitClientError(
            "Calendar color must use #RRGGBB",
            external_state_changed=False,
        )
    try:
        return (
            int(value[1:3], 16),
            int(value[3:5], 16),
            int(value[5:7], 16),
        )
    except ValueError as error:
        raise EventKitClientError(
            "Calendar color must use #RRGGBB",
            external_state_changed=False,
        ) from error


def _item_belongs_to_calendar(item: object, calendar: object) -> bool:
    item_calendar = item.calendar()
    return item_calendar is not None and _calendar_identifier(
        item_calendar
    ) == _calendar_identifier(calendar)


def _required_native_string(value: object, field_name: str) -> str:
    if value is None:
        raise EventKitClientError(
            f"EventKit returned a missing {field_name}",
            external_state_changed=False,
        )
    text = str(value)
    if not text:
        raise EventKitClientError(
            f"EventKit returned an empty {field_name}",
            external_state_changed=False,
        )
    return text


def _optional_native_string(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value) or None


def _native_local_date(value: object) -> date:
    return datetime.fromtimestamp(float(value.timeIntervalSince1970())).date()


def _native_all_day_end_date(value: object) -> date:
    timestamp = float(value.timeIntervalSince1970())
    return datetime.fromtimestamp(timestamp + 1).date()


def _native_error_description(error: object | None) -> str:
    if error is None:
        return "unknown EventKit error"
    description = getattr(error, "localizedDescription", None)
    if callable(description):
        return str(description())
    return str(error)


def _reminder_matches_query(
    reminder: EventKitReminderData,
    *,
    start_due_at: datetime | None,
    end_due_at: datetime | None,
    start_completed_at: datetime | None,
    end_completed_at: datetime | None,
    include_completed: bool,
) -> bool:
    if reminder.is_completed and not include_completed:
        return False
    if start_due_at is not None or end_due_at is not None:
        if reminder.due_date is None:
            return False
        due_at = _due_value_for_comparison(
            reminder.due_date,
            start_due_at or end_due_at,
        )
        if start_due_at is not None and due_at < start_due_at:
            return False
        if end_due_at is not None and due_at > end_due_at:
            return False
    if start_completed_at is not None or end_completed_at is not None:
        if reminder.completion_date is None:
            return False
        if start_completed_at is not None and reminder.completion_date < start_completed_at:
            return False
        if end_completed_at is not None and reminder.completion_date > end_completed_at:
            return False
    return True


def _due_value_for_comparison(
    value: datetime | date,
    reference: datetime | None,
) -> datetime:
    if isinstance(value, datetime):
        return value
    timezone = reference.tzinfo if reference is not None else UTC
    return datetime.combine(value, time.min, tzinfo=timezone)
