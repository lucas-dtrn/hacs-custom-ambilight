"""Light module for Custom Ambilight integration."""

import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
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

_LOGGER = logging.getLogger(__name__)


class CustomAmbilightLight(CoordinatorEntity, LightEntity):
    """Representation of a Custom Ambilight light."""

    _attr_translation_key = "ambilight"
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id) -> None:
        """Initialize the Custom Ambilight light."""
        super().__init__(coordinator)
        self.api = coordinator.api
        self._attr_supported_features = (
            LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
        )
        self._attr_supported_color_modes = {ColorMode.HS}
        self._attr_color_mode = ColorMode.HS
        self._attr_unique_id = entry_id  # Use the config entry ID as the unique ID
        self._effect_translations = {}
        self._effect_translation_language = None
        self._effect_translation_reverse = {}

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
        self._effect_translation_reverse = {
            value: key for key, value in self._effect_translations.items()
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
        hs_color = self.api.get_hs_color()
        # Always return a valid hs_color tuple to ensure the brightness slider is colored
        # If None is returned (e.g., when light is off), return white (0, 0) as default
        if hs_color is None:
            return (0, 0)
        return hs_color

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

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        effect_icons = {}
        for effect in self.api.EFFECTS.values():
            icon = effect.get("icon")
            if not icon:
                continue
            name = effect["friendly_name"]
            effect_icons[name] = icon
            translated_name = self._effect_translations.get(name)
            if translated_name and translated_name not in effect_icons:
                effect_icons[translated_name] = icon
        if not effect_icons:
            return {}
        return {"effect_icons": effect_icons}

    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        if kwargs.get(ATTR_TRANSITION) is not None:
            kwargs[ATTR_TRANSITION] = float(kwargs[ATTR_TRANSITION])
        if kwargs.get(ATTR_EFFECT):
            await self._async_refresh_effect_translations()
            effect_value = kwargs.get(ATTR_EFFECT)
            normalized_effect = self._effect_translation_reverse.get(
                effect_value, effect_value
            )
            kwargs[ATTR_EFFECT] = normalized_effect
        # Don't refresh before turn_on to avoid recursion if get_data calls turn_on
        try:
            await self.api.turn_on(**kwargs)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to turn on Ambilight light: %s", err)
        # Refresh after to update the state
        await self.coordinator.async_refresh()

    async def async_turn_off(self):
        """Turn the light off."""
        try:
            await self.api.turn_off()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to turn off Ambilight light: %s", err)
        # Refresh after to update the state
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up Custom Ambilight light based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CustomAmbilightLight(coordinator, entry.entry_id)], update_before_add=True)
