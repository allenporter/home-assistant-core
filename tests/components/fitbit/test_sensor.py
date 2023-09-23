"""Tests for the fitbit sensor platform."""

from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

import pytest
from requests_mock.mocker import Mocker

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant

DEVICES_API_URL = "https://api.fitbit.com/1/user/-/devices.json"


@pytest.fixture(name="devices_response")
async def mock_device_response() -> list[dict[str, Any]]:
    """Return the list of devices."""
    return {}


@pytest.fixture(autouse=True)
async def mock_devices(requests_mock: Mocker, devices_response: dict[str, Any]) -> None:
    """Fixture to setup fake device responses."""
    requests_mock.register_uri(
        "GET",
        DEVICES_API_URL,
        status_code=HTTPStatus.OK,
        json=devices_response,
    )


@pytest.mark.parametrize(
    "devices_response",
    [
        [
            {
                "battery": "Medium",
                "batteryLevel": 60,
                "deviceVersion": "Charge 2",
                "id": "816713257",
                "lastSyncTime": "2019-11-07T12:00:58.000",
                "mac": "16ADD56D54GD",
                "type": "TRACKER",
            },
            {
                "battery": "High",
                "batteryLevel": 95,
                "deviceVersion": "Inspire 3",
                "id": "016713257",
                "lastSyncTime": "2019-11-07T12:00:58.000",
                "mac": "06ADD56D54GD",
                "type": "SCALE",
            },
        ]
    ],
)
async def test_battery_level_sensor(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
) -> None:
    """Test battery level sensor."""
    await integration_setup()

    state = hass.states.get("sensor.charge_2_battery")
    assert state
    assert state.state == "60"
    assert state.attributes.get(ATTR_DEVICE_CLASS) == SensorDeviceClass.BATTERY

    state = hass.states.get("sensor.inspire_3_battery")
    assert state
    assert state.state == "95"
    assert state.attributes.get(ATTR_DEVICE_CLASS) == SensorDeviceClass.BATTERY
