"""Support for iCloud Reminders as a todo platform."""
from __future__ import annotations

import logging
from typing import Any

from pyicloud.services import RemindersService

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .account import IcloudAccount
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up todo for iCloud component."""
    account: IcloudAccount = hass.data[DOMAIN][entry.unique_id]

    service: RemindersService | None = await hass.async_add_executor_job(
        account.get_reminders
    )
    if not service:
        return
    entities = []
    for title, items in service.lists.items():
        entities.append(RemindersEntitiy(title, items))

    async_add_entities(entities, True)


class RemindersEntitiy(TodoListEntity):
    """Representation of a iCloud reminders list."""

    _attr_has_entity_name = True

    def __init__(self, title: str, items: list[dict[str, Any]]) -> None:
        """Initialize the to-do list."""
        self._attr_name = title.capitalize()
        self._attr_todo_items = [
            TodoItem(
                summary=item["title"],
                status=TodoItemStatus.NEEDS_ACTION,
            )
            for item in items
        ]
        # self._account = account
        # self._attr_unique_id = f"{device.unique_id}"
        # self._attr_device_info = DeviceInfo(
        #     configuration_url="https://icloud.com/",
        #     identifiers={(DOMAIN, device.unique_id)},
        #     manufacturer="Apple",
        #     model=device.device_model,
        #     name=device.name,
        # )
