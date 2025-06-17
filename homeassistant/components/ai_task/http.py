"""HTTP endpoint for AI Task integration."""

from typing import Any

import voluptuous as vol
from voluptuous_openapi import convert_to_voluptuous

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .task import GenTextTaskType, async_generate_data, async_generate_text


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Set up the HTTP API for the conversation integration."""
    websocket_api.async_register_command(hass, websocket_generate_text)
    websocket_api.async_register_command(hass, websocket_generate_data)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_task/generate_text",
        vol.Required("task_name"): str,
        vol.Required("entity_id"): str,
        vol.Required("task_type"): (lambda v: GenTextTaskType(v)),  # pylint: disable=unnecessary-lambda
        vol.Required("instructions"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_generate_text(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run a generate text task."""
    msg.pop("type")
    msg_id = msg.pop("id")
    result = await async_generate_text(hass=hass, **msg)
    connection.send_result(msg_id, result.as_dict())


def _validate_structure_schema(value: Any) -> dict[str, Any]:
    """Validate the structured data schema."""
    if isinstance(value, dict):
        # We will not actually use a voluptuous schema, but using our existing
        # openapi validation logic to ensure the structure is valid.
        try:
            convert_to_voluptuous(value)
        except ValueError as err:
            raise vol.Invalid(f"Structure is not a valid JSON schema: {err}") from err
        return value
    raise vol.Invalid("Structure must be a dictionary.")


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_task/generate_data",
        vol.Required("task_name"): str,
        vol.Required("entity_id"): str,
        vol.Required("instructions"): str,
        vol.Required("structure"): vol.All(dict, _validate_structure_schema),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_generate_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run a generate data task."""
    msg.pop("type")
    msg_id = msg.pop("id")
    result = await async_generate_data(hass=hass, **msg)
    connection.send_result(msg_id, result.as_dict())
