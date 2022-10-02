"""Calendar platform for a Local Calendar."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from ical.calendar import Calendar
from ical.calendar_stream import IcsCalendarStream
from ical.event import Event
from ical.store import EventStore
from ical.types import Range, Recur
import voluptuous as vol

from homeassistant.components.calendar import (
    ENTITY_ID_FORMAT,
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_CALENDAR_NAME, DOMAIN
from .store import LocalCalendarStore

_LOGGER = logging.getLogger(__name__)


EVENT_DESCRIPTION = "description"
EVENT_END = "dtend"
EVENT_START = "dtstart"
EVENT_SUMMARY = "summary"
EVENT_RECURRENCE_ID = "recurrence_id"
EVENT_RECURRENCE_RANGE = "recurrence_range"
EVENT_RRULE = "rrule"
EVENT_UID = "uid"

SERVICE_CREATE_EVENT = "create_event"
CREATE_EVENT_SCHEMA = vol.All(
    cv.make_entity_service_schema(
        {
            vol.Required(EVENT_SUMMARY): cv.string,
            vol.Required(EVENT_START): vol.Any(cv.date, cv.datetime),
            vol.Required(EVENT_END): vol.Any(cv.date, cv.datetime),
            vol.Optional(EVENT_DESCRIPTION, default=""): cv.string,
            vol.Optional(EVENT_RRULE, default=""): cv.string,
        }
    ),
)

SERVICE_DELETE_EVENT = "delete_event"
DELETE_EVENT_SCHEMA = vol.All(
    cv.make_entity_service_schema(
        {
            vol.Required(EVENT_UID): cv.string,
            vol.Optional(EVENT_RECURRENCE_ID): cv.string,
            vol.Optional(EVENT_RECURRENCE_RANGE): cv.string,
        }
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the local calendar platform."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    ics = await store.async_load()
    calendar = IcsCalendarStream.calendar_from_ics(ics)

    name = config_entry.data[CONF_CALENDAR_NAME]
    entity_id = generate_entity_id(ENTITY_ID_FORMAT, name, hass=hass)
    entity = LocalCalendarEntity(store, calendar, name, entity_id)
    async_add_entities([entity], True)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CREATE_EVENT,
        CREATE_EVENT_SCHEMA,
        "async_create_event",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_EVENT,
        DELETE_EVENT_SCHEMA,
        "async_delete_event",
    )


class LocalCalendarEntity(CalendarEntity):
    """A calendar entity backed by a local iCalendar file."""

    _attr_has_entity_name = True

    def __init__(
        self, store: LocalCalendarStore, calendar: Calendar, name: str, entity_id: str
    ) -> None:
        """Initialize LocalCalendarEntity."""
        self._store = store
        self._calendar = calendar
        self._event: CalendarEvent | None = None
        self._attr_name = name.capitalize()
        self.entity_id = entity_id
        self._attr_unique_id = calendar.prodid

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        return self._event

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Get all events in a specific time frame."""
        events = self._calendar.timeline.overlapping(start_date, end_date)
        return [_get_calendar_event(event) for event in events]

    async def async_update(self) -> None:
        """Update entity state with the next upcoming event."""
        events = self._calendar.timeline.active_after(dt_util.now())
        if event := next(events, None):
            self._event = _get_calendar_event(event)
        else:
            self._event = None

    async def _async_store(self) -> None:
        """Persist the calendar to disk."""
        content = IcsCalendarStream.calendar_to_ics(self._calendar)
        await self._store.async_store(content)

    async def async_create_event(self, **kwargs: Any) -> None:
        """Add a new event to calendar."""
        event = Event.parse_obj(
            {
                EVENT_SUMMARY: kwargs[EVENT_SUMMARY],
                EVENT_START: kwargs[EVENT_START],
                EVENT_END: kwargs[EVENT_END],
                EVENT_DESCRIPTION: kwargs.get(EVENT_DESCRIPTION),
            }
        )
        if rrule := kwargs.get(EVENT_RRULE):
            event.rrule = Recur.from_rrule(rrule)

        EventStore(self._calendar).add(event)
        await self._async_store()

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Cancel an event on the calendar."""
        range_value: Range = Range.NONE
        if recurrence_range == Range.THIS_AND_FUTURE:
            range_value = Range.THIS_AND_FUTURE
        EventStore(self._calendar).delete(
            uid,
            recurrence_id=recurrence_id,
            recurrence_range=range_value,
        )
        await self._async_store()


def _get_calendar_event(event: Event) -> CalendarEvent:
    """Return a CalendarEvent from an API event."""
    return CalendarEvent(
        summary=event.summary,
        start=event.start,
        end=event.end,
        description=event.description,
        location=event.location,
    )
