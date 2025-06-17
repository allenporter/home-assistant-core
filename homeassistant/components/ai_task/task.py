"""AI tasks to be handled by agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DATA_COMPONENT, GenTextTaskType


async def async_generate_text(
    hass: HomeAssistant,
    *,
    task_name: str,
    entity_id: str,
    task_type: GenTextTaskType,
    instructions: str,
) -> GenTextTaskResult:
    """Run a task in the AI Task integration."""
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    if entity is None:
        raise ValueError(f"AI Task entity {entity_id} not found")

    return await entity.internal_async_generate_text(
        GenTextTask(
            name=task_name,
            type=task_type,
            instructions=instructions,
        )
    )


@dataclass(slots=True)
class GenTextTask:
    """Gen text task to be processed."""

    name: str
    """Name of the task."""

    type: GenTextTaskType
    """Type of the task."""

    instructions: str
    """Instructions on what needs to be done."""

    def __str__(self) -> str:
        """Return task as a string."""
        return f"<GenTextTask {self.type}: {id(self)}>"


@dataclass(slots=True)
class GenTextTaskResult:
    """Result of gen text task."""

    conversation_id: str
    """Unique identifier for the conversation."""

    result: str
    """Result of the task."""

    def as_dict(self) -> dict[str, str]:
        """Return result as a dict."""
        return {
            "conversation_id": self.conversation_id,
            "result": self.result,
        }


async def async_generate_data(
    hass: HomeAssistant,
    *,
    task_name: str,
    entity_id: str,
    instructions: str,
    structure: dict[str, str],
) -> GenDataTaskResult:
    """Run a task in the AI Task integration."""
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    if entity is None:
        raise ValueError(f"AI Task entity {entity_id} not found")

    return await entity.internal_async_generate_data(
        GenDataTask(
            name=task_name,
            instructions=instructions,
            structure=structure,
        )
    )


@dataclass(slots=True)
class GenDataTask:
    """Gen data task to be processed."""

    name: str
    """Name of the task."""

    instructions: str
    """Instructions on what needs to be done."""

    structure: dict[str, str]
    """Structure of the data to be generated in JSON schema format."""

    def __str__(self) -> str:
        """Return task as a string."""
        return f"<GenDataTask: {id(self)}>"


@dataclass(slots=True)
class GenDataTaskResult:
    """Result of gen data task."""

    conversation_id: str
    """Unique identifier for the conversation."""

    result: dict[str, str]
    """Result of the task."""

    def as_dict(self) -> dict[str, Any]:
        """Return result as a dict."""
        return {
            "conversation_id": self.conversation_id,
            "result": self.result,
        }
