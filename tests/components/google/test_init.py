"""The tests for the Google Calendar component."""
from collections.abc import Awaitable, Callable
import datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call, patch

from oauth2client.client import (
    FlowExchangeError,
    OAuth2Credentials,
    OAuth2DeviceCodeError,
)
from oauth2client.file import Storage
import pytest
import yaml

import homeassistant.components.google as google
from homeassistant.components.google import (
    DOMAIN,
    SERVICE_ADD_EVENT,
    GoogleCalendarService,
)
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from .conftest import CALENDAR_ID, ApiResult, YieldFixture

from tests.common import async_fire_time_changed

# Typing helpers
ComponentSetup = Callable[[], Awaitable[bool]]


CODE_CHECK_INTERVAL = 1
CODE_CHECK_ALARM_TIMEDELTA = datetime.timedelta(seconds=CODE_CHECK_INTERVAL * 2)


@pytest.fixture
async def code_expiration_delta() -> datetime.timedelta:
    """Fixture for code expiration time, defaulting to the future."""
    return datetime.timedelta(minutes=3)


@pytest.fixture
async def mock_code_flow(
    code_expiration_delta: datetime.timedelta,
) -> YieldFixture[Mock]:
    """Fixture for initiating OAuth flow."""
    with patch(
        "oauth2client.client.OAuth2WebServerFlow.step1_get_device_and_user_codes",
    ) as mock_flow:
        mock_flow.return_value.user_code_expiry = utcnow() + code_expiration_delta
        mock_flow.return_value.interval = CODE_CHECK_INTERVAL
        yield mock_flow


@pytest.fixture
async def token_scopes() -> list[str]:
    """Fixture for scopes used during test."""
    return ["https://www.googleapis.com/auth/calendar"]


@pytest.fixture
async def creds(token_scopes: list[str]) -> OAuth2Credentials:
    """Fixture that defines creds used in the test."""
    token_expiry = utcnow() + datetime.timedelta(days=7)
    return OAuth2Credentials(
        access_token="ACCESS_TOKEN",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="REFRESH_TOKEN",
        token_expiry=token_expiry,
        token_uri="http://example.com",
        user_agent="n/a",
        scopes=token_scopes,
    )


@pytest.fixture
async def mock_exchange(creds: OAuth2Credentials) -> YieldFixture[Mock]:
    """Fixture for mocking out the exchange for credentials."""
    with patch(
        "oauth2client.client.OAuth2WebServerFlow.step2_exchange", return_value=creds
    ) as mock:
        yield mock


@pytest.fixture
async def token_file(
    hass: HomeAssistant, creds: OAuth2Credentials, token_filename: Path
) -> None:
    """Fixture to populate an existing token file."""
    storage = Storage(token_filename)
    storage.put(creds)


@pytest.fixture
async def calendars_config() -> list[dict[str, Any]]:
    """Fixture for tests to override default calendar configuration."""
    return [
        {
            "cal_id": CALENDAR_ID,
            "entities": [
                {
                    "device_id": "backyard_light",
                    "name": "Backyard Light",
                    "search": "#Backyard",
                    "track": True,
                }
            ],
        }
    ]


@pytest.fixture
async def calendars_yaml(
    hass: HomeAssistant,
    calendars_config: list[dict[str, Any]],
    yaml_devices_filename: Path,
) -> None:
    """Fixture that prepares the calendars.yaml file."""
    with open(yaml_devices_filename, "w") as out:
        yaml.dump(calendars_config, out)


@pytest.fixture
async def mock_load_platform() -> YieldFixture[Mock]:
    """Fixture that counts when calendars are loaded, to exercise success case."""
    with patch("homeassistant.components.google.discovery.load_platform") as mock:
        yield mock


@pytest.fixture
async def mock_notification() -> YieldFixture[Mock]:
    """Fixture for capturing persistent notifications."""
    with patch("homeassistant.components.persistent_notification.create") as mock:
        yield mock


@pytest.fixture
async def config() -> dict[str, Any]:
    """Fixture for overriding component config."""
    return {DOMAIN: {CONF_CLIENT_ID: "client-id", CONF_CLIENT_SECRET: "client-ecret"}}


@pytest.fixture
async def component_setup(
    hass: HomeAssistant, config: dict[str, Any]
) -> ComponentSetup:
    """Fixture for setting up the integration."""

    async def _setup_func() -> bool:
        result = await async_setup_component(hass, DOMAIN, config)
        await hass.async_block_till_done()
        return result

    return _setup_func


@pytest.fixture
async def google_service() -> YieldFixture[GoogleCalendarService]:
    """Fixture to capture service calls."""
    with patch("homeassistant.components.google.GoogleCalendarService") as mock:
        yield mock


