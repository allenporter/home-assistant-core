"""Tests for the iCloud config flow."""
import json
from unittest.mock import patch

from pyicloud.base import PyiCloudSession
import pytest
from requests import Response
import requests_mock

from homeassistant.components.icloud.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import MOCK_CONFIG, USERNAME

from tests.common import MockConfigEntry


class ResponseMock(Response):
    """Mocked Response."""

    def __init__(self, result, status_code=200, **kwargs):
        """Set up response mock."""
        Response.__init__(self)
        self.result = result
        self.status_code = status_code
        self.raw = kwargs.get("raw")
        self.headers = kwargs.get("headers", {})

    @property
    def text(self):
        """Return text."""
        return json.dumps(self.result)


class PyiCloudSessionMock(PyiCloudSession):
    """Mocked PyiCloudSession."""

    def __init__(self) -> None:
        """Init PyiCloudSessionMock."""
        self.headers = {}

    def request(self, method, url, **kwargs):
        """Make the request."""
        if method == "POST":
            if url == "https://idmsa.apple.com/appleauth/auth/signin":
                return ResponseMock({})
            if url == "https://setup.icloud.com/setup/ws/1/accountLogin":
                return ResponseMock(
                    {
                        "webservices": {
                            "reminders": {
                                "url": "https://p31-remindersws.icloud.com:443",
                                "status": "active",
                            },
                            "findme": {
                                "url": "https://p31-fmipweb.icloud.com:443",
                                "status": "active",
                            },
                        },
                        "dsInfo": {"hsaVersion": 0, "hsaChallengeRequired": False},
                    }
                )
            if (
                url
                == "https://p31-fmipweb.icloud.com:443/fmipservice/client/web/refreshClient"
            ):
                return ResponseMock(
                    {
                        "userInfo": {
                            "firstName": "First",
                            "lastName": "Last",
                        },
                        "content": [
                            {
                                "id": "MacBookPro10,1",
                                "msg": {},
                                "deviceStatus": "200",
                                "batteryLevel": 0.5,
                                "name": "Macbook",
                            }
                        ],
                    }
                )
        if method == "GET":
            if url == "https://p31-remindersws.icloud.com:443/rd/startup":
                return ResponseMock(
                    {
                        "Collections": [
                            {
                                "guid": "guid-1",
                                "ctag": "ctag-1",
                                "title": "My Reminders",
                            },
                        ],
                        "Reminders": [
                            {
                                "pGuid": "guid-1",
                                "title": "Milk",
                                "description": "Remember the milk",
                            },
                        ],
                    }
                )

        return ResponseMock({})


@pytest.fixture(autouse=True)
def mock_platforms() -> None:
    """Fixture to set platforms in scope."""

    with patch(
        "homeassistant.components.icloud.const.PLATFORMS", return_value=[Platform.TODO]
    ):
        yield


async def test_empty_todo(
    hass: HomeAssistant, requests_mock: requests_mock.Mocker
) -> None:
    """Test that invalid login triggers reauth flow."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", unique_id=USERNAME
    )
    config_entry.add_to_hass(hass)

    with patch("pyicloud.base.PyiCloudSession", return_value=PyiCloudSessionMock()):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("todo.my_reminders")
    assert state
    assert state.state == "1"
    assert dict(state.attributes) == {"friendly_name": "My reminders"}
