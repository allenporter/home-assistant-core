"""Test the Developer Credentials integration."""

from __future__ import annotations

from collections.abc import Callable, Generator
import logging
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientWebSocketResponse
import pytest

from homeassistant import config_entries, data_entry_flow
from homeassistant.components.developer_credentials import (
    AuthorizationServer,
    DeveloperCredential,
)
from homeassistant.components.developer_credentials.const import DOMAIN
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component

from tests.common import mock_platform

CLIENT_ID = "some-client-id"
CLIENT_SECRET = "some-client-secret"
DEVELOPER_CREDENTIAL = DeveloperCredential(CLIENT_ID, CLIENT_SECRET)
ID = "fake_integration_some_client_id"
AUTHORIZE_URL = "https://example.com/auth"
TOKEN_URL = "https://example.com/oauth2/v4/token"
REFRESH_TOKEN = "mock-refresh-token"
ACCESS_TOKEN = "mock-access-token"

TEST_DOMAIN = "fake_integration"


@pytest.fixture
async def authorization_server() -> AuthorizationServer:
    """Fixture AuthorizationServer for mock developer_credentials integration."""
    return AuthorizationServer(AUTHORIZE_URL, TOKEN_URL)


@pytest.fixture
async def config_credential() -> DeveloperCredential | None:
    """Fixture DeveloperCredential for mock developer_credentials integration."""
    return None


@pytest.fixture(autouse=True)
async def mock_developer_credentials_integration(
    hass, authorization_server, config_credential: DeveloperCredential | None
):
    """Mock a developer_credentials integration."""
    hass.config.components.add(TEST_DOMAIN)
    mock_platform(
        hass,
        f"{TEST_DOMAIN}.developer_credentials",
        Mock(
            async_get_authorization_server=AsyncMock(return_value=authorization_server),
            async_get_developer_credential=AsyncMock(return_value=config_credential),
        ),
    )
    assert await async_setup_component(hass, "developer_credentials", {})


class FakeConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Config flow used during tests."""

    DOMAIN = TEST_DOMAIN

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def config_flow_handler(
    hass: HomeAssistant, current_request_with_host: Any
) -> Generator[FakeConfigFlow, None, None]:
    """Fixture for a test config flow."""
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow")
    with patch.dict(config_entries.HANDLERS, {TEST_DOMAIN: FakeConfigFlow}):
        yield FakeConfigFlow


class OAuthFixture:
    """Fixture to facilitate testing an OAuth flow."""

    def __init__(self, hass, hass_client, aioclient_mock):
        """Initialize OAuthFixture."""
        self.hass = hass
        self.hass_client = hass_client
        self.aioclient_mock = aioclient_mock
        self.client_id = CLIENT_ID

    async def complete_external_step(
        self, result: data_entry_flow.FlowResult
    ) -> data_entry_flow.FlowResult:
        """Fixture method to complete the OAuth flow and return the completed result."""
        client = await self.hass_client()
        state = config_entry_oauth2_flow._encode_jwt(
            self.hass,
            {
                "flow_id": result["flow_id"],
                "redirect_uri": "https://example.com/auth/external/callback",
            },
        )
        assert result["url"] == (
            f"{AUTHORIZE_URL}?response_type=code&client_id={self.client_id}"
            "&redirect_uri=https://example.com/auth/external/callback"
            f"&state={state}"
        )
        resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
        assert resp.status == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"

        self.aioclient_mock.post(
            TOKEN_URL,
            json={
                "refresh_token": REFRESH_TOKEN,
                "access_token": ACCESS_TOKEN,
                "type": "bearer",
                "expires_in": 60,
            },
        )

        result = await self.hass.config_entries.flow.async_configure(result["flow_id"])
        assert result.get("type") == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
        assert result.get("title") == "Configuration.yaml"
        assert "data" in result
        assert "token" in result["data"]
        return result


@pytest.fixture
async def oauth_fixture(
    hass: HomeAssistant, hass_client_no_auth: Any, aioclient_mock: Any
) -> OAuthFixture:
    """Fixture for testing the OAuth flow."""
    return OAuthFixture(hass, hass_client_no_auth, aioclient_mock)


class Client:
    """Test client with helper methods for developer credentials websocket."""

    def __init__(self, client):
        """Initialize Client."""
        self.client = client
        self.id = 0

    async def cmd(self, cmd: str, payload: dict[str, Any] = None) -> dict[str, Any]:
        """Send a command and receive the json result."""
        self.id += 1
        await self.client.send_json(
            {
                "id": self.id,
                "type": f"{DOMAIN}/{cmd}",
                **(payload if payload is not None else {}),
            }
        )
        resp = await self.client.receive_json()
        assert resp.get("id") == self.id
        return resp

    async def cmd_result(self, cmd: str, payload: dict[str, Any] = None) -> Any:
        """Send a command and parse the result."""
        resp = await self.cmd(cmd, payload)
        assert resp.get("success")
        assert resp.get("type") == "result"
        return resp.get("result")


ClientFixture = Callable[[], Client]


@pytest.fixture
async def ws_client(
    hass_ws_client: Callable[[...], ClientWebSocketResponse]
) -> ClientFixture:
    """Fixture for creating the test websocket client."""

    async def create_client() -> Client:
        ws_client = await hass_ws_client()
        return Client(ws_client)

    return create_client


async def test_websocket_list_empty(ws_client: ClientFixture):
    """Test websocket list command."""
    client = await ws_client()
    assert await client.cmd_result("list") == []


async def test_websocket_create(ws_client: ClientFixture):
    """Test websocket create command."""
    client = await ws_client()
    result = await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    assert result == {
        CONF_DOMAIN: TEST_DOMAIN,
        CONF_CLIENT_ID: CLIENT_ID,
        CONF_CLIENT_SECRET: CLIENT_SECRET,
        "id": ID,
    }

    result = await client.cmd_result("list")
    assert result == [
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "id": ID,
        }
    ]


async def test_websocket_create_invalid_domain(ws_client: ClientFixture):
    """Test websocket create command."""
    client = await ws_client()
    resp = await client.cmd(
        "create",
        {
            CONF_DOMAIN: "other-domain",
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    assert not resp.get("success")
    assert "error" in resp
    assert resp["error"].get("code") == "invalid_format"
    assert (
        resp["error"].get("message")
        == "No authorization server registered for other-domain"
    )


async def test_websocket_update_not_supported(ws_client: ClientFixture):
    """Test websocket update command in unsupported."""
    client = await ws_client()
    result = await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    assert result == {
        CONF_DOMAIN: TEST_DOMAIN,
        CONF_CLIENT_ID: CLIENT_ID,
        CONF_CLIENT_SECRET: CLIENT_SECRET,
        "id": ID,
    }

    resp = await client.cmd("update", {"developer_credentials_id": ID})
    assert not resp.get("success")
    assert "error" in resp
    assert resp["error"].get("code") == "invalid_format"
    assert resp["error"].get("message") == "Updates not supported"


async def test_websocket_delete(ws_client: ClientFixture):
    """Test websocket delete command."""
    client = await ws_client()

    await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    assert await client.cmd_result("list") == [
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "id": ID,
        }
    ]

    await client.cmd_result("delete", {"developer_credentials_id": ID})
    assert await client.cmd_result("list") == []


@pytest.mark.parametrize("config_credential", [DEVELOPER_CREDENTIAL])
async def test_websocket_yaml_config(ws_client: ClientFixture):
    """Test websocket list command for a yaml based credential."""
    client = await ws_client()

    # Configuration based creds are not returned from the websocket
    assert await client.cmd_result("list") == []

    # Configuration based cannot be deleted
    resp = await client.cmd("delete", {"developer_credentials_id": ID})
    assert not resp.get("success")
    assert "error" in resp
    assert resp["error"].get("code") == "not_found"


async def test_config_flow_no_credentials(hass):
    """Test config flow base case with no credentials registered."""
    result = await hass.config_entries.flow.async_init(
        TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_ABORT
    assert result.get("reason") == "missing_configuration"


async def test_config_flow(
    hass: HomeAssistant,
    ws_client: ClientFixture,
    oauth_fixture: OAuthFixture,
):
    """Test config flow with developer credential registered."""
    client = await ws_client()

    await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    result = await hass.config_entries.flow.async_init(
        TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_EXTERNAL_STEP
    result = await oauth_fixture.complete_external_step(result)
    assert (
        result["data"].get("auth_implementation") == "fake_integration_some_client_id"
    )

    # Verify it is not possible to delete an in-use config entry
    resp = await client.cmd("delete", {"developer_credentials_id": ID})
    assert not resp.get("success")
    assert "error" in resp
    assert resp["error"].get("code") == "unknown_error"


async def test_config_flow_multiple_entries(
    hass: HomeAssistant,
    ws_client: ClientFixture,
    oauth_fixture: OAuthFixture,
):
    """Test config flow with multiple developer credentials registered."""
    client = await ws_client()

    await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID + "2",
            CONF_CLIENT_SECRET: CLIENT_SECRET + "2",
        },
    )
    result = await hass.config_entries.flow.async_init(
        TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_FORM
    assert result.get("step_id") == "pick_implementation"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"implementation": "fake_integration_some_client_id2"},
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_EXTERNAL_STEP
    oauth_fixture.client_id = CLIENT_ID + "2"
    result = await oauth_fixture.complete_external_step(result)
    assert (
        result["data"].get("auth_implementation") == "fake_integration_some_client_id2"
    )


async def test_config_flow_create_delete_credential(
    hass: HomeAssistant,
    ws_client: ClientFixture,
    oauth_fixture: OAuthFixture,
):
    """Test adding and deleting a credential unregisters from the config flow."""
    client = await ws_client()

    await client.cmd_result(
        "create",
        {
            CONF_DOMAIN: TEST_DOMAIN,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    await client.cmd("delete", {"developer_credentials_id": ID})

    result = await hass.config_entries.flow.async_init(
        TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_ABORT
    assert result.get("reason") == "missing_configuration"


@pytest.mark.parametrize("config_credential", [DEVELOPER_CREDENTIAL])
async def test_config_flow_with_config_credential(
    hass, hass_client_no_auth, aioclient_mock, oauth_fixture: OAuthFixture
):
    """Test config flow with developer credential registered."""
    result = await hass.config_entries.flow.async_init(
        TEST_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == data_entry_flow.RESULT_TYPE_EXTERNAL_STEP
    result = await oauth_fixture.complete_external_step(result)
    assert result["data"].get("auth_implementation") == ID
