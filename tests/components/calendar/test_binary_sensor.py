"""Tests for calendar event binary sensor."""

from __future__ import annotations

import datetime
import logging

import pytest

from homeassistant.components.calendar import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import FakeSchedule

from tests.common import MockConfigEntry

_LOGGER = logging.getLogger(__name__)

CALENDAR_ENTITY_ID = "calendar.calendar_2"
CONFIG = {DOMAIN: {"platform": "demo"}}


@pytest.fixture(autouse=True)
async def setup_freezer(freezer):
    """Set fake times used during tests."""
    freezer.move_to("2022-08-01 10:31:02+00:00")


@pytest.fixture(autouse=True)
def set_time_zone(hass):
    """Set the time zone for the tests."""
    hass.config.set_time_zone("America/Regina")  # UTC-6


@pytest.fixture(autouse=True)
async def setup_calendar(hass: HomeAssistant, fake_schedule: FakeSchedule) -> None:
    """Initialize the demo calendar."""
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()


async def test_all_day(hass, fake_schedule):
    """Test binary sensor matching an all day event."""
    state = hass.states.get(CALENDAR_ENTITY_ID)
    assert state

    fake_schedule.create_event(
        summary="Trash day",
        start=datetime.date.fromisoformat("2022-08-08"),
        end=datetime.date.fromisoformat("2022-08-09"),
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "entity_id": CALENDAR_ENTITY_ID,
            "search": "Trash",
            "name": "Trash Day",
        },
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("binary_sensor.trash_day")
    assert state
    assert state.name == "Trash day"
    assert state.state == STATE_OFF

    assert await fake_schedule.fire_state_changes(
        "binary_sensor.trash_day",
        [
            datetime.datetime.fromisoformat("2022-08-07 23:59:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 00:00:01-06:00"),
            datetime.datetime.fromisoformat("2022-08-09 00:00:01-06:00"),
        ],
    ) == [
        STATE_OFF,
        STATE_ON,
        STATE_OFF,
    ]


async def test_multiple_all_day(hass, fake_schedule):
    """Test multiple events, some matching and some not, across a few days."""
    state = hass.states.get(CALENDAR_ENTITY_ID)
    assert state

    fake_schedule.create_event(
        summary="Trash day",
        start=datetime.date.fromisoformat("2022-08-08"),
        end=datetime.date.fromisoformat("2022-08-09"),
    )
    fake_schedule.create_event(
        summary="Vacation",
        start=datetime.date.fromisoformat("2022-08-12"),
        end=datetime.date.fromisoformat("2022-08-13"),
    )
    fake_schedule.create_event(
        summary="Trash day",
        start=datetime.date.fromisoformat("2022-08-15"),
        end=datetime.date.fromisoformat("2022-08-16"),
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "entity_id": CALENDAR_ENTITY_ID,
            "search": "Trash",
            "name": "Trash Day",
        },
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("binary_sensor.trash_day")
    assert state
    assert state.name == "Trash day"
    assert state.state == STATE_OFF

    assert await fake_schedule.fire_state_changes(
        "binary_sensor.trash_day",
        [
            datetime.datetime.fromisoformat("2022-08-08 00:01:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-09 00:00:01-06:00"),
            datetime.datetime.fromisoformat("2022-08-12 00:01:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-15 00:01:00-06:00"),
        ],
    ) == [
        STATE_ON,
        STATE_OFF,
        STATE_OFF,
        STATE_ON,
    ]


async def test_overlapping_events(hass, fake_schedule):
    """Test matching events that match and overlap."""
    state = hass.states.get(CALENDAR_ENTITY_ID)
    assert state

    fake_schedule.create_event(
        summary="Front light",
        start=datetime.datetime.fromisoformat("2022-08-08 18:30:00"),
        end=datetime.datetime.fromisoformat("2022-08-08 23:00:00"),
    )
    fake_schedule.create_event(
        summary="Back light",
        start=datetime.datetime.fromisoformat("2022-08-08 20:30:00"),
        end=datetime.datetime.fromisoformat("2022-08-08 22:00:00"),
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "entity_id": CALENDAR_ENTITY_ID,
            "search": "light",
            "name": "Exterior lights",
        },
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("binary_sensor.exterior_lights")
    assert state
    assert state.name == "Exterior lights"
    assert state.state == STATE_OFF

    assert await fake_schedule.fire_state_changes(
        "binary_sensor.exterior_lights",
        [
            datetime.datetime.fromisoformat("2022-08-08 18:29:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 18:31:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 20:31:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 22:01:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 23:01:00-06:00"),
        ],
    ) == [
        STATE_OFF,
        STATE_ON,
        STATE_ON,
        STATE_ON,  # Event 2 ends but Event 1 is still active
        STATE_OFF,
    ]


async def test_active_at_start(hass, fake_schedule):
    """Test an even that is active when home assistant starts."""
    state = hass.states.get(CALENDAR_ENTITY_ID)
    assert state

    fake_schedule.create_event(
        summary="Vacation 1",
        start=datetime.date.fromisoformat("2022-07-30"),
        end=datetime.date.fromisoformat("2022-08-07"),
    )
    fake_schedule.create_event(
        summary="Vacation 2",
        start=datetime.date.fromisoformat("2022-08-09"),
        end=datetime.date.fromisoformat("2022-08-10"),
    )

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "entity_id": CALENDAR_ENTITY_ID,
            "search": "vacation",
            "name": "Vacation",
        },
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("binary_sensor.vacation")
    assert state
    assert state.name == "Vacation"
    assert state.state == STATE_ON

    assert await fake_schedule.fire_state_changes(
        "binary_sensor.vacation",
        [
            datetime.datetime.fromisoformat("2022-08-06 23:00:00-06:00"),
            datetime.datetime.fromisoformat("2022-08-07 00:00:01-06:00"),
            datetime.datetime.fromisoformat("2022-08-08 00:00:01-06:00"),
            datetime.datetime.fromisoformat("2022-08-09 00:00:01-06:00"),
            datetime.datetime.fromisoformat("2022-08-10 00:00:01-06:00"),
        ],
    ) == [
        STATE_ON,
        STATE_OFF,
        STATE_OFF,
        STATE_ON,
        STATE_OFF,
    ]
