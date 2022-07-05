"""Template platform."""

from __future__ import annotations

import asyncio
from datetime import datetime

from homeassistant.core import HomeAssistant

from . import DOMAIN, CalendarEntity


def example_call(hass: HomeAssistant) -> str:
    """Return events during a specified time."""
    return "3"


def get_events(
    hass: HomeAssistant, entity_id: str, start_date: datetime, end_date: datetime
) -> list | None:
    """Return events during a specified time."""
    return asyncio.run_coroutine_threadsafe(
        async_get_events(hass, entity_id, start_date, end_date), hass.loop
    ).result()


async def async_get_events(
    hass: HomeAssistant, entity_id: str, start_date: datetime, end_date: datetime
) -> list | None:
    """Return events during a specified time."""
    component = hass.data[DOMAIN]
    entity = component.get_entity(entity_id)
    if not isinstance(entity, CalendarEntity):
        return None
    event_list = await entity.async_get_events(hass, start_date, end_date)
    return event_list


TEMPLATE_FUNCTIONS = {
    "example_call",
    "get_events",
}
