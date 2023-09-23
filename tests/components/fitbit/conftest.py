"""Test fixtures for fitbit."""

from collections.abc import Awaitable, Callable
from http import HTTPStatus
import time
from typing import Any

import pytest
from requests_mock.mocker import Mocker

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.components.fitbit.const import DOMAIN, OAUTH_SCOPES
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests.common import MockConfigEntry

CLIENT_ID = "1234"
CLIENT_SECRET = "5678"
PROFILE_USER_ID = "fitbit-api-user-id-1"
FAKE_TOKEN = "some-token"
FAKE_REFRESH_TOKEN = "some-refresh-token"
AUTH_IMPL = "imported-cred"

PROFILE_API_URL = "https://api.fitbit.com/1/user/-/profile.json"


@pytest.fixture(name="token_expiration_time")
def mcok_token_expiration_time() -> float:
    """Fixture for expiration time of the config entry auth token."""
    return time.time() + 86400


@pytest.fixture(name="scopes")
def mock_scopes() -> list[str]:
    """Fixture for expiration time of the config entry auth token."""
    return OAUTH_SCOPES


@pytest.fixture(name="token_entry")
def mock_token_entry(token_expiration_time: float, scopes: list[str]) -> dict[str, Any]:
    """Fixture for OAuth 'token' data for a ConfigEntry."""
    return {
        "access_token": FAKE_TOKEN,
        "refresh_token": FAKE_REFRESH_TOKEN,
        "scope": " ".join(scopes),
        "token_type": "Bearer",
        "expires_at": token_expiration_time,
    }


@pytest.fixture(name="config_entry")
def mock_config_entry(token_entry: dict[str, Any]) -> MockConfigEntry:
    """Fixture for a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": AUTH_IMPL,
            "token": token_entry,
        },
        unique_id=PROFILE_USER_ID,
    )


@pytest.fixture(autouse=True)
async def setup_credentials(hass: HomeAssistant) -> None:
    """Fixture to setup credentials."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass,
        DOMAIN,
        ClientCredential(CLIENT_ID, CLIENT_SECRET),
        AUTH_IMPL,
    )


@pytest.fixture(name="integration_setup")
async def mock_integration_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> Callable[[], Awaitable[bool]]:
    """Fixture to set up the integration."""
    config_entry.add_to_hass(hass)

    async def run() -> bool:
        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return result

    return run


@pytest.fixture(name="profile_id")
async def mock_profile_id() -> str:
    """Fixture for the profile id returned from the API response."""
    return PROFILE_USER_ID


@pytest.fixture(name="profile", autouse=True)
async def mock_profile(requests_mock: Mocker, profile_id: str) -> None:
    """Fixture to setup fake requests made to Fitbit API during config flow."""
    requests_mock.register_uri(
        "GET",
        PROFILE_API_URL,
        status_code=HTTPStatus.OK,
        json={
            "user": {
                "encodedId": profile_id,
                "fullName": "My name",
            },
        },
    )
