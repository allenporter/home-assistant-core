"""Test the RTSPtoWebRTC config flow."""

from unittest.mock import patch

import rtsp_to_webrtc

from homeassistant import config_entries, setup
from homeassistant.components.rtsptowebrtc import DOMAIN
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

CONFIG = {}


async def test_web_full_flow(hass: HomeAssistant):
    """Check full flow."""
    assert await setup.async_setup_component(hass, DOMAIN, CONFIG)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert result.get("data_schema").schema.get("server_url") == str
    assert not result.get("errors")
    assert "flow_id" in result
    with patch("rtsp_to_webrtc.client.Client.heartbeat"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"server_url": "https://example.com"}
        )
        assert result.get("type") == "create_entry"
        assert "result" in result
        assert result["result"].data == {"server_url": "https://example.com"}


async def test_single_config_entry(hass):
    """Test that only a single config entry is allowed."""
    old_entry = MockConfigEntry(domain=DOMAIN, data={"example": True})
    old_entry.add_to_hass(hass)

    assert await setup.async_setup_component(hass, DOMAIN, CONFIG)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "abort"
    assert result.get("reason") == "single_instance_allowed"


async def test_invalid_url(hass: HomeAssistant):
    """Check full flow."""
    assert await setup.async_setup_component(hass, DOMAIN, CONFIG)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert result.get("data_schema").schema.get("server_url") == str
    assert not result.get("errors")
    assert "flow_id" in result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"server_url": "not-a-url"}
    )

    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert result.get("errors") == {"server_url": "invalid_url"}


async def test_server_unreachable(hass: HomeAssistant):
    """Exercise case where the server is unreachable."""
    assert await setup.async_setup_component(hass, DOMAIN, CONFIG)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert not result.get("errors")
    assert "flow_id" in result
    with patch(
        "rtsp_to_webrtc.client.Client.heartbeat",
        side_effect=rtsp_to_webrtc.exceptions.ClientError(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"server_url": "https://example.com"}
        )
        assert result.get("type") == "form"
        assert result.get("step_id") == "user"
        assert result.get("errors") == {"base": "server_unreachable"}


async def test_server_failure(hass: HomeAssistant):
    """Exercise case where server returns a failure."""
    assert await setup.async_setup_component(hass, DOMAIN, CONFIG)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result.get("type") == "form"
    assert result.get("step_id") == "user"
    assert not result.get("errors")
    assert "flow_id" in result
    with patch(
        "rtsp_to_webrtc.client.Client.heartbeat",
        side_effect=rtsp_to_webrtc.exceptions.ResponseError(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"server_url": "https://example.com"}
        )
        assert result.get("type") == "form"
        assert result.get("step_id") == "user"
        assert result.get("errors") == {"base": "server_failure"}
