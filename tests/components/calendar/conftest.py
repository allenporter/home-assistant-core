<<<<<<< HEAD
"""Test fixtures for calendar sensor platforms."""
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant):
    """Set up the homeassistant integration."""
    await async_setup_component(hass, "homeassistant", {})
=======
"""Test fixtures for calendar component."""
from __future__ import annotations

import datetime
import secrets
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.components import calendar
from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util

from tests.common import async_fire_time_changed

# The trigger sets two alarms: One based on the next event and one
# to refresh the schedule. The test advances the time an arbitrary
# amount to trigger either type of event with a small jitter.
TEST_TIME_ADVANCE_INTERVAL = datetime.timedelta(minutes=1)
TEST_UPDATE_INTERVAL = datetime.timedelta(minutes=7)


class FakeSchedule:
    """Test fixture class for return events in a specific date range."""

    def __init__(self, hass, freezer):
        """Initiailize FakeSchedule."""
        self.hass = hass
        self.freezer = freezer
        # Map of event start time to event
        self.events: list[calendar.CalendarEvent] = []

    def create_event(
        self,
        start: datetime.timedelta,
        end: datetime.timedelta,
        description: str = None,
        location: str = None,
    ) -> dict[str, Any]:
        """Create a new fake event, used by tests."""
        event = calendar.CalendarEvent(
            start=start,
            end=end,
            summary=f"Event {secrets.token_hex(16)}",  # Arbitrary unique data
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
            if start_date < event.start < end_date or start_date < event.end < end_date:
                values.append(event)
        return values

    async def fire_time(self, trigger_time: datetime.datetime) -> None:
        """Fire an alarm and wait."""
        self.freezer.move_to(trigger_time)
        async_fire_time_changed(self.hass, trigger_time)
        await self.hass.async_block_till_done()

    async def fire_until(self, end: datetime.timedelta) -> None:
        """Simulate the passage of time by firing alarms until the time is reached."""
        while dt_util.utcnow() < end:
            self.freezer.tick(TEST_TIME_ADVANCE_INTERVAL)
            await self.fire_time(dt_util.utcnow())


@pytest.fixture
def fake_schedule(hass, freezer):
    """Fixture that tests can use to make fake events."""

    # Setup start time for all tests
    freezer.move_to("2022-04-19 10:31:02+00:00")

    schedule = FakeSchedule(hass, freezer)
    with patch(
        "homeassistant.components.demo.calendar.DemoCalendar.async_get_events",
        new=schedule.async_get_events,
    ):
        yield schedule
>>>>>>> Template platform implementation
