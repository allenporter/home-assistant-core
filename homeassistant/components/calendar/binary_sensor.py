"""Binary sensor for calendar to match a specific Calendar Event."""

from __future__ import annotations

from collections.abc import Callable
import datetime
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity

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
from homeassistant.util import dt as dt_util

from . import CalendarEntity, CalendarEvent
from .const import CALENDAR_EVENT, CONF_SEARCH, DOMAIN, EVENT_END, EVENT_START

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
        _LOGGER.debug("searching summary %s", calendar_event.summary)
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


class CalendarEventSensor(BinarySensorEntity):
    """A sensor that matches a specific calednar event."""

    _attr_has_entity_name = True
    _attr_name: str
    _attr_icon = ICON
    _attr_should_poll = False

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
        self._attr_is_on = False
        self._is_on_count = 0  # Tracks possibly overlapping events

    async def async_added_to_hass(self) -> None:
        """Determine if any events are active right now and register triggers.

        This will register any triggers to get notified about any calendar event
        start/end events. On startup, we also need to handle any currently
        active events to set the initial on/off state.
        """
        await super().async_added_to_hass()

        component: EntityComponent = self.hass.data[DOMAIN]
        if not (
            entity := component.get_entity(self._calendar_entity_id)
        ) or not isinstance(entity, CalendarEntity):
            raise HomeAssistantError(
                f"Entity does not exist {self.entity_id} or is not a calendar entity"
            )

        for active_event in await self._async_get_active_events(entity):
            self._async_consume_event(is_on=True, calendar_event=active_event)

        configs = [
            {
                CONF_PLATFORM: DOMAIN,
                CONF_ENTITY_ID: self._calendar_entity_id,
                CONF_EVENT: event_type,
                CONF_OFFSET: ZERO,
            }
            for event_type in (EVENT_START, EVENT_END)
        ]
        self._unsub = await trigger_helper.async_initialize_triggers(
            self.hass,
            configs,
            self._trigger_action,
            DOMAIN,
            self._attr_name,
            _LOGGER.log,
        )

    async def _async_get_active_events(
        self, entity: CalendarEntity
    ) -> list[CalendarEvent]:
        """Return any currently active events."""
        now = dt_util.utcnow()
        return await entity.async_get_events(
            self.hass, now - datetime.timedelta(minutes=1), now
        )

    @callback
    def _trigger_action(
        self, run_variables: dict[str, Any], context: Context | None = None
    ) -> None:
        trigger_data = run_variables["trigger"]
        is_on = trigger_data[CONF_EVENT] == EVENT_START
        self._async_consume_event(
            is_on, CalendarEvent.from_dict(trigger_data[CALENDAR_EVENT])
        )
        self.async_write_ha_state()

    def _async_consume_event(self, is_on: bool, calendar_event: CalendarEvent) -> None:
        """Determine if the event matches the filters and update appropriate attributes."""
        if not self._event_predicate(calendar_event):
            return
        if is_on:
            self._is_on_count += 1
        else:
            self._is_on_count = max(0, self._is_on_count - 1)
        self._attr_is_on = self._is_on_count > 0

    async def async_will_remove_from_hass(self) -> None:
        """Remove triggers."""
        await super().async_will_remove_from_hass()
        if self._unsub:
            self._unsub()
