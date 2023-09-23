"""Test fitbit component."""

from collections.abc import Awaitable, Callable
from http import HTTPStatus

import pytest

from homeassistant.components.fitbit.const import OAUTH2_TOKEN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry
from tests.test_util.aiohttp import AiohttpClientMocker


async def test_setup(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
) -> None:
    """Test setting up the integration."""
    assert config_entry.state == ConfigEntryState.NOT_LOADED

    assert await integration_setup()
    assert config_entry.state == ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state == ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("token_expiration_time", [12345])
async def test_token_refresh_failure(
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test where token is expired and the refresh attempt fails and will be retried."""

    aioclient_mock.post(
        OAUTH2_TOKEN,
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
    )

    assert not await integration_setup()
    assert config_entry.state == ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize("token_expiration_time", [12345])
async def test_token_requires_reauth(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test where token is expired and the refresh attempt requires reauth."""

    aioclient_mock.post(
        OAUTH2_TOKEN,
        status=HTTPStatus.UNAUTHORIZED,
    )

    assert not await integration_setup()
    assert config_entry.state == ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"
