"""Test fixtures for calendar component."""
from __future__ import annotations

import datetime
import secrets
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.components import calendar
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
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
        summary: str | None = None,
        description: str = None,
        location: str = None,
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


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant):
    """Set up the homeassistant integration."""
    await async_setup_component(hass, "homeassistant", {})
