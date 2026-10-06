from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .image import BaseImmichImage

_LOGGER = logging.getLogger(__name__)

# Keep roughly in step with the image platform's refresh cadence.
SCAN_INTERVAL = timedelta(minutes=5)


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Immich detail sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    images: list[BaseImmichImage] = data["images"]

    async_add_entities(ImmichImageDetailsSensor(image) for image in images)


class ImmichImageDetailsSensor(SensorEntity):
    """Human-readable details (location + date) of a companion image entity."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_icon = "mdi:image-text"

    def __init__(self, image: BaseImmichImage) -> None:
        """Initialize the details sensor for a given image entity."""
        self._image = image
        self._attr_unique_id = f"{image.unique_id}_details"
        self._attr_name = f"{image.name} Details"
        self._attr_device_info = image.device_info

    async def async_added_to_hass(self) -> None:
        """Register a listener to update the sensor when the image entity updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._image.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Mirror the availability of the backing image entity."""
        return self._image.available

    @property
    def native_value(self) -> str | None:
        """Return the location/date summary of the current image."""
        attrs = self._image.current_asset_attributes

        exif = attrs.get("media_exif")
        if not isinstance(exif, dict):
            exif = {}

        city = exif.get("city")
        country = exif.get("country")
        location = ", ".join(part for part in (city, country) if part)

        date_str = ""
        raw_dt = attrs.get("media_localdatetime")
        if raw_dt:
            parsed = dt_util.parse_datetime(raw_dt)
            if parsed is not None:
                date_str = parsed.strftime("%d-%B-%Y")

        summary = ", ".join(part for part in (location, date_str) if part)
        return summary or None
