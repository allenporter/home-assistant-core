"""Test the Model Context Protocol Server init module."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
import json
import logging
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp.test_utils import TestClient
import mcp
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.auth.exceptions import OAuthRegistrationError
import mcp.client.session
import mcp.client.sse
import mcp.client.streamable_http
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.shared.exceptions import McpError
from pydantic import AnyHttpUrl, AnyUrl
import pytest

from homeassistant.auth import auth_manager_from_config
from homeassistant.components.conversation import DOMAIN as CONVERSATION_DOMAIN
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.mcp_server.const import STATELESS_LLM_API
from homeassistant.components.mcp_server.http import (
    MESSAGES_API,
    SSE_API,
    STREAMABLE_API,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LLM_HASS_API, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    llm,
)
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.setup import async_setup_component

from tests.common import (
    MockConfigEntry,
    ensure_auth_manager_loaded,
    setup_test_component_platform,
)
from tests.components.light.common import MockLight
from tests.typing import ClientSessionGenerator

_LOGGER = logging.getLogger(__name__)

TEST_ENTITY = "light.kitchen"
INITIALIZE_MESSAGE = {
    "jsonrpc": "2.0",
    "id": "request-id-1",
    "method": "initialize",
    "params": {
        "protocolVersion": "1.0",
        "capabilities": {},
        "clientInfo": {
            "name": "test",
            "version": "1",
        },
    },
}
EVENT_PREFIX = "event: "
DATA_PREFIX = "data: "
EXPECTED_PROMPT_SUFFIX = """
- names: Kitchen Light
  domain: light
  areas: Kitchen
"""


@pytest.fixture
async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the config entry."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.fixture(autouse=True)
async def mock_entities(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    area_registry: ar.AreaRegistry,
    setup_integration: None,
) -> None:
    """Fixture to expose entities to the conversation agent."""
    entity = MockLight("Kitchen Light", STATE_OFF)
    entity.entity_id = TEST_ENTITY
    entity.unique_id = "test-light-unique-id"
    setup_test_component_platform(hass, LIGHT_DOMAIN, [entity])

    assert await async_setup_component(
        hass,
        LIGHT_DOMAIN,
        {LIGHT_DOMAIN: [{"platform": "test"}]},
    )
    await hass.async_block_till_done()
    kitchen = area_registry.async_get_or_create("Kitchen")
    entity_registry.async_update_entity(TEST_ENTITY, area_id=kitchen.id)

    async_expose_entity(hass, CONVERSATION_DOMAIN, TEST_ENTITY, True)


async def sse_response_reader(
    response: aiohttp.ClientResponse,
) -> AsyncGenerator[tuple[str, str]]:
    """Read SSE responses from the server and emit event messages.

    SSE responses are formatted as:
        event: event-name
        data: event-data
    and this function emits each event-name and event-data as a tuple.
    """
    it = aiter(response.content)
    while True:
        line = (await anext(it)).decode()
        if not line.startswith(EVENT_PREFIX):
            raise ValueError("Expected event")
        event = line[len(EVENT_PREFIX) :].strip()
        line = (await anext(it)).decode()
        if not line.startswith(DATA_PREFIX):
            raise ValueError("Expected data")
        data = line[len(DATA_PREFIX) :].strip()
        line = (await anext(it)).decode()
        assert line == "\r\n"
        yield event, data


async def test_http_sse(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test SSE endpoint can be used to receive MCP messages."""

    client = await hass_client()

    # Start an SSE session
    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.OK

    # Decode a single SSE response that sends the messages endpoint
    reader = sse_response_reader(response)
    event, endpoint_url = await anext(reader)
    assert event == "endpoint"

    # Send an initialize message on the messages endpoint
    response = await client.post(endpoint_url, json=INITIALIZE_MESSAGE)
    assert response.status == HTTPStatus.OK

    # Decode the initialize response event message from the SSE stream
    event, data = await anext(reader)
    assert event == "message"
    message = json.loads(data)
    assert message.get("jsonrpc") == "2.0"
    assert message.get("id") == "request-id-1"
    assert "serverInfo" in message.get("result", {})
    assert "protocolVersion" in message.get("result", {})