async def fire_alarm(hass, point_in_time):
    """Fire an alarm and wait for callbacks to run."""
    with patch("homeassistant.util.dt.utcnow", return_value=point_in_time):
        async_fire_time_changed(hass, point_in_time)
        await hass.async_block_till_done()


@pytest.mark.parametrize("config", [{}])
async def test_setup_config_empty(
    hass: HomeAssistant, component_setup: ComponentSetup, mock_notification: Mock
):
    """Test setup component with an empty configuruation."""
    assert await component_setup()
    assert hass.data[google.DATA_INDEX] == {}

    mock_notification.assert_not_called()


async def test_init_success(
    hass: HomeAssistant,
    google_service: GoogleCalendarService,
    mock_code_flow: Mock,
    mock_exchange: Mock,
    mock_notification: Mock,
    mock_load_platform: Mock,
    calendars_yaml: None,
    component_setup: ComponentSetup,
) -> None:
    """Test successful creds setup."""
    assert await component_setup()

    # Run one tick to invoke the credential exchange check
    now = utcnow()
    await fire_alarm(hass, now + CODE_CHECK_ALARM_TIMEDELTA)

    mock_load_platform.assert_called_once()

    # One calendar configured
    assert len(hass.data[google.DATA_INDEX]) == 1

    mock_notification.assert_called()
    assert "We are all setup now" in mock_notification.call_args[0][1]


async def test_code_error(
    hass: HomeAssistant,
    mock_code_flow: Mock,
    component_setup: ComponentSetup,
    mock_notification: Mock,
) -> None:
    """Test loading the integration with no existing credentials."""

    with patch(
        "oauth2client.client.OAuth2WebServerFlow.step1_get_device_and_user_codes",
        side_effect=OAuth2DeviceCodeError("Test Failure"),
    ):
        assert await component_setup()

    mock_notification.assert_called()
    assert "Error: Test Failure" in mock_notification.call_args[0][1]


@pytest.mark.parametrize("code_expiration_delta", [datetime.timedelta(minutes=-5)])
async def test_expired_after_exchange(
    hass: HomeAssistant,
    mock_code_flow: Mock,
    component_setup: ComponentSetup,
    mock_notification: Mock,
) -> None:
    """Test loading the integration with no existing credentials."""

    assert await component_setup()

    now = utcnow()
    await fire_alarm(hass, now + CODE_CHECK_ALARM_TIMEDELTA)

    mock_notification.assert_called()
    assert (
        "Authentication code expired, please restart Home-Assistant and try again"
        in mock_notification.call_args[0][1]
    )


async def test_exchange_error(
    hass: HomeAssistant,
    mock_code_flow: Mock,
    component_setup: ComponentSetup,
    mock_notification: Mock,
) -> None:
    """Test an error while exchanging the code for credentials."""

    with patch(
        "oauth2client.client.OAuth2WebServerFlow.step2_exchange",
        side_effect=FlowExchangeError(),
    ):
        assert await component_setup()

        now = utcnow()
        await fire_alarm(hass, now + CODE_CHECK_ALARM_TIMEDELTA)

    mock_notification.assert_called()
    assert "In order to authorize Home-Assistant" in mock_notification.call_args[0][1]


async def test_existing_token(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    mock_load_platform: Mock,
    google_service: GoogleCalendarService,
    calendars_yaml: None,
    mock_notification: Mock,
) -> None:
    """Test setup with an existing token file."""
    assert await component_setup()

    mock_load_platform.assert_called_once()

    # One calendar configured
    assert len(hass.data[google.DATA_INDEX]) == 1

    # No notifications on success
    mock_notification.assert_not_called()


@pytest.mark.parametrize(
    "token_scopes", ["https://www.googleapis.com/auth/calendar.readonly"]
)
async def test_existing_token_missing_scope(
    hass: HomeAssistant,
    token_scopes: list[str],
    token_file: None,
    component_setup: ComponentSetup,
    mock_load_platform: Mock,
    google_service: GoogleCalendarService,
    calendars_yaml: None,
    mock_notification: Mock,
    mock_code_flow: Mock,
    mock_exchange: Mock,
) -> None:
    """Test setup where existing token does not have sufficient scopes."""
    assert await component_setup()

    # Run one tick to invoke the credential exchange check
    now = utcnow()
    await fire_alarm(hass, now + CODE_CHECK_ALARM_TIMEDELTA)
    assert len(mock_exchange.mock_calls) == 1

    mock_load_platform.assert_called_once()

    # One calendar configured
    assert len(hass.data[google.DATA_INDEX]) == 1

    # No notifications on success
    mock_notification.assert_called()
    assert "We are all setup now" in mock_notification.call_args[0][1]


