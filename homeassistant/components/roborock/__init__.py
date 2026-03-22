"""The Roborock component."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from roborock import (
    RoborockException,
    RoborockInvalidCredentials,
    RoborockInvalidUserAgreement,
    RoborockNoUserAgreement,
)
from roborock.data import UserData
from roborock.devices.device import RoborockDevice
from roborock.devices.device_manager import UserParams, create_device_manager
from roborock.map.map_parser import MapParserConfig
from roborock.mqtt.session import MqttSessionUnauthorized

from homeassistant.const import CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BASE_URL,
    CONF_SHOW_BACKGROUND,
    CONF_USER_DATA,
    DEFAULT_DRAWABLES,
    DOMAIN,
    DRAWABLES,
    MAP_SCALE,
    PLATFORMS,
)
from .coordinator import (
    DeviceDispatcher,
    RoborockB01Q7UpdateCoordinator,
    RoborockB01Q10UpdateCoordinator,
    RoborockConfigEntry,
    RoborockCoordinators,
    RoborockDataUpdateCoordinator,
    RoborockDataUpdateCoordinatorA01,
    RoborockDataUpdateCoordinatorB01,
    RoborockWashingMachineUpdateCoordinator,
    RoborockWetDryVacUpdateCoordinator,
)
from .models import get_device_info
from .roborock_storage import CacheStore, async_cleanup_map_storage
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SCAN_INTERVAL = timedelta(seconds=30)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RoborockConfigEntry) -> bool:
    """Set up roborock from a config entry."""
    await async_cleanup_map_storage(hass, entry.entry_id)

    user_data = UserData.from_dict(entry.data[CONF_USER_DATA])
    user_params = UserParams(
        username=entry.data[CONF_USERNAME],
        user_data=user_data,
        base_url=entry.data[CONF_BASE_URL],
    )
    entry.runtime_data = RoborockCoordinators()
    device_listener = DeviceListener(hass, entry)
    cache = CacheStore(hass, entry.entry_id)
    try:
        device_manager = await create_device_manager(
            user_params,
            cache=cache,
            session=async_get_clientsession(hass),
            map_parser_config=MapParserConfig(
                drawables=[
                    drawable
                    for drawable, default_value in DEFAULT_DRAWABLES.items()
                    if entry.options.get(DRAWABLES, {}).get(drawable, default_value)
                ],
                show_background=entry.options.get(CONF_SHOW_BACKGROUND, False),
                map_scale=MAP_SCALE,
            ),
            mqtt_session_unauthorized_hook=lambda: entry.async_start_reauth(hass),
            prefer_cache=False,
            ready_callback=device_listener.device_ready_callback,
        )
    except RoborockInvalidCredentials as err:
        raise ConfigEntryAuthFailed(
            "Invalid credentials",
            translation_domain=DOMAIN,
            translation_key="invalid_credentials",
        ) from err
    except RoborockInvalidUserAgreement as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_user_agreement",
        ) from err
    except RoborockNoUserAgreement as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="no_user_agreement",
        ) from err
    except MqttSessionUnauthorized as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="mqtt_unauthorized",
        ) from err
    except RoborockException as err:
        _LOGGER.debug("Failed to get Roborock home data: %s", err)
        raise ConfigEntryNotReady(
            "Failed to get Roborock home data",
            translation_domain=DOMAIN,
            translation_key="home_data_fail",
        ) from err

    entry.runtime_data.device_manager = device_manager

    async def shutdown_roborock(_: Event | None = None) -> None:
        await asyncio.gather(device_manager.close(), cache.flush())

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown_roborock)
    )
    entry.async_on_unload(shutdown_roborock)

    devices = await device_manager.get_devices()
    _LOGGER.debug("Device manager found %d devices", len(devices))

    # Register all discovered devices in the device registry so we can
    # check the disabled state before creating coordinators.
    device_registry = dr.async_get(hass)
    for device in devices:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **get_device_info(device),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _remove_stale_devices(hass, entry, devices)

    return True


def _remove_stale_devices(
    hass: HomeAssistant,
    entry: RoborockConfigEntry,
    devices: list[RoborockDevice],
) -> None:
    device_map: dict[str, RoborockDevice] = {device.duid: device for device in devices}
    device_registry = dr.async_get(hass)
    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry_id=entry.entry_id
    )
    for device in device_entries:
        # Remove any devices that are no longer in the account.
        # The API returns all devices, even if they are offline
        device_duids = {
            identifier[1].replace("_dock", "") for identifier in device.identifiers
        }
        if any(device_duid in device_map for device_duid in device_duids):
            continue
        _LOGGER.info(
            "Removing device: %s because it is no longer exists in your account",
            device.name,
        )
        device_registry.async_update_device(
            device_id=device.id,
            remove_config_entry_id=entry.entry_id,
        )


async def async_migrate_entry(hass: HomeAssistant, entry: RoborockConfigEntry) -> bool:
    """Migrate old configuration entries to the new format."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        entry.version,
        entry.minor_version,
    )
    if entry.version > 1:
        # Downgrade from future version
        return False

    # 1->2: Migrate from unique id as email address to unique id as rruid
    if entry.minor_version == 1:
        user_data = UserData.from_dict(entry.data[CONF_USER_DATA])
        _LOGGER.debug("Updating unique id to %s", user_data.rruid)
        hass.config_entries.async_update_entry(
            entry,
            unique_id=user_data.rruid,
            version=1,
            minor_version=2,
        )

    return True