async def test_http_messages_missing_session_id(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test the tools list endpoint."""

    client = await hass_client()
    response = await client.post(MESSAGES_API.format(session_id="invalid-session-id"))
    assert response.status == HTTPStatus.NOT_FOUND
    response_data = await response.text()
    assert response_data == "Could not find session ID 'invalid-session-id'"


async def test_http_messages_invalid_message_format(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test the tools list endpoint."""

    client = await hass_client()
    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.OK
    reader = sse_response_reader(response)
    event, endpoint_url = await anext(reader)
    assert event == "endpoint"

    response = await client.post(endpoint_url, json={"invalid": "message"})
    assert response.status == HTTPStatus.BAD_REQUEST
    response_data = await response.text()
    assert response_data == "Could not parse message"


async def test_http_sse_multiple_config_entries(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test the SSE endpoint will fail with multiple config entries.

    This cannot happen in practice as the integration only supports a single
    config entry, but this is added for test coverage.
    """

    config_entry = MockConfigEntry(
        domain="mcp_server", data={CONF_LLM_HASS_API: ["llm-api-id"]}
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)

    client = await hass_client()

    # Attempt to start an SSE session will fail
    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.NOT_FOUND
    response_data = await response.text()
    assert "Found multiple Model Context Protocol" in response_data


async def test_http_sse_no_config_entry(
    hass: HomeAssistant,
    setup_integration: None,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test the SSE endpoint fails with a missing config entry."""

    await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED

    client = await hass_client()

    # Start an SSE session
    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.NOT_FOUND
    response_data = await response.text()
    assert "Model Context Protocol server is not configured" in response_data


async def test_http_messages_no_config_entry(
    hass: HomeAssistant,
    setup_integration: None,
    config_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test the message endpoint will fail if the config entry is unloaded."""

    client = await hass_client()

    # Start an SSE session
    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.OK
    reader = sse_response_reader(response)
    event, endpoint_url = await anext(reader)
    assert event == "endpoint"

    # Invalidate the session by unloading the config entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED

    # Reload the config entry and ensure the session is not found
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED

    response = await client.post(endpoint_url, json=INITIALIZE_MESSAGE)
    assert response.status == HTTPStatus.NOT_FOUND
    response_data = await response.text()
    assert "Could not find session ID" in response_data


async def test_http_requires_authentication(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Test the SSE endpoint requires authentication."""

    client = await hass_client_no_auth()

    response = await client.get(SSE_API)
    assert response.status == HTTPStatus.UNAUTHORIZED

    response = await client.post(MESSAGES_API.format(session_id="session-id"))
    assert response.status == HTTPStatus.UNAUTHORIZED


@pytest.fixture(params=["sse", "streamable"])
def mcp_protocol(request: pytest.FixtureRequest):
    """Fixture to parametrize tests with different MCP protocols."""
    return request.param


@pytest.fixture
async def mcp_url(mcp_protocol: str, hass_client: ClientSessionGenerator) -> str:
    """Fixture to get the MCP integration URL."""
    if mcp_protocol == "sse":
        url = SSE_API
    else:
        url = STREAMABLE_API
    client = await hass_client()
    return str(client.make_url(url))


@asynccontextmanager
async def mcp_sse_session(
    hass: HomeAssistant,
    mcp_url: str,
    *,
    access_token: str | None = None,
) -> AsyncGenerator[mcp.client.session.ClientSession]:
    """Create an MCP session."""

    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    async with (
        mcp.client.sse.sse_client(mcp_url, headers=headers) as streams,
        mcp.client.session.ClientSession(*streams) as session,
    ):
        await session.initialize()
        yield session


@asynccontextmanager
async def mcp_streamable_session(
    hass: HomeAssistant,
    mcp_url: str,
    *,
    access_token: str | None = None,
    auth: OAuthClientProvider | None = None,
) -> AsyncGenerator[mcp.client.session.ClientSession]:
    """Create an MCP session."""

    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    async with (
        mcp.client.streamable_http.streamable_http_client(
            mcp_url,
            http_client=create_async_httpx_client(hass, headers=headers, auth=auth),
        ) as (read_stream, write_stream, _),
        mcp.client.session.ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


@pytest.fixture(name="mcp_client")
def mcp_client_fixture(mcp_protocol: str) -> Any:
    """Fixture to parametrize tests with different MCP clients."""
    if mcp_protocol == "sse":
        return mcp_sse_session
    if mcp_protocol == "streamable":
        return mcp_streamable_session
    raise ValueError(f"Unknown MCP protocol: {mcp_protocol}")


@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_mcp_tools_list(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the tools list endpoint."""

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        result = await session.list_tools()

    # Pick a single arbitrary tool and test that description and parameters
    # are converted correctly.
    tool = next(iter(tool for tool in result.tools if tool.name == "HassTurnOn"))
    assert tool.name == "HassTurnOn"
    assert tool.description is not None
    assert tool.inputSchema
    assert tool.inputSchema.get("type") == "object"
    properties = tool.inputSchema.get("properties")
    assert properties.get("name") == {"type": "string"}


@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_mcp_tool_call(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the tool call endpoint."""

    state = hass.states.get("light.kitchen")
    assert state
    assert state.state == STATE_OFF

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        result = await session.call_tool(
            name="HassTurnOn",
            arguments={"name": "kitchen light"},
        )

    assert not result.isError
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    # The content is the raw tool call payload
    content = json.loads(result.content[0].text)
    assert content.get("data", {}).get("success")
    assert not content.get("data", {}).get("failed")

    # Verify tool call invocation
    state = hass.states.get("light.kitchen")
    assert state
    assert state.state == STATE_ON


async def test_mcp_tool_call_failed(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the tool call endpoint with a failure."""

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        result = await session.call_tool(
            name="HassTurnOn",
            arguments={"name": "backyard"},
        )

    assert result.isError
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert "Error calling tool" in result.content[0].text


@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_prompt_list(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the list prompt endpoint."""

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        result = await session.list_prompts()

    assert len(result.prompts) == 1
    prompt = result.prompts[0]
    assert prompt.name == "Assist"
    assert prompt.description == "Default prompt for Home Assistant Assist API"


@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_prompt_get(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the get prompt endpoint."""

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        result = await session.get_prompt(name="Assist")

    assert result.description == "Default prompt for Home Assistant Assist API"
    assert len(result.messages) == 1
    assert result.messages[0].role == "assistant"
    assert result.messages[0].content.type == "text"
    assert "When controlling Home Assistant" in result.messages[0].content.text
    assert result.messages[0].content.text.endswith(EXPECTED_PROMPT_SUFFIX)


async def test_get_unknown_prompt(
    hass: HomeAssistant,
    setup_integration: None,
    mcp_url: str,
    mcp_client: Any,
    hass_supervisor_access_token: str,
) -> None:
    """Test the get prompt endpoint."""

    async with mcp_client(
        hass, mcp_url, access_token=hass_supervisor_access_token
    ) as session:
        with pytest.raises(McpError):
            await session.get_prompt(name="Unknown")


class InMemoryTokenStorage(TokenStorage):
    """Demo In-memory token storage implementation."""

    def __init__(self) -> None:
        """Initialize the storage."""
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Get stored tokens."""
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store tokens."""
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Get stored client information."""
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store client information."""
        self.client_info = client_info


class OAuthCallbackDriver:
    """Driver to handle OAuth callbacks.

    This handles callbacks from the OAuth provider and emulates performing
    login and capturing the OAuth code. This is invoked by the MCP OAuth
    provider.
    """

    def __init__(self, client: TestClient) -> None:
        """Initialize the callback driver."""
        self._client = client
        self._state: str | None = None
        self._client_id: str | None = None
        self._redirect_uri: str | None = None

    async def handle_redirect(self, auth_url: str) -> None:
        """Capture the redirect URL."""
        _LOGGER.info("Emulating redirect to: %s", auth_url)
        parsed_auth_url = urlparse(auth_url)
        parsed_query = parse_qs(parsed_auth_url.query)
        self._state = parsed_query.get("state", [None])[0]
        self._client_id = parsed_query.get("client_id", [None])[0]
        self._redirect_uri = parsed_query.get("redirect_uri", [None])[0]

    async def handle_callback(self) -> tuple[str, str | None]:
        """Emulate a login and invoking callback."""
        resp = await self._client.post(
            "/auth/login_flow",
            json={
                "client_id": self._client_id,
                "handler": ["insecure_example", None],
                "redirect_uri": self._redirect_uri,
            },
        )
        assert resp.status == HTTPStatus.OK
        step = await resp.json()

        resp = await self._client.post(
            f"/auth/login_flow/{step['flow_id']}",
            json={
                "client_id": self._client_id,
                "username": "test-user",
                "password": "test-pass",
            },
        )
        assert resp.status == HTTPStatus.OK
        step = await resp.json()
        code = step["result"]
        _LOGGER.info("Emulated OAuth login, got code: %s", code)
        return (code, self._state)


async def setup_authentication(hass: HomeAssistant, external_url: str) -> None:
    """Set up authentication for testing."""
    hass.auth = await auth_manager_from_config(
        hass,
        [
            {
                "type": "insecure_example",
                "users": [{"username": "test-user", "password": "test-pass"}],
            }
        ],
        [{"type": "notify"}],
    )
    ensure_auth_manager_loaded(hass.auth)
    await async_setup_component(hass, "auth", {"http": {}})

    cred = await hass.auth.auth_providers[0].async_get_or_create_credentials(
        {"username": "test-user"}
    )
    await hass.auth.async_get_or_create_user(cred)

    # Ensure the Auth Server metadata returned points back to this server
    await async_process_ha_core_config(
        hass,
        {"external_url": str(external_url)},
    )


@pytest.mark.parametrize("mcp_protocol", ["streamable"])
@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_oauth_flow_indieauth(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
    mcp_url: str,
    mcp_client: Any,
) -> None:
    """Test the OAuth flow with Home Assistant MCP server."""
    client = await hass_client()
    await setup_authentication(hass, str(client.make_url("/")))

    # Use an IndieAuth style client id and redirect uri
    client_id = "http://example.com"
    callback_url = "http://example.com/callback"

    storage = InMemoryTokenStorage()
    client_info = OAuthClientInformationFull(
        redirect_uris=[AnyUrl(str(callback_url))],
        client_id=client_id,
        client_secret="ignored",
    )
    await storage.set_client_info(client_info)

    oauth_callback_driver = OAuthCallbackDriver(client)
    oauth_auth = OAuthClientProvider(
        server_url=mcp_url,
        client_metadata=client_info,
        storage=storage,
        redirect_handler=oauth_callback_driver.handle_redirect,
        callback_handler=oauth_callback_driver.handle_callback,
    )
    async with mcp_client(hass, mcp_url, auth=oauth_auth) as session:
        result = await session.list_tools()

        # Verify we got a valid response
        assert result
        assert len(result.tools) > 0
        tool = next(iter(tool for tool in result.tools if tool.name == "HassTurnOn"))
        assert tool.name == "HassTurnOn"


@pytest.mark.parametrize("mcp_protocol", ["streamable"])
@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_oauth_flow_cimd(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
    mcp_url: str,
    mcp_client: Any,
) -> None:
    """Test the OAuth flow with Home Assistant MCP server."""
    client = await hass_client()
    await setup_authentication(hass, str(client.make_url("/")))

    # Use an IndieAuth style client id and redirect uri.
    # For now we serve the client metadata from our own server, but this
    # test could be improved by mocking the httpx requests for metadata.
    client_metadata_url = client.make_url("/oauth/metadata.json")
    callback_url = client.make_url("/my_client/callback")

    storage = InMemoryTokenStorage()

    # Note: We don't set the client ID in our storage.
    client_info = OAuthClientInformationFull(
        redirect_uris=[AnyUrl(str(callback_url))],
        client_id=str(client_metadata_url),
        client_secret="ignored",
        client_metadata_url=AnyHttpUrl(str(client_metadata_url)),
    )

    oauth_callback_driver = OAuthCallbackDriver(client)

    # Fake the client metadata URL SSL check
    with patch(
        "mcp.client.auth.oauth2.is_valid_client_metadata_url", return_value=True
    ):
        oauth_auth = OAuthClientProvider(
            server_url=mcp_url,
            client_metadata=client_info,
            storage=storage,
            redirect_handler=oauth_callback_driver.handle_redirect,
            callback_handler=oauth_callback_driver.handle_callback,
            client_metadata_url=str(client_metadata_url),
        )
    async with mcp_client(hass, mcp_url, auth=oauth_auth) as session:
        result = await session.list_tools()

        # Verify we got a valid response
        assert result
        assert len(result.tools) > 0
        tool = next(iter(tool for tool in result.tools if tool.name == "HassTurnOn"))
        assert tool.name == "HassTurnOn"


@pytest.mark.parametrize("mcp_protocol", ["streamable"])
@pytest.mark.parametrize("llm_hass_api", [llm.LLM_API_ASSIST, STATELESS_LLM_API])
async def test_oauth_dynamic_client_registration_not_supported(
    hass: HomeAssistant,
    setup_integration: None,
    hass_client: ClientSessionGenerator,
    mcp_url: str,
    mcp_client: Any,
) -> None:
    """Verify that Home Assistant does not support Dynamic Client Registration."""
    client = await hass_client()
    await setup_authentication(hass, str(client.make_url("/")))

    # Use an IndieAuth style client id and redirect uri, but without any token
    # storage. The MCP client will attempt dynamic client registration since
    # it requires CIMD for this to work.
    client_id = "http://example.com"
    callback_url = "http://example.com/callback"

    storage = InMemoryTokenStorage()
    client_info = OAuthClientMetadata(
        redirect_uris=[AnyUrl(str(callback_url))],
        client_id=client_id,
        client_secret="ignored",
    )

    oauth_callback_driver = OAuthCallbackDriver(client)
    oauth_auth = OAuthClientProvider(
        server_url=mcp_url,
        client_metadata=client_info,
        storage=storage,
        redirect_handler=oauth_callback_driver.handle_redirect,
        callback_handler=oauth_callback_driver.handle_callback,
    )
    with pytest.raises(ExceptionGroup) as exc_info:
        async with mcp_client(hass, mcp_url, auth=oauth_auth) as session:
            await session.list_tools()

    assert exc_info.value.exceptions
    assert isinstance(exc_info.value.exceptions[0], OAuthRegistrationError)
    assert "Registration failed: 404 404: Not Found" in str(
        exc_info.value.exceptions[0]
    )
