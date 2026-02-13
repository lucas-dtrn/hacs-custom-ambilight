"""API module for Custom Ambilight integration."""

import asyncio
import colorsys
from base64 import b64decode
from json import JSONDecodeError
import logging
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
import httpx

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from .effects import EFFECTS

_LOGGER = logging.getLogger(__name__)
# Define the rate limit (in seconds)
RATE_LIMIT = 0.1
CONNECTION_OK = "ok"
CONNECTION_CANNOT_CONNECT = "cannot_connect"
CONNECTION_INVALID_AUTH = "invalid_auth"
CONNECTION_UNKNOWN = "unknown"


class MyApi:
    """The Custom Ambilight API."""

    def __init__(self, host: str, connection_type: str, username: str = None, password: str = None) -> None:
        """Initialise the API."""
        self.host = host
        self.connection_type = connection_type
        self.username = username
        self.password = password
        self.url = f"{connection_type}://{host}:1926/6" if connection_type == "https" else f"http://{host}:1925/6"
        self.client = httpx.AsyncClient(
            auth=httpx.DigestAuth(username, password) if connection_type == "https" else None, verify=False
        )
        self.EFFECTS = EFFECTS
        self.previous_state = None
        self._data = {}
        self._turn_on_in_progress = False

    @staticmethod
    def _truncate_text(value: str, max_len: int = 200) -> str:
        """Return a single-line preview for logging."""
        text = value.replace("\n", " ").replace("\r", " ").strip()
        if len(text) > max_len:
            return f"{text[:max_len]}..."
        return text

    @staticmethod
    def _response_preview(response: httpx.Response, max_len: int = 200) -> str:
        """Return a short response body preview for diagnostics."""
        text = response.text if response.content else ""
        return MyApi._truncate_text(text, max_len=max_len)

    async def get_data(self) -> Any:
        """Fetch data from the API."""
        endpoint = f"{self.url}/ambilight/currentconfiguration"
        try:
            response = await self.client.get(endpoint)
            await asyncio.sleep(RATE_LIMIT)
            response.raise_for_status()

            if not response.content:
                if self._data:
                    _LOGGER.warning(
                        "Received empty response from %s (status=%s, content_type=%s); using previous state",
                        endpoint,
                        response.status_code,
                        response.headers.get("content-type"),
                    )
                    return self._data
                raise UpdateFailed("Received empty response from Ambilight API")

            try:
                self._data = response.json()
            except JSONDecodeError as err:
                _LOGGER.warning(
                    "Invalid JSON from %s (status=%s, content_type=%s, body_len=%s, body_preview=%r): %s",
                    endpoint,
                    response.status_code,
                    response.headers.get("content-type"),
                    len(response.content),
                    self._response_preview(response),
                    err,
                )
                raise
        except (httpx.HTTPError, JSONDecodeError) as err:
            if self._data:
                _LOGGER.warning(
                    "Failed to fetch Ambilight data from %s (%s); using previous state",
                    endpoint,
                    err,
                )
                return self._data
            raise UpdateFailed("Unable to fetch valid Ambilight data") from err

        # Check if the response matches the glitch state
        glitch_state = {
            "styleName": "Lounge light",
            "isExpert": True,
            "colorSettings": {
                "color": {"hue": 0, "saturation": 0, "brightness": 0},
                "colorDelta": {"hue": 0, "saturation": 0, "brightness": 0},
                "speed": 255,
                "algorithm": "MANUAL_HUE",
            },
        }
        if self._data == glitch_state:
            # Don't restore state if turn_on is already in progress to avoid recursion
            if self._turn_on_in_progress:
                return self._data
            # If it does, save the previous state
            self.previous_state = {
                "brightness": self.get_brightness(),
                "hs_color": self.get_hs_color(),
                "effect": self.get_effect(),
            }
            # Reset the connection
            await self.client.aclose()
            self.client = httpx.AsyncClient(
                auth=httpx.DigestAuth(self.username, self.password), verify=False
            )
            # Restore the previous state
            if any(
                key in self.previous_state
                for key in [ATTR_BRIGHTNESS, ATTR_HS_COLOR, ATTR_EFFECT]
            ):
                await self.turn_on(**self.previous_state)
            else:
                await self.turn_off()

        return self._data

    async def send_data(self, endpoint: str, data: Any) -> int:
        """Send data to the API."""
        url = f"{self.url}/{endpoint}"
        response = await self.client.post(url, json=data)
        # Sleep for the rate limit duration
        await asyncio.sleep(RATE_LIMIT)
        return response.status_code

    @staticmethod
    def _build_color_data(hue: int, saturation: int, brightness: int) -> dict[str, Any]:
        """Build payload for manual lounge color mode."""
        return {
            "color": {
                "hue": hue,
                "saturation": saturation,
                "brightness": brightness,
            },
            "colorDelta": {"hue": 0, "saturation": 0, "brightness": 0},
            "speed": 255,
            "algorithm": "MANUAL_HUE",
        }

    @staticmethod
    def _interpolate_hue_shortest_path(
        start_hue: float, end_hue: float, progress: float
    ) -> float:
        """Interpolate hue on the shortest path on the hue circle."""
        start = start_hue % 360.0
        end = end_hue % 360.0
        delta = ((end - start + 180.0) % 360.0) - 180.0
        return (start + delta * progress) % 360.0

    def cbc_decode(self, key: bytes, data: str):
        """Decode encrypted fields based on shared key."""
        if data == "":
            return ""
        raw = b64decode(data)
        assert len(raw) >= 16, f"Length of data too short: '{data}'"
        decryptor = Cipher(algorithms.AES(key[0:16]), modes.CBC(raw[0:16])).decryptor()
        unpadder = PKCS7(128).unpadder()
        result = decryptor.update(raw[16:]) + decryptor.finalize()
        result = unpadder.update(result) + unpadder.finalize()
        return result.decode("utf-8")

    async def get_connection_status(self) -> str:
        """Validate connectivity/auth and return a status key."""
        if self.connection_type == "https" and (
            not self.username or not self.password
        ):
            return CONNECTION_INVALID_AUTH

        try:
            system_endpoint = f"{self.url}/system"
            response = await self.client.get(system_endpoint)
        except (httpx.ConnectError, httpx.TimeoutException) as err:
            _LOGGER.warning("Cannot connect to TV at %s: %s", self.url, err)
            return CONNECTION_CANNOT_CONNECT
        except httpx.HTTPError as err:
            _LOGGER.warning("HTTP error while connecting to %s: %s", self.url, err)
            return CONNECTION_CANNOT_CONNECT

        if response.status_code in (401, 403):
            _LOGGER.warning(
                "Authentication rejected by %s/system (status=%s)",
                self.url,
                response.status_code,
            )
            return CONNECTION_INVALID_AUTH

        if response.status_code != 200:
            _LOGGER.warning(
                "Unexpected status from %s/system: %s (content_type=%s, body_len=%s, body_preview=%r)",
                self.url,
                response.status_code,
                response.headers.get("content-type"),
                len(response.content),
                self._response_preview(response),
            )
            return CONNECTION_UNKNOWN

        try:
            data = response.json()
        except JSONDecodeError as err:
            _LOGGER.warning(
                "Failed to parse /system response from %s (content_type=%s, body_len=%s, body_preview=%r): %s",
                self.url,
                response.headers.get("content-type"),
                len(response.content),
                self._response_preview(response),
                err,
            )
            return CONNECTION_UNKNOWN

        # Decode encrypted fields when available (best effort).
        key = b64decode(
            "ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA=="
        )
        for k, encrypted_value in data.items():
            if k.endswith("_encrypted"):
                decrypted_key = k.replace("_encrypted", "")
                try:
                    decrypted_value = self.cbc_decode(key, encrypted_value.strip())
                    setattr(self, decrypted_key, decrypted_value)
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.debug(
                        "Could not decode field %s from /system response: %s",
                        k,
                        err,
                    )
            elif k == "name":
                setattr(self, k, encrypted_value)

        return CONNECTION_OK

    async def validate_connection(self) -> bool:
        """Backward compatible connectivity check."""
        return (await self.get_connection_status()) == CONNECTION_OK

    def get_is_on(self):
        """Get the current power status from the data."""
        return self._data.get("styleName") != "OFF"

    def get_brightness(self):
        """Get the current brightness from the data."""
        # If the light is in normal hs color mode
        if (
            self._data.get("styleName") == "Lounge light"
            and self._data.get("isExpert") == True
        ):
            # Get the brightness value
            brightness = (
                self._data.get("colorSettings", {}).get("color", {}).get("brightness")
            )
            return brightness
        else:
            # If the light is not in normal hs color mode, return None
            return None

    def get_hs_color(self):
        """Get the current color from the data."""
        # If the light is in normal hs color mode
        if (
            self._data.get("styleName") == "Lounge light"
            and self._data.get("isExpert") == True
        ):
            # Get the hue and saturation values
            hue = self._data.get("colorSettings", {}).get("color", {}).get("hue")
            saturation = (
                self._data.get("colorSettings", {}).get("color", {}).get("saturation")
            )

            # Convert hue and saturation to the correct ranges and round to 0 decimal places
            if hue is not None:
                hue = round((hue / 255) * 360)
            if saturation is not None:
                saturation = round((saturation / 255) * 100)

            # Return the hs color as a tuple
            return (hue, saturation)
        else:
            # If the light is not in normal hs color mode, try to return the previous state color
            # This ensures the brightness slider can be colored even when an effect is active
            if self.previous_state and self.previous_state.get("hs_color") is not None:
                return self.previous_state.get("hs_color")
            # If no previous state is available, return None
            return None

    def get_effect(self):
        """Get the current effect from the data."""
        # If the light is in effect mode or isExpert is False
        if (
            self._data.get("styleName") != "Lounge light"
            and self._data.get("styleName") != "OFF"
        ) or not self._data.get("isExpert"):
            # Get the menuSetting value
            menu_setting = self._data.get("menuSetting")
            # Return the friendly name for the effect, or the original value if no friendly name is defined
            return self.EFFECTS.get(menu_setting, {"friendly_name": menu_setting})[
                "friendly_name"
            ]
        else:
            # If the light is not in effect mode and isExpert is True, return None
            return None

    async def turn_on(self, **kwargs):
        """Turn the light on."""
        # Prevent recursive calls
        if self._turn_on_in_progress:
            return
        self._turn_on_in_progress = True
        
        try:
            # Get the current brightness, hue, and saturation
            current_brightness = self.get_brightness()
            current_hs_color = self.get_hs_color()

            # Check if brightness or color is in kwargs
            if kwargs.get(ATTR_BRIGHTNESS) is not None or kwargs.get(ATTR_HS_COLOR) is not None:
                transition = kwargs.get(ATTR_TRANSITION)
                transition_seconds = float(transition) if transition is not None else 0.0
                transition_seconds = max(0.0, transition_seconds)

                # If the light is off, power it on directly.
                # Avoid switching through FOLLOW_VIDEO/NATURAL, as that can
                # temporarily force the TV default orange color.
                if not self.get_is_on():
                    await self.send_data(
                        "ambilight/power",
                        {"power": "on"},
                    )
                # Determine the brightness value
                # If brightness is explicitly provided, use it
                # Otherwise, try to preserve the current brightness
                # If current brightness is not available, try to use the previous state brightness
                # Only use 255 as a last resort
                if kwargs.get(ATTR_BRIGHTNESS) is not None:
                    brightness = kwargs.get(ATTR_BRIGHTNESS)
                elif current_brightness is not None:
                    brightness = current_brightness
                elif self.previous_state and self.previous_state.get("brightness") is not None:
                    brightness = self.previous_state.get("brightness")
                else:
                    brightness = 255

                # Determine the hue and saturation values
                if kwargs.get(ATTR_HS_COLOR):
                    hue, saturation = kwargs.get(ATTR_HS_COLOR)
                elif current_hs_color:
                    # If color is not provided but current_hs_color is not None, use the previous values
                    hue, saturation = current_hs_color
                elif self.previous_state and self.previous_state.get("hs_color"):
                    # If current_hs_color is None (e.g., effect is active), try to use the last known color from previous_state
                    hue, saturation = self.previous_state.get("hs_color")
                else:
                    # If both color and current_hs_color are None, set default values to white (neutral)
                    # This happens when brightness is changed while an effect is active and no previous color is known
                    hue, saturation = 0, 0

                if transition_seconds > 0:
                    if current_hs_color:
                        start_hs_hue, start_hs_saturation = current_hs_color
                    elif self.previous_state and self.previous_state.get("hs_color"):
                        start_hs_hue, start_hs_saturation = self.previous_state.get(
                            "hs_color"
                        )
                    else:
                        start_hs_hue, start_hs_saturation = hue, saturation

                    if current_brightness is not None:
                        start_brightness = current_brightness
                    elif self.previous_state and self.previous_state.get("brightness") is not None:
                        start_brightness = self.previous_state.get("brightness")
                    elif self.get_is_on():
                        start_brightness = brightness
                    else:
                        start_brightness = 0

                    # Home Assistant provides HS + brightness in HSV semantics.
                    # Convert both endpoints to HLS, interpolate there, then convert
                    # each step back to HSV for the Ambilight API payload.
                    start_hsv_h = (float(start_hs_hue) % 360.0) / 360.0
                    start_hsv_s = max(0.0, min(100.0, float(start_hs_saturation))) / 100.0
                    start_hsv_v = max(0.0, min(255.0, float(start_brightness))) / 255.0
                    start_rgb = colorsys.hsv_to_rgb(start_hsv_h, start_hsv_s, start_hsv_v)
                    start_hls_h, start_hls_l, start_hls_s = colorsys.rgb_to_hls(*start_rgb)

                    target_hsv_h = (float(hue) % 360.0) / 360.0
                    target_hsv_s = max(0.0, min(100.0, float(saturation))) / 100.0
                    target_hsv_v = max(0.0, min(255.0, float(brightness))) / 255.0
                    target_rgb = colorsys.hsv_to_rgb(
                        target_hsv_h, target_hsv_s, target_hsv_v
                    )
                    target_hls_h, target_hls_l, target_hls_s = colorsys.rgb_to_hls(
                        *target_rgb
                    )

                    target_step_duration = 0.2
                    steps = max(1, int(transition_seconds / target_step_duration))
                    steps = min(steps, 50)
                    delay_per_step = max(0.0, (transition_seconds / steps) - RATE_LIMIT)

                    for step in range(1, steps + 1):
                        progress = step / steps
                        step_hls_h = self._interpolate_hue_shortest_path(
                            start_hls_h * 360.0, target_hls_h * 360.0, progress
                        )
                        step_hls_l = (
                            start_hls_l + (target_hls_l - start_hls_l) * progress
                        )
                        step_hls_s = (
                            start_hls_s + (target_hls_s - start_hls_s) * progress
                        )
                        step_hue_norm = (step_hls_h % 360.0) / 360.0
                        step_rgb = colorsys.hls_to_rgb(
                            step_hue_norm,
                            max(0.0, min(1.0, step_hls_l)),
                            max(0.0, min(1.0, step_hls_s)),
                        )
                        step_hsv_h, step_hsv_s, step_hsv_v = colorsys.rgb_to_hsv(*step_rgb)
                        if step_hsv_s < 0.01:
                            # Hue is undefined near grayscale; keep the interpolated hue
                            # instead of falling back to colorsys default (0/red).
                            step_hsv_h = step_hue_norm

                        step_hue = int(round(step_hsv_h * 255)) % 256
                        step_saturation = int(round(step_hsv_s * 255))
                        step_brightness = int(round(step_hsv_v * 255))

                        step_saturation = max(0, min(255, step_saturation))
                        step_brightness = max(0, min(255, step_brightness))
                        if step_hue == 255:
                            # Normalize hue boundary to avoid wrap artifacts on some TVs.
                            step_hue = 0
                        await self.send_data(
                            "ambilight/lounge",
                            self._build_color_data(
                                step_hue, step_saturation, step_brightness
                            ),
                        )
                        if delay_per_step > 0 and step < steps:
                            await asyncio.sleep(delay_per_step)
                else:
                    target_hue = int((hue / 360) * 255)
                    target_saturation = int((saturation / 100) * 255)
                    await self.send_data(
                        "ambilight/lounge",
                        self._build_color_data(target_hue, target_saturation, brightness),
                    )
                
                # Save the current color and brightness for later use (e.g., when switching to effect mode)
                # Convert back from 0-255 range to 0-360/0-100 range for storage
                stored_hue = round(hue)
                stored_saturation = round(saturation)
                self.previous_state = {
                    "brightness": brightness,
                    "hs_color": (stored_hue, stored_saturation),
                    "effect": None,
                }

            elif kwargs.get(ATTR_EFFECT):
                friendly_name = kwargs.get(ATTR_EFFECT)
                for effect in self.EFFECTS.values():
                    if effect["friendly_name"] == friendly_name:
                        # Check if the light is currently in HS mode
                        if self.get_effect() is None:
                            # Save the current color and brightness before switching to effect mode
                            # This allows us to restore the color when brightness is changed later
                            self.previous_state = {
                                "brightness": self.get_brightness(),
                                "hs_color": self.get_hs_color(),
                                "effect": None,
                            }
                            # If it is, turn off the light first
                            await self.send_data("ambilight/power", {"power": "off"})
                        # Then apply the new effect
                        await self.send_data(
                            effect["endpoint"],
                            effect["data"],
                        )
                        break

            # If no kwargs are provided, restore the previous state or use defaults
            elif not self.get_is_on():
                # Light is off, try to restore previous state or use defaults
                # Check if previous_state has valid (non-None) values
                restore_kwargs = {}
                
                if self.previous_state:
                    if self.previous_state.get(ATTR_BRIGHTNESS) is not None:
                        restore_kwargs[ATTR_BRIGHTNESS] = self.previous_state.get(ATTR_BRIGHTNESS)
                    if self.previous_state.get(ATTR_HS_COLOR) is not None:
                        restore_kwargs[ATTR_HS_COLOR] = self.previous_state.get(ATTR_HS_COLOR)
                    if self.previous_state.get(ATTR_EFFECT) is not None:
                        restore_kwargs[ATTR_EFFECT] = self.previous_state.get(ATTR_EFFECT)
                
                # If we have brightness or color or effect to restore, use them
                # If only brightness is available, use it with default color (0, 0)
                if restore_kwargs:
                    # If brightness is set but no color, use default color
                    if ATTR_BRIGHTNESS in restore_kwargs and ATTR_HS_COLOR not in restore_kwargs:
                        restore_kwargs[ATTR_HS_COLOR] = (0, 0)
                    # Temporarily reset the flag to allow the recursive call
                    self._turn_on_in_progress = False
                    await self.turn_on(**restore_kwargs)
                    return
                else:
                    # No previous state or no valid values, use defaults
                    # Temporarily reset the flag to allow the recursive call
                    self._turn_on_in_progress = False
                    await self.turn_on(brightness=255, hs_color=(0, 0))
                    return
            # If light is already on and no kwargs provided, do nothing
        finally:
            self._turn_on_in_progress = False

    async def turn_off(self):
        """Turn the light off."""
        # Store the current Home Assistant-reported state before turning off the light
        # Use brightness from get_brightness() if available, otherwise keep previous brightness
        current_brightness = self.get_brightness()
        if current_brightness is None and self.previous_state and self.previous_state.get("brightness") is not None:
            # If brightness is not available but we have a previous brightness, use that
            current_brightness = self.previous_state.get("brightness")
        
        # Use hs_color from get_hs_color() if available, otherwise keep previous hs_color
        current_hs_color = self.get_hs_color()
        if current_hs_color is None and self.previous_state and self.previous_state.get("hs_color") is not None:
            # If hs_color is not available but we have a previous hs_color, use that
            current_hs_color = self.previous_state.get("hs_color")
        
        # Always save the brightness and color, even if they are None (will use defaults on turn_on)
        self.previous_state = {
            "brightness": current_brightness,
            "hs_color": current_hs_color,
            "effect": self.get_effect(),
        }
        # Turn off the light
        await self.send_data("ambilight/power", {"power": "off"})