class DeviceListener:
    """Listener for device ready events.

    This will listen for the device connection to be made available
    """

    def __init__(self, hass: HomeAssistant, entry: RoborockConfigEntry) -> None:
        """Initialize the DeviceListener."""
        self._hass = hass
        self._entry = entry
        self._tasks: list[asyncio.Task] = []
        self._entry.async_on_unload(self.shutdown)

    def device_ready_callback(self, device: RoborockDevice) -> None:
        """Handle a device becoming ready by creating a coordinator."""
        device_registry = dr.async_get(self._hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, device.duid)}
        )
        if device_entry is not None and device_entry.disabled:
            return

        coord: DataUpdateCoordinator[Any] | None
        dispatcher: DeviceDispatcher | None
        coord, dispatcher = self._build_coordinator(device)
        if coord is None or dispatcher is None:
            return
        # Set up the coordinator
        # XXX: This doesn't work because all the entities expect data to be
        # available before the entity is created.
        dispatcher.add_coordinator(device.duid, coord)
        self._tasks.append(
            self._entry.async_create_task(
                self._hass, coord.async_config_entry_first_refresh()
            )
        )

    async def shutdown(self) -> None:
        """Cancel all pending tasks."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _build_coordinator(
        self, device: RoborockDevice
    ) -> tuple[
        RoborockDataUpdateCoordinator
        | RoborockDataUpdateCoordinatorA01
        | RoborockDataUpdateCoordinatorB01
        | RoborockB01Q10UpdateCoordinator
        | None,
        DeviceDispatcher | None,
    ]:
        """Create a coordinator for the device and return the appropriate coordinator/listener."""
        coordinators = self._entry.runtime_data
        coord: (
            RoborockDataUpdateCoordinator
            | RoborockDataUpdateCoordinatorA01
            | RoborockDataUpdateCoordinatorB01
            | RoborockB01Q10UpdateCoordinator
        )
        if device.v1_properties is not None:
            coord = RoborockDataUpdateCoordinator(
                self._hass, self._entry, device, device.v1_properties
            )
            return coord, coordinators.v1_dispatcher
        if device.dyad is not None:
            coord = RoborockWetDryVacUpdateCoordinator(
                self._hass, self._entry, device, device.dyad
            )
            return coord, coordinators.a01_dispatcher
        if device.zeo is not None:
            coord = RoborockWashingMachineUpdateCoordinator(
                self._hass, self._entry, device, device.zeo
            )
            return coord, coordinators.a01_dispatcher
        if device.b01_q7_properties is not None:
            coord = RoborockB01Q7UpdateCoordinator(
                self._hass, self._entry, device, device.b01_q7_properties
            )
            return coord, coordinators.b01_q7_dispatcher
        if device.b01_q10_properties is not None:
            coord = RoborockB01Q10UpdateCoordinator(
                self._hass, self._entry, device, device.b01_q10_properties
            )
            return coord, coordinators.b01_q10_dispatcher
        _LOGGER.info(
            "Not adding device %s (duid=%s) because its protocol version %s or category %s is not supported",
            device.name,
            device.duid,
            device.device_info.pv,
            device.product.category.name,
        )
        return None, None


async def async_unload_entry(hass: HomeAssistant, entry: RoborockConfigEntry) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: RoborockConfigEntry) -> None:
    """Handle removal of an entry."""
    store = CacheStore(hass, entry.entry_id)
    await store.async_remove()
