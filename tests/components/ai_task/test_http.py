"""Test the HTTP API for AI Task integration."""

from typing import Any

import pytest

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .conftest import TEST_ENTITY_ID, TEST_STRUCTURE

from tests.typing import WebSocketGenerator


async def test_ws_generate_text(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_components: None,
) -> None:
    """Test running a generate text task via the WebSocket API."""
    entity = hass.states.get(TEST_ENTITY_ID)
    assert entity is not None
    assert entity.state == STATE_UNKNOWN

    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "ai_task/generate_text",
            "task_name": "Test Task",
            "entity_id": TEST_ENTITY_ID,
            "task_type": "summary",
            "instructions": "Test prompt",
        }
    )

    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["result"] == "Mock result"

    entity = hass.states.get(TEST_ENTITY_ID)
    assert entity.state != STATE_UNKNOWN


@pytest.mark.parametrize(
    ("structure"),
    [
        TEST_STRUCTURE,
        {"type": "object", "properties": {"key": {"type": "string"}}},
        {"type": "object", "properties": {"key": {"type": "number"}}},
        {"type": "object", "properties": {"key": {"type": "boolean"}}},
        {"anyOf": [{"type": "object", "properties": {"key": {"type": "boolean"}}}]},
        {"type": "string"},
    ],
)
async def test_ws_generate_data(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_components: None,
    structure: dict[str, Any],
) -> None:
    """Test running a generate data task via the WebSocket API."""
    entity = hass.states.get(TEST_ENTITY_ID)
    assert entity is not None
    assert entity.state == STATE_UNKNOWN

    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "ai_task/generate_data",
            "task_name": "Test Task",
            "entity_id": TEST_ENTITY_ID,
            "instructions": "Test prompt",
            "structure": structure,
        }
    )

    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["result"] == {"mock_result": "data"}

    entity = hass.states.get(TEST_ENTITY_ID)
    assert entity.state != STATE_UNKNOWN


@pytest.mark.parametrize(
    ("structure", "expect_message"),
    [
        (
            "invalid-structure",
            "expected dict for dictionary value",
        ),
        (
            {"type": "unknown-type"},
            "Structure is not a valid JSON schema: Unable to convert schema",
        ),
        ({}, "Structure is not a valid JSON schema: Invalid schema, missing type"),
        (
            {"key": "value"},
            "Structure is not a valid JSON schema: Invalid schema, missing type",
        ),
        (
            {"type": "object", "properties": {"key": {"type": "unknown-type"}}},
            "Structure is not a valid JSON schema: Unable to convert schema",
        ),
    ],
)
async def test_ws_generate_data_invalid_structure(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_components: None,
    structure: dict[str, Any],
    expect_message: str,
) -> None:
    """Test running a generate data task with an invalid structure format."""
    entity = hass.states.get(TEST_ENTITY_ID)
    assert entity is not None
    assert entity.state == STATE_UNKNOWN

    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "ai_task/generate_data",
            "task_name": "Test Task",
            "entity_id": TEST_ENTITY_ID,
            "instructions": "Test prompt",
            "structure": structure,
        }
    )

    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"
    message = msg["error"]["message"]
    assert expect_message in message
