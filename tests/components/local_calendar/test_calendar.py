"""Tests for calendar platform of local calendar."""

from collections.abc import Awaitable, Callable
import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import patch
import urllib

from aiohttp import ClientSession
import pytest

from homeassistant.components.local_calendar import LocalCalendarStore
from homeassistant.components.local_calendar.const import CONF_CALENDAR_NAME, DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import DATE_STR_FORMAT
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util

from tests.common import MockConfigEntry

CALENDAR_NAME = "Light Schedule"
FRIENDLY_NAME = "Light schedule"
TEST_ENTITY = "calendar.light_schedule"


class FakeStore(LocalCalendarStore):
    """Mock storage implementation."""

    def __init__(self, hass: HomeAssistant, path: Path) -> None:
        """Initialize FakeStore."""
        super().__init__(hass, path)
        self._content = ""

    def _load(self) -> str:
        """Read from calendar storage."""
        return self._content

    def _store(self, ics_content: str) -> None:
        """Persist the calendar storage."""
        self._content = ics_content


@pytest.fixture(name="store", autouse=True)
def mock_store() -> None:
    """Test cleanup, remove any media storage persisted during the test."""

    def new_store(hass: HomeAssistant, path: Path) -> FakeStore:
        return FakeStore(hass, path)

    with patch(
        "homeassistant.components.local_calendar.LocalCalendarStore", new=new_store
    ):
        yield


@pytest.fixture(autouse=True)
def set_time_zone(hass: HomeAssistant):
    """Set the time zone for the tests."""
    # Set our timezone to CST/Regina so we can check calculations
    # This keeps UTC-6 all year round
    hass.config.set_time_zone("America/Regina")


@pytest.fixture(name="config_entry")
def mock_config_entry() -> MockConfigEntry:
    """Fixture for mock configuration entry."""
    return MockConfigEntry(domain=DOMAIN, data={CONF_CALENDAR_NAME: CALENDAR_NAME})


@pytest.fixture(name="setup_integration")
async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the integration."""
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


@pytest.fixture(name="create_event")
def create_event_fixture(
    hass: HomeAssistant,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Fixture to simplify creating events for tests."""

    async def _create(data: dict[str, Any]) -> None:
        await hass.services.async_call(
            DOMAIN,
            "create_event",
            data,
            target={"entity_id": TEST_ENTITY},
            blocking=True,
        )

    return _create


