"""Helpers to host client metadata."""

from __future__ import annotations

from http import HTTPStatus

from aiohttp import web

from homeassistant.components import http
from homeassistant.helpers.network import NoURLAvailableError, get_url

# We support draft-ietf-oauth-client-id-metadata-document-00 for
# OAuth2 Client Metadata which is compatible with IndieAuth
# Clients can use https://<home_assistant_url>/auth/client-metadata.json
# as their client_id.
CLIENT_METADATA_PATH = "/auth/client-metadata.json"


class ClientMetadataView(http.HomeAssistantView):
    """View to host the OAuth2 client information."""

    requires_auth = False
    url = CLIENT_METADATA_PATH
    name = "oauth-metadata-json"

    async def get(self, request: web.Request) -> web.Response:
        """Return the well known OAuth2 authorization info."""
        hass = request.app[http.KEY_HASS]
        try:
            url_prefix = get_url(hass, require_current_request=True)
        except NoURLAvailableError:
            return self.json_message(
                message="Unable to determine base URL", status_code=HTTPStatus.FORBIDDEN
            )
        return self.json(
            {
                "client_id": f"{url_prefix}{CLIENT_METADATA_PATH}",
                "client_name": "Home Assistant",
                "client_uri": url_prefix,
                "redirect_uris": [f"{url_prefix}/auth/authorize/callback"],
            }
        )
