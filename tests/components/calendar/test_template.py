"""Tests for calendar template platform."""
from __future__ import annotations

import datetime

import pytest

from homeassistant.components import calendar
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import FakeSchedule

CONFIG = {calendar.DOMAIN: {"platform": "demo"}}


@pytest.fixture(autouse=True)
async def setup_calendar(hass: HomeAssistant, fake_schedule: FakeSchedule) -> None:
    """Initialize the demo calendar."""
    assert await async_setup_component(hass, calendar.DOMAIN, CONFIG)
    await hass.async_block_till_done()


async def test_baseline_template(hass: HomeAssistant, fake_schedule: FakeSchedule):
    """Test the a calendar trigger based on start time."""
    fake_schedule.create_event(
        start=datetime.datetime.fromisoformat("2022-04-19 11:00:00+00:00"),
        end=datetime.datetime.fromisoformat("2022-04-19 11:30:00+00:00"),
    )

    assert await async_setup_component(
        hass,
        "template",
        {
            "template": {
                "sensor": {
                    "state": "{{ 1 + 1}}",
                    "name": "test_sensor",
                },
            }
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_sensor")
    assert state.state == "2"


async def test_call(hass: HomeAssistant, fake_schedule: FakeSchedule):
    """Test the a calendar trigger based on start time."""
    assert await async_setup_component(
        hass,
        "template",
        {
            "template": {
                "sensor": {
                    "state": "{{ calendar.example_call() }}",
                    "name": "test_sensor",
                },
            }
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_sensor")
    assert state.state == "3"


async def test_get_events(hass: HomeAssistant, fake_schedule: FakeSchedule, freezer):
    """Test the a calendar trigger based on start time."""
    freezer.move_to("2022-04-19 10:00:00+00:00")
    fake_schedule.create_event(
        start=datetime.datetime.fromisoformat("2022-04-19 11:00:00+00:00"),
        end=datetime.datetime.fromisoformat("2022-04-19 11:30:00+00:00"),
    )

    assert await async_setup_component(
        hass,
        "template",
        {
            "template": {
                "sensor": {
                    "state": "{{ calendar.get_events('calendar.calendar_1', utcnow(), utcnow() + timedelta(hours=2)) }}",
                    "name": "test_sensor",
                },
            }
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_sensor")
    assert state.state == "3"