@pytest.fixture(name="delete_event")
def delete_event_fixture(
    hass: HomeAssistant,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Fixture to simplify deleting events for tests."""

    async def _delete(data: dict[str, Any]) -> None:
        await hass.services.async_call(
            DOMAIN,
            "delete_event",
            data,
            target={"entity_id": TEST_ENTITY},
            blocking=True,
        )

    return _delete


GetEventsFn = Callable[[str, str], Awaitable[dict[str, Any]]]


@pytest.fixture(name="get_events")
def get_events_fixture(
    hass_client: Callable[..., Awaitable[ClientSession]]
) -> GetEventsFn:
    """Fetch calendar events from the HTTP API."""

    async def _fetch(start: str, end: str) -> None:
        client = await hass_client()
        response = await client.get(
            f"/api/calendars/{TEST_ENTITY}?start={urllib.parse.quote(start)}&end={urllib.parse.quote(end)}"
        )
        assert response.status == HTTPStatus.OK
        return await response.json()

    return _fetch


def event_fields(data: dict[str, str]) -> dict[str, str]:
    """Filter event API response to minimum fields."""
    return {k: data.get(k) for k in ["summary", "start", "end"] if data.get(k)}


async def test_empty_calendar(hass, setup_integration, get_events):
    """Test querying the API and fetching events."""
    events = await get_events("1997-07-14T00:00:00", "1997-07-16T00:00:00")
    assert len(events) == 0

    state = hass.states.get(TEST_ENTITY)
    assert state.name == FRIENDLY_NAME
    assert state.state == STATE_OFF
    assert dict(state.attributes) == {
        "friendly_name": FRIENDLY_NAME,
    }


async def test_api_date_time_event(setup_integration, create_event, get_events):
    """Test an event with a start/end date time."""
    await create_event(
        {
            "summary": "Bastille Day Party",
            "start_date_time": "1997-07-14T17:00:00+00:00",
            "end_date_time": "1997-07-15T04:00:00+00:00",
        }
    )

    events = await get_events("1997-07-14T00:00:00Z", "1997-07-16T00:00:00Z")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Bastille Day Party",
            "start": {"dateTime": "1997-07-14T11:00:00-06:00"},
            "end": {"dateTime": "1997-07-14T22:00:00-06:00"},
        }
    ]

    # Time range before event
    events = await get_events("1997-07-13T00:00:00Z", "1997-07-14T16:00:00Z")
    assert len(events) == 0
    # Time range after event
    events = await get_events("1997-07-15T05:00:00Z", "1997-07-15T06:00:00Z")
    assert len(events) == 0

    # Overlap with event start
    events = await get_events("1997-07-13T00:00:00Z", "1997-07-14T18:00:00Z")
    assert len(events) == 1
    # Overlap with event end
    events = await get_events("1997-07-15T03:00:00Z", "1997-07-15T06:00:00Z")
    assert len(events) == 1


async def test_api_date_event(setup_integration, create_event, get_events):
    """Test an event with a start/end date all day event."""
    await create_event(
        {
            "summary": "Festival International de Jazz de Montreal",
            "start_date": "2007-06-28",
            "end_date": "2007-07-09",
        }
    )

    events = await get_events("2007-06-20T00:00:00", "2007-07-20T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Festival International de Jazz de Montreal",
            "start": {"date": "2007-06-28"},
            "end": {"date": "2007-07-09"},
        }
    ]

    # Time range before event (timezone is -6)
    events = await get_events("2007-06-26T00:00:00Z", "2007-06-28T01:00:00Z")
    assert len(events) == 0
    # Time range after event
    events = await get_events("2007-07-10T00:00:00Z", "2007-07-11T00:00:00Z")
    assert len(events) == 0

    # Overlap with event start (timezone is -6)
    events = await get_events("2007-06-26T00:00:00Z", "2007-06-28T08:00:00Z")
    assert len(events) == 1
    # Overlap with event end
    events = await get_events("2007-07-09T00:00:00Z", "2007-07-11T00:00:00Z")
    assert len(events) == 1


async def test_active_event(hass, setup_integration, create_event):
    """Test an event with a start/end date time."""
    start = dt_util.now() - datetime.timedelta(minutes=30)
    end = dt_util.now() + datetime.timedelta(minutes=30)
    await create_event(
        {
            "summary": "Evening lights",
            "start_date_time": start,
            "end_date_time": end,
        }
    )

    state = hass.states.get(TEST_ENTITY)
    assert state.name == FRIENDLY_NAME
    assert state.state == STATE_ON
    assert dict(state.attributes) == {
        "friendly_name": FRIENDLY_NAME,
        "message": "Evening lights",
        "all_day": False,
        "description": "",
        "location": "",
        "message": "Evening lights",
        "start_time": start.strftime(DATE_STR_FORMAT),
        "end_time": end.strftime(DATE_STR_FORMAT),
    }


async def test_upcoming_event(hass, setup_integration, create_event):
    """Test an event with a start/end date time."""
    start = dt_util.now() + datetime.timedelta(days=1)
    end = dt_util.now() + datetime.timedelta(days=1, hours=1)
    await create_event(
        {
            "summary": "Evening lights",
            "start_date_time": start,
            "end_date_time": end,
        }
    )

    state = hass.states.get(TEST_ENTITY)
    assert state.name == FRIENDLY_NAME
    assert state.state == STATE_OFF
    assert dict(state.attributes) == {
        "friendly_name": FRIENDLY_NAME,
        "message": "Evening lights",
        "all_day": False,
        "description": "",
        "location": "",
        "message": "Evening lights",
        "start_time": start.strftime(DATE_STR_FORMAT),
        "end_time": end.strftime(DATE_STR_FORMAT),
    }


async def test_recurring_event(setup_integration, create_event, get_events):
    """Test an event with a recurrence rule."""
    await create_event(
        {
            "summary": "Monday meeting",
            "start_date_time": "2022-08-29T09:00:00",
            "end_date_time": "2022-08-29T10:00:00",
            "rrule": "FREQ=WEEKLY",
        }
    )

    events = await get_events("2022-08-20T00:00:00", "2022-09-20T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Monday meeting",
            "start": {"dateTime": "2022-08-29T09:00:00-06:00"},
            "end": {"dateTime": "2022-08-29T10:00:00-06:00"},
        },
        {
            "summary": "Monday meeting",
            "start": {"dateTime": "2022-09-05T09:00:00-06:00"},
            "end": {"dateTime": "2022-09-05T10:00:00-06:00"},
        },
        {
            "summary": "Monday meeting",
            "start": {"dateTime": "2022-09-12T09:00:00-06:00"},
            "end": {"dateTime": "2022-09-12T10:00:00-06:00"},
        },
        {
            "summary": "Monday meeting",
            "start": {"dateTime": "2022-09-19T09:00:00-06:00"},
            "end": {"dateTime": "2022-09-19T10:00:00-06:00"},
        },
    ]


async def test_websocket_delete(
    setup_integration: None,
    get_events: GetEventsFn,
    create_event: Callable[[dict[str, Any]], Awaitable[None]],
    delete_event: Callable[[dict[str, Any]], Awaitable[None]],
):
    """Test websocket delete command."""

    await create_event(
        "create",
        {
            "entity_id": TEST_ENTITY,
            "event": {
                "summary": "Bastille Day Party",
                "dtstart": "1997-07-14T17:00:00+00:00",
                "dtend": "1997-07-15T04:00:00+00:00",
            },
        },
    )

    events = await get_events("1997-07-14T00:00:00", "1997-07-16T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Bastille Day Party",
            "start": {"dateTime": "1997-07-14T11:00:00-06:00"},
            "end": {"dateTime": "1997-07-14T22:00:00-06:00"},
        }
    ]
    uid = "ABC"  # result["uid"]

    # Delete the event
    await delete_event(
        {
            "entity_id": TEST_ENTITY,
            "uid": uid,
        },
    )
    events = await get_events("1997-07-14T00:00:00", "1997-07-16T00:00:00")
    assert list(map(event_fields, events)) == []


async def test_websocket_delete_recurring(
    setup_integration: None,
    get_events: GetEventsFn,
    create_event: Callable[[dict[str, Any]], Awaitable[None]],
    delete_event: Callable[[dict[str, Any]], Awaitable[None]],
):
    """Test deleting a recurring event."""
    await create_event(
        {
            "entity_id": TEST_ENTITY,
            "event": {
                "summary": "Morning Routine",
                "dtstart": "2022-08-22T08:30:00",
                "dtend": "2022-08-22T09:00:00",
                "rrule": "FREQ=DAILY",
            },
        }
    )
    uid = "ABC"  # result["uid"]

    events = await get_events("2022-08-22T00:00:00", "2022-08-26T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-22T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-22T09:00:00-06:00"},
            "recurrence_id": "20220822T083000",
        },
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-23T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-23T09:00:00-06:00"},
            "recurrence_id": "20220823T083000",
        },
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-24T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-24T09:00:00-06:00"},
            "recurrence_id": "20220824T083000",
        },
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-25T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-25T09:00:00-06:00"},
            "recurrence_id": "20220825T083000",
        },
    ]

    # Cancel a single instance and confirm it was removed
    await delete_event(
        {
            "entity_id": TEST_ENTITY,
            "uid": uid,
            "recurrence_id": "20220824T083000",
        },
    )
    events = await get_events("2022-08-22T00:00:00", "2022-08-26T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-22T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-22T09:00:00-06:00"},
            "recurrence_id": "20220822T083000",
        },
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-23T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-23T09:00:00-06:00"},
            "recurrence_id": "20220823T083000",
        },
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-25T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-25T09:00:00-06:00"},
            "recurrence_id": "20220825T083000",
        },
    ]

    # Delete all and future and confirm multiple were removed
    await delete_event(
        {
            "entity_id": TEST_ENTITY,
            "uid": uid,
            "recurrence_id": "20220823T083000",
            "recurrence_range": "THISANDFUTURE",
        },
    )
    events = await get_events("2022-08-22T00:00:00", "2022-08-26T00:00:00")
    assert list(map(event_fields, events)) == [
        {
            "summary": "Morning Routine",
            "start": {"dateTime": "2022-08-22T08:30:00-06:00"},
            "end": {"dateTime": "2022-08-22T09:00:00-06:00"},
            "recurrence_id": "20220822T083000",
        },
    ]
