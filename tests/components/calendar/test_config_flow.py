"""Tests the calendar integration config flow."""

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.components.calendar.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


@pytest.mark.parametrize("platform", ("calendar",))
async def test_config_flow(hass: HomeAssistant, platform) -> None:
    """Test the config flow."""
    input_sensor_entity_id = "calendar.demo"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] is None

    with patch(
        "homeassistant.components.calendar.async_setup_entry",
        return_value=True,
    ) as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Trash Day",
                "search": "Trash",
                "entity_id": input_sensor_entity_id,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Trash Day"
    assert result["data"] == {}
    assert result["options"] == {
        "name": "Trash Day",
        "search": "Trash",
        "entity_id": "calendar.demo",
    }
    assert len(mock_setup_entry.mock_calls) == 1

    config_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert config_entry.data == {}
    assert config_entry.options == {
        "name": "Trash Day",
        "search": "Trash",
        "entity_id": "calendar.demo",
    }
    assert config_entry.title == "Trash Day"
