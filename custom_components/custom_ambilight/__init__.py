"""Initialisation module for Custom Ambilight integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    CONNECTION_CANNOT_CONNECT,
    CONNECTION_INVALID_AUTH,
    CONNECTION_UNKNOWN,
    MyApi,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Custom Ambilight from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API instance with host, connection type, username, and password from the config entry
    api = MyApi(entry.data["host"], entry.data["type"], entry.data.get("username"), entry.data.get("password"))

    # Validate the API connection
    connection_status = await api.get_connection_status()
    if connection_status == CONNECTION_CANNOT_CONNECT:
        raise ConfigEntryNotReady("Cannot connect to TV JointSpace API")
    if connection_status == CONNECTION_INVALID_AUTH:
        raise ConfigEntryNotReady("Invalid TV API credentials for HTTPS")
    if connection_status == CONNECTION_UNKNOWN:
        raise ConfigEntryNotReady("Unexpected response from TV JointSpace API")

    # Create a data update coordinator
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="custom_ambilight_forked",
        update_method=api.get_data,
        update_interval=timedelta(seconds=30),
    )

    # Fetch initial data (best effort).
    # Some TVs answer /system but may temporarily fail on
    # /ambilight/currentconfiguration right after startup/standby.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        _LOGGER.warning(
            "Initial Ambilight data refresh failed; continuing setup and waiting for next poll"
        )

    # Store the data update coordinator for your platforms to access
    coordinator.api = api
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward the entry setup to the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


#
