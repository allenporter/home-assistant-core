"""Tests for conversation entity."""

from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import intent, selector
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from tests.common import mock_restore_cache


async def test_state_set_and_restore(hass: HomeAssistant) -> None:
    """Test we set and restore state in the integration."""
    entity_id = "conversation.home_assistant"
    timestamp = "2023-01-01T23:59:59+00:00"
    mock_restore_cache(hass, (State(entity_id, timestamp),))

    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "conversation", {})

    state = hass.states.get(entity_id)
    assert state
    assert state.state == timestamp

    now = dt_util.utcnow()
    context = Context()

    with (
        patch(
            "homeassistant.components.conversation.default_agent.DefaultAgent.async_process"
        ) as mock_process,
        patch("homeassistant.util.dt.utcnow", return_value=now),
    ):
        intent_response = intent.IntentResponse(language="en")
        intent_response.async_set_speech("response text")
        mock_process.return_value = conversation.ConversationResult(
            response=intent_response,
        )
        await hass.services.async_call(
            "conversation",
            "process",
            {"text": "Hello"},
            context=context,
            blocking=True,
        )

    assert len(mock_process.mock_calls) == 1

    state = hass.states.get(entity_id)
    assert state
    assert state.state == now.isoformat()
    assert state.context is context


async def test_output_structure(hass: HomeAssistant, init_components) -> None:
    """Test structured output with async_converse."""
    with (
        patch(
            "homeassistant.components.conversation.default_agent.DefaultAgent.async_process"
        ) as mock_process,
    ):
        intent_response = intent.IntentResponse(language="en")
        intent_response.async_set_speech(
            {"name": "John Smith", "age": 30}, speech_type="structure"
        )
        mock_process.return_value = conversation.ConversationResult(
            response=intent_response,
        )
        result = await hass.services.async_call(
            "conversation",
            "process",
            {
                "text": "Please generate a profile for a new user",
                "structure": {
                    "name": {
                        "description": "First and last name of the user such as Alice Smith",
                        "selector": {"text": {}},
                    },
                    "age": {
                        "description": "Age of the user",
                        "selector": {
                            "number": {
                                "min": 0,
                                "max": 120,
                            }
                        },
                    },
                },
            },
            blocking=True,
            return_response=True,
        )

    assert len(mock_process.mock_calls) == 1
    conversation_input = mock_process.mock_calls[0][1][0]
    assert conversation_input.text == "Please generate a profile for a new user"
    assert conversation_input.language == "en"

    # Verify the selectors are parsed correctly
    fields = conversation_input.output_structure.fields
    assert len(fields) == 2

    assert fields[0].name == "name"
    assert (
        fields[0].description == "First and last name of the user such as Alice Smith"
    )
    assert isinstance(fields[0].selector, selector.TextSelector)

    assert fields[1].name == "age"
    assert fields[1].description == "Age of the user"
    assert isinstance(fields[1].selector, selector.NumberSelector)

    assert result["response"]["speech"]["structure"]["speech"] == {
        "name": "John Smith",
        "age": 30,
    }


async def test_output_structure_requires_response(
    hass: HomeAssistant, init_components
) -> None:
    """Test structured output can only be used with response values."""
    with pytest.raises(ServiceValidationError, match="Cannot return structured output"):
        await hass.services.async_call(
            "conversation",
            "process",
            {
                "text": "Please generate a profile for a new user",
                "structure": {
                    "name": {
                        "description": "First and last name of the user such as Alice Smith",
                        "selector": {"text": {}},
                    },
                    "age": {
                        "description": "Age of the user",
                        "selector": {
                            "number": {
                                "min": 0,
                                "max": 120,
                            }
                        },
                    },
                },
            },
            blocking=True,
            return_response=False,
        )


@pytest.mark.parametrize(
    ("structure", "expected_exception", "expected_error"),
    [
        (
            {
                "name": {
                    "description": "First and last name of the user such as Alice Smith",
                    "selector": {"invalid-selector": {}},
                },
            },
            vol.Invalid,
            r"Unknown selector type invalid-selector.*",
        ),
        (
            {
                "name": {
                    "description": "First and last name of the user such as Alice Smith",
                    "selector": {
                        "text": {
                            "extra-config": False,
                        }
                    },
                },
            },
            vol.Invalid,
            r"extra keys not allowed.*",
        ),
        (
            {
                "name": {
                    "description": "First and last name of the user such as Alice Smith",
                },
            },
            vol.Invalid,
            r"required key not provided.*",
        ),
        (["name"], vol.Invalid, r"expected a dictionary.*"),
        (
            {
                "name": {
                    "description": "First and last name of the user such as Alice Smith",
                    "selector": {"text": {}},
                    "extra-fields": "Some extra fields",
                },
            },
            vol.Invalid,
            r"extra keys not allowed .*",
        ),
    ],
    ids=(
        "invalid-selector",
        "invalid-selector-config",
        "missing-selector",
        "structure-not-object",
        "extra-fields",
    ),
)
async def test_invalid_output_structure(
    hass: HomeAssistant,
    init_components,
    structure: Any,
    expected_exception: Exception,
    expected_error: str,
) -> None:
    """Test structured output can only be used with response values."""
    with pytest.raises(expected_exception, match=expected_error):
        await hass.services.async_call(
            "conversation",
            "process",
            {
                "text": "Please generate a profile for a new user",
                "structure": structure,
            },
            blocking=True,
            return_response=True,
        )
