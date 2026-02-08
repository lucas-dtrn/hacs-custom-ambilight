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
from homeassistant.helpers.translation import async_get_translations
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
        self._effect_translations = {}
        self._effect_translation_language = None

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        await self._async_refresh_effect_translations()

    async def _async_refresh_effect_translations(self) -> None:
        """Load effect translations for the active language."""
        if not self.hass:
            return
        language = self.hass.config.language
        if self._effect_translation_language == language and self._effect_translations:
            return
        translations = await async_get_translations(self.hass, language, "entity", {DOMAIN})
        prefix = (
            f"component.{DOMAIN}.entity.light.{self._attr_translation_key}."
            "state_attributes.effect.state."
        )
        self._effect_translations = {
            key[len(prefix):]: value
            for key, value in translations.items()
            if key.startswith(prefix)
        }
        self._effect_translation_language = language

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
        return self.api.get_hs_color()

    @property
    def effect_list(self):
        """Return the list of supported effects."""
        effects = [effect["friendly_name"] for effect in self.api.EFFECTS.values()]
        if not self._effect_translations:
            return effects
        return [self._effect_translations.get(effect, effect) for effect in effects]

    @property
    def effect(self):
        """Return the current effect."""
        effect = self.api.get_effect()
        if effect is None:
            return None
        return self._effect_translations.get(effect, effect)

    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        await self.coordinator.async_refresh()
        await self.api.turn_on(**kwargs)
        await self.coordinator.async_refresh()

    async def async_turn_off(self):
        """Turn the light off."""
        await self.coordinator.async_refresh()
        await self.api.turn_off()
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up Custom Ambilight light based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CustomAmbilightLight(coordinator, entry.entry_id)], update_before_add=True)
