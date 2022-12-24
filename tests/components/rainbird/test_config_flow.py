"""Tests for the Rain Bird config flow."""

import asyncio
from collections.abc import Generator
from http import HTTPStatus
from unittest.mock import Mock, patch

import pytest

from homeassistant import config_entries
from homeassistant.components.rainbird import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .conftest import CONFIG_ENTRY_DATA, HOST, PASSWORD, URL

from tests.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse


@pytest.fixture(autouse=True)
async def setup_config_entry() -> None:
    """Fixture to disable config entry setup for exercising config flow."""
    return None


@pytest.fixture(autouse=True)
async def mock_setup() -> Generator[Mock, None, None]:
    """Fixture for patching out integration setup."""

    with patch(
        "homeassistant.components.rainbird.async_setup_entry",
        return_value=True,
    ) as mock_setup:
        yield mock_setup


async def complete_flow(hass: HomeAssistant) -> FlowResult:
    """Start the config flow and enter the host and password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert not result.get("errors")
    assert "flow_id" in result

    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_PASSWORD: PASSWORD},
    )


async def test_controller_flow(hass: HomeAssistant, mock_setup: Mock) -> None:
    """Test the controller is setup correctly."""

    result = await complete_flow(hass)
    assert result.get("type") == "create_entry"
    assert result.get("title") == HOST
    assert "result" in result
    assert result["result"].data == CONFIG_ENTRY_DATA

    assert len(mock_setup.mock_calls) == 1


async def test_controller_cannot_connect(
    hass: HomeAssistant,
    mock_setup: Mock,
    responses: list[AiohttpClientMockResponse],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test an error talking to the controller."""

    # Controller response with a failure
    responses.clear()
    responses.append(
        AiohttpClientMockResponse("POST", URL, status=HTTPStatus.SERVICE_UNAVAILABLE)
    )

    result = await complete_flow(hass)
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert result.get("errors") == {"base": "cannot_connect"}

    assert not mock_setup.mock_calls


async def test_controller_timeout(
    hass: HomeAssistant,
    mock_setup: Mock,
) -> None:
    """Test an error talking to the controller."""

    with patch(
        "homeassistant.components.rainbird.config_flow.async_timeout.timeout",
        side_effect=asyncio.TimeoutError,
    ):
        result = await complete_flow(hass)
        assert result.get("type") == "form"
        assert result.get("step_id") == "user"
        assert result.get("errors") == {"base": "timeout_connect"}

    assert not mock_setup.mock_calls
