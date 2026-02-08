"""Light module for Custom Ambilight integration."""

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class CustomAmbilightLight(CoordinatorEntity, LightEntity):
    """Representation of a Custom Ambilight light."""

    _attr_translation_key = "ambilight"
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id) -> None:
        """Initialize the Custom Ambilight light."""
        super().__init__(coordinator)
        self.api = coordinator.api
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_supported_color_modes = {ColorMode.HS}
        self._attr_color_mode = ColorMode.HS
        self._attr_unique_id = entry_id  # Use the config entry ID as the unique ID

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name="Philips Ambilight",
            manufacturer="Philips",
            model="Ambilight",
            sw_version="1.0",
        )

    @property
    def is_on(self):
        """Return true if the light is on."""
        return self.api.get_is_on()

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self.api.get_brightness()

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        hs_color = self.api.get_hs_color()
        # Always return a valid hs_color tuple to ensure the brightness slider is colored
        # If None is returned (e.g., when light is off), return white (0, 0) as default
        if hs_color is None:
            return (0, 0)
        return hs_color

    @property
    def effect_list(self):
        """Return the list of supported effects."""
        return [effect["friendly_name"] for effect in self.api.EFFECTS.values()]

    @property
    def effect(self):
        """Return the current effect."""
        return self.api.get_effect()

    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        # Don't refresh before turn_on to avoid recursion if get_data calls turn_on
        await self.api.turn_on(**kwargs)
        # Refresh after to update the state
        await self.coordinator.async_refresh()

    async def async_turn_off(self):
        """Turn the light off."""
        await self.api.turn_off()
        # Refresh after to update the state
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up Custom Ambilight light based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CustomAmbilightLight(coordinator, entry.entry_id)], update_before_add=True)
