"""Tests for init for manual."""

from unittest.mock import patch

from homeassistant.components.manual import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

CODE = "1234"


async def test_load_unload(hass: HomeAssistant) -> None:
    """Test loading and unloading a config entry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "name": "test",
            "code": CODE,
            "code_arm_required": False,
            "arming_time": 30,
            "delay_time": 45,
            "trigger_time": 300,
        },
    )
    config_entry.add_to_hass(hass)

    with patch("homeassistant.components.manual.PLATFORMS", []):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
