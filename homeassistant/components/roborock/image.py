"""Support for Roborock image."""
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN
from .coordinator import RoborockDataUpdateCoordinator
from .device import RoborockCoordinatedEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roborock time platform."""
    coordinators: dict[str, RoborockDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    async_add_entities(
        MapImage(hass, slugify(device_id), coordinator)
        for device_id, coordinator in coordinators.items()
    )


class MapImage(RoborockCoordinatedEntity, ImageEntity):
    """Roborock vacuum floor plan image."""

    _attr_has_entity_name = True
    _attr_content_type = "image/x-png"
    _attr_icon = "mdi:floor-plan"
    _attr_name = None

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str,
        coordinator: RoborockDataUpdateCoordinator,
    ) -> None:
        """Initialize a vacuum."""
        ImageEntity.__init__(self, hass)
        RoborockCoordinatedEntity.__init__(self, unique_id, coordinator)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
        if self.coordinator.cloud_api is None:
            return None
        map_v1 = await self.coordinator.cloud_api.get_map_v1()
        _LOGGER.debug("Map v1: %s", map_v1[0:500])
        # buf = BytesIO()
        # image.save(buf, format="PNG")
        # return buf.getbuffer()
        return None

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()
