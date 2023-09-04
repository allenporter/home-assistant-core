"""Test fixtures for calendar integration."""

from collections.abc import Generator
import datetime
import logging
import secrets
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components import calendar
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util

from tests.common import async_fire_time_changed

_LOGGER = logging.getLogger(__name__)


# The trigger sets two alarms: One based on the next event and one
# to refresh the schedule. The test advances the time an arbitrary
# amount to trigger either type of event with a small jitter.
TEST_TIME_ADVANCE_INTERVAL = datetime.timedelta(minutes=1)
TEST_UPDATE_INTERVAL = datetime.timedelta(minutes=7)


class FakeSchedule:
    """Test fixture class for return events in a specific date range."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Initiailize FakeSchedule."""
        self.hass = hass
        self.freezer = freezer
        # Map of event start time to event
        self.events: list[calendar.CalendarEvent] = []

    def create_event(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        summary: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Create a new fake event, used by tests."""
        event = calendar.CalendarEvent(
            start=start,
            end=end,
            summary=summary if summary else f"Event {secrets.token_hex(16)}",
            description=description,
            location=location,
        )
        self.events.append(event)
        return event.as_dict()

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> list[calendar.CalendarEvent]:
        """Get all events in a specific time frame, used by the demo calendar."""
        assert start_date < end_date
        values = []
        for event in self.events:
            if event.start_datetime_local >= end_date:
                continue
            if event.end_datetime_local < start_date:
                continue
            values.append(event)
        return values

    async def fire_time(self, trigger_time: datetime.datetime) -> None:
        """Fire an alarm and wait."""
        _LOGGER.debug("Firing alarm @ %s", dt_util.as_local(trigger_time))
        self.freezer.move_to(trigger_time)
        async_fire_time_changed(self.hass, trigger_time)
        await self.hass.async_block_till_done()

    async def fire_until(self, end: datetime.datetime) -> None:
        """Simulate the passage of time by firing alarms until the time is reached."""

        current_time = dt_util.as_utc(self.freezer())
        if (end - current_time) > (TEST_UPDATE_INTERVAL * 2):
            # Jump ahead to right before the target alarm them to remove
            # unnecessary waiting, before advancing in smaller increments below.
            # This leaves time for multiple update intervals to refresh the set
            # of upcoming events
            await self.fire_time(end - TEST_UPDATE_INTERVAL * 2)

        while dt_util.utcnow() < end:
            self.freezer.tick(TEST_TIME_ADVANCE_INTERVAL)
            await self.fire_time(dt_util.utcnow())

    async def fire_state_changes(
        self, entity_id: str, fire_times: list[datetime.datetime]
    ) -> list[tuple[str, str]]:
        """Fire alarms at the specified times and return the list of state changes."""
        results = []
        for fire_time in fire_times:
            await self.fire_until(fire_time)
            state = self.hass.states.get(entity_id)
            results.append((state.state, dict(state.attributes)))
        return results


@pytest.fixture
def fake_schedule(hass, freezer):
    """Fixture that tests can use to make fake events."""
    schedule = FakeSchedule(hass, freezer)
    with patch(
        "homeassistant.components.demo.calendar.DemoCalendar.async_get_events",
        new=schedule.async_get_events,
    ):
        yield schedule


@pytest.fixture(autouse=True)
def mock_update_interval() -> Generator[None, None, None]:
    """Fixture to override the update interval for refreshing events."""
    with patch(
        "homeassistant.components.calendar.trigger.UPDATE_INTERVAL",
        new=TEST_UPDATE_INTERVAL,
    ):
        yield


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant):
    """Set up the homeassistant integration."""
    await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def set_time_zone(hass: HomeAssistant) -> None:
    """Set the time zone for the tests."""
    # Set our timezone to CST/Regina so we can check calculations
    # This keeps UTC-6 all year round
    hass.config.set_time_zone("America/Regina")
