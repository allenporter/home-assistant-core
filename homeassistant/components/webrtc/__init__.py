"""WebRTC integration with an external RTSPToWebRTC Server.

WebRTC uses a direct communication from the client (e.g. a web browser) to a
camera device. Home Assistant acts as the signal path for initial set up,
passing through the client offer and returning a camera answer, then the client
and camera communicate directly.

However, not all cameras natively support WebRTC. This integration is a shim
for camera devices that support RTSP streams only, relying on an external
server RTSPToWebRTC that is a proxy. Home Assistant does not participate in
the offer/answer SDP protocol, other than as a signal path pass through.

Other integrations may use this webrtc integration with these steps:
- Check if this integration is loaded
- Call is_suported_stream_source for compatibility
- Call async_offer_for_stream_source to get back an answer for a client offer
"""

from __future__ import annotations

import logging

import async_timeout
from rtsp_to_webrtc.client import Client
from rtsp_to_webrtc.exceptions import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DATA_RTSP_TO_WEBRTC_URL

_LOGGER = logging.getLogger(__name__)

DOMAIN = "webrtc"
TIMEOUT = 10
RTSP_PREFIXES = {"rtsp://", "rtsps://"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WebRTC from a config entry."""

    if DATA_RTSP_TO_WEBRTC_URL not in entry.data:
        _LOGGER.error(
            "Invalid ConfigEntry for webrtc, missing '%s'", DATA_RTSP_TO_WEBRTC_URL
        )
        return False

    hass.data[DOMAIN] = Client(
        async_get_clientsession(hass), entry.data[DATA_RTSP_TO_WEBRTC_URL]
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    del hass.data[DOMAIN]
    return True


def is_suported_stream_source(stream_source: str) -> bool:
    """Return True if the stream source is supported by this component."""
    for prefix in RTSP_PREFIXES:
        if stream_source.startswith(prefix):
            return True
    return False


async def async_offer_for_stream_source(
    hass: HomeAssistant, offer_sdp: str, stream_source: str
) -> str:
    """Handle the signal path for a WebRTC stream.

    This signal path is used to route the offer created by the client to the
    a proxy server that translates a stream to WebRTC. The communication for
    the stream itself happens directly between the client and proxy.
    """
    if DOMAIN not in hass.config.components:
        raise HomeAssistantError("webrtc integration is not set up.")
    client: Client = hass.data[DOMAIN]
    try:
        async with async_timeout.timeout(TIMEOUT):
            return await client.offer(offer_sdp, stream_source)
    except TimeoutError as err:
        raise HomeAssistantError("Timeout talking to RTSPtoWebRTC server") from err
    except ClientError as err:
        raise HomeAssistantError(str(err)) from err
