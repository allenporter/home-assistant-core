"""Event platform for matching start/end of a Calendar Event."""

from __future__ import annotations

from collections.abc import Callable
import datetime
import logging
from typing import Any

from homeassistant.components.event import EventEntity

# from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_EVENT,
    CONF_NAME,
    CONF_OFFSET,
    CONF_PLATFORM,
)
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, trigger as trigger_helper
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
        search = config_entry.options[CONF_SEARCH].lower()
        if calendar_event.summary and search in calendar_event.summary.lower():
            return True
        if calendar_event.description and search in calendar_event.description.lower():
            return True
        return False

    calendar_event_sensor = CalendarEventSensor(
        name=config_entry.options[CONF_NAME],
        calendar_entity_id=calendar_entity_id,
        unique_id=config_entry.entry_id,
        event_predicate=is_match,
    )

    async_add_entities([calendar_event_sensor])


class CalendarEventSensor(EventEntity):
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
        self._unsub: CALLBACK_TYPE | None = None

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
        configs = [
            {
                CONF_PLATFORM: DOMAIN,
                CONF_ENTITY_ID: self._calendar_entity_id,
                CONF_EVENT: event_type,
                CONF_OFFSET: ZERO,
            }
            for event_type in (TRIGGER_EVENT_START, TRIGGER_EVENT_END)
        ]
        self._unsub = await trigger_helper.async_initialize_triggers(
            self.hass,
            configs,
            self._trigger_action,
            DOMAIN,
            self._attr_name,
            _LOGGER.log,
        )

    @callback
    def _trigger_action(
        self, run_variables: dict[str, Any], context: Context | None = None
    ) -> None:
        trigger_data = run_variables["trigger"]
        event_type = trigger_data[CONF_EVENT]
        self._trigger_event(event_type, trigger_data[CALENDAR_EVENT])
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Remove triggers."""
        await super().async_will_remove_from_hass()
        if self._unsub:
            self._unsub()
