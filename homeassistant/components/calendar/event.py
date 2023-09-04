"""Event platform for matching start/end of a Calendar Event."""

from __future__ import annotations

from collections.abc import Callable
import datetime
import logging

from homeassistant.components.event import EventEntity

# from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CalendarEntity, CalendarEvent
from .const import (
    CALENDAR_EVENT,
    CONF_SEARCH,
    DOMAIN,
    TRIGGER_EVENT_END,
    TRIGGER_EVENT_START,
)
from .trigger import (
    CalendarEventListener,
    QueuedCalendarEvent,
    event_fetcher,
    queued_event_fetcher,
)

_LOGGER = logging.getLogger(__name__)

ICON = "mdi:calendar-text"
ZERO = datetime.timedelta(seconds=0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Calendar event config entry."""
    registry = er.async_get(hass)
    # Validate + resolve entity registry id to entity_id
    calendar_entity_id = er.async_validate_entity_id(
        registry, config_entry.options[CONF_ENTITY_ID]
    )

    def is_match(calendar_event: CalendarEvent) -> bool:
        if CONF_SEARCH not in config_entry.options:
            return True
        search = config_entry.options[CONF_SEARCH].lower()
        if calendar_event.summary and search in calendar_event.summary.lower():
            return True
        if calendar_event.description and search in calendar_event.description.lower():
            return True
        return False

    calendar_event_sensor = CalendarEventEntity(
        name=config_entry.options[CONF_NAME],
        calendar_entity_id=calendar_entity_id,
        unique_id=config_entry.entry_id,
        event_predicate=is_match,
    )

    async_add_entities([calendar_event_sensor])


class CalendarEventEntity(EventEntity):
    """A sensor that matches a specific calednar event."""

    _attr_has_entity_name = True
    _attr_name: str
    _attr_icon = ICON
    _attr_should_poll = False
    _attr_event_types = [TRIGGER_EVENT_START, TRIGGER_EVENT_END]

    def __init__(
        self,
        name: str,
        calendar_entity_id: str,
        unique_id: str,
        event_predicate: Callable[[CalendarEvent], bool],
    ) -> None:
        """Initialize the calendar event sensor."""
        self._attr_unique_id = unique_id
        self._attr_name = name.capitalize()
        self._calendar_entity_id = calendar_entity_id
        self._event_predicate = event_predicate
        self._listener: CalendarEventListener | None = None

    async def async_added_to_hass(self) -> None:
        """Register triggers for listening to calendar event state changes.

        This will register any triggers to get notified about any calendar event
        start/end events.
        """
        await super().async_added_to_hass()

        component: EntityComponent = self.hass.data[DOMAIN]
        if not (
            entity := component.get_entity(self._calendar_entity_id)
        ) or not isinstance(entity, CalendarEntity):
            raise HomeAssistantError(
                f"Entity does not exist {self.entity_id} or is not a calendar entity"
            )

        self._listener = CalendarEventListener(
            self.hass,
            HassJob(self._trigger_action),
            queued_event_fetcher(
                event_fetcher(self.hass, entity),
                {TRIGGER_EVENT_START, TRIGGER_EVENT_END},
                ZERO,
                self._event_predicate,
            ),
        )
        await self._listener.async_attach()

    @callback
    def _trigger_action(
        self,
        queued_event: QueuedCalendarEvent,
    ) -> None:
        self._trigger_event(
            queued_event.event_type, {CALENDAR_EVENT: queued_event.event.as_dict()}
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Remove triggers."""
        await super().async_will_remove_from_hass()
        if self._listener is not None:
            self._listener.async_detach()