@pytest.mark.parametrize("calendars_config", [[{"cal_id": "invalid-schema"}]])
async def test_calendar_yaml_missing_required_fields(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    mock_load_platform: Mock,
    google_service: GoogleCalendarService,
    calendars_config: list[dict[str, Any]],
    calendars_yaml: None,
    mock_notification: Mock,
) -> None:
    """Test setup with a missing schema fields, ignores the error and continues."""
    assert await component_setup()

    mock_load_platform.assert_not_called()
    mock_notification.assert_not_called()
    assert hass.data[google.DATA_INDEX] == {}


@pytest.mark.parametrize("calendars_config", [[{"missing-cal_id": "invalid-schema"}]])
async def test_invalid_calendar_yaml(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    mock_load_platform: Mock,
    google_service: GoogleCalendarService,
    calendars_config: list[dict[str, Any]],
    calendars_yaml: None,
    mock_notification: Mock,
) -> None:
    """Test setup with missing entity id fields fails to setup the integration."""

    # Integration fails to setup
    assert not await component_setup()

    mock_load_platform.assert_not_called()
    mock_notification.assert_not_called()
    assert hass.data[google.DATA_INDEX] == {}


async def test_invalid_calendar_config_format(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    mock_load_platform: Mock,
    google_service: GoogleCalendarService,
    calendars_config: list[dict[str, Any]],
    calendars_yaml: None,
    mock_notification: Mock,
    yaml_devices_filename: Path,
) -> None:
    """Test setup when the yaml file does not contain yaml."""

    # Write arbitrary binary data that isn't yaml
    with open(yaml_devices_filename, "wb") as out:
        out.write(bytearray(range(1, 100)))

    assert not await component_setup()

    mock_load_platform.assert_not_called()
    mock_notification.assert_not_called()
    assert hass.data[google.DATA_INDEX] == {}


async def test_found_calendar_from_api(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    google_service: GoogleCalendarService,
    mock_calendars_list: ApiResult,
    test_calendar: dict[str, Any],
) -> None:
    """Test finding a calendar from the API."""

    mock_calendars_list({"items": [test_calendar]})

    assert await component_setup()

    # One calendar loaded from the API
    assert len(hass.data[google.DATA_INDEX]) == 1


async def test_add_event(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    google_service: GoogleCalendarService,
    mock_calendars_list: ApiResult,
    test_calendar: dict[str, Any],
    mock_insert_event: Mock,
) -> None:
    """Test service call that adds an event."""

    mock_calendars_list({"items": [test_calendar]})
    assert await component_setup()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_EVENT,
        {
            "calendar_id": CALENDAR_ID,
            "summary": "Summary",
            "description": "Description",
        },
        blocking=True,
    )
    mock_insert_event.assert_called()
    assert mock_insert_event.mock_calls[0] == call(
        calendarId=CALENDAR_ID,
        body={
            "summary": "Summary",
            "description": "Description",
            "start": {},
            "end": {},
        },
    )


@pytest.mark.parametrize(
    "date_fields,start_timedelta,end_timedelta",
    [
        (
            {"in": {"days": 3}},
            datetime.timedelta(days=3),
            datetime.timedelta(days=4),
        ),
        (
            {"in": {"weeks": 1}},
            datetime.timedelta(days=7),
            datetime.timedelta(days=8),
        ),
        (
            {
                "start_date": datetime.date.today().isoformat(),
                "end_date": (
                    datetime.date.today() + datetime.timedelta(days=2)
                ).isoformat(),
            },
            datetime.timedelta(days=0),
            datetime.timedelta(days=2),
        ),
    ],
    ids=["in_days", "in_weeks", "explit_date"],
)
async def test_add_event_date_ranges(
    hass: HomeAssistant,
    token_file: None,
    component_setup: ComponentSetup,
    google_service: GoogleCalendarService,
    mock_calendars_list: ApiResult,
    test_calendar: dict[str, Any],
    mock_insert_event: Mock,
    date_fields: dict[str, Any],
    start_timedelta: datetime.timedelta,
    end_timedelta: datetime.timedelta,
) -> None:
    """Test service call that adds an event with various time ranges."""

    mock_calendars_list({"items": [test_calendar]})
    assert await component_setup()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_EVENT,
        {
            "calendar_id": CALENDAR_ID,
            "summary": "Summary",
            "description": "Description",
            **date_fields,
        },
        blocking=True,
    )
    mock_insert_event.assert_called()

    now = datetime.datetime.now()
    start_date = now + start_timedelta
    end_date = now + end_timedelta

    assert mock_insert_event.mock_calls[0] == call(
        calendarId=CALENDAR_ID,
        body={
            "summary": "Summary",
            "description": "Description",
            "start": {"date": start_date.date().isoformat()},
            "end": {"date": end_date.date().isoformat()},
        },
    )
