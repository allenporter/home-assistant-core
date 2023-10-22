"""Test Roborock Image platform."""
from http import HTTPStatus

import pytest

from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry
from tests.typing import ClientSessionGenerator


@pytest.mark.freeze_time("2023-10-20 00:00:00+00:00")
async def test_floorplan_image(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test floor plan map image is correctly set up."""

    # assert len(hass.states.async_all("image")) == 2

    state = hass.states.get("image.roborock_s7_maxv")
    assert state.state == "2023-10-20T00:00:00+00:00"

    client = await hass_client()
    resp = await client.get("/api/image_proxy/image.roborock_s7_maxv")
    assert resp.status == HTTPStatus.OK
    body = await resp.read()
    assert body is not None
