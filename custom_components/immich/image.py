"""Image device for Immich integration."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import ImmichHub

SCAN_INTERVAL = timedelta(minutes=5)

# How often to refresh the list of available asset IDs
_ID_LIST_REFRESH_INTERVAL = timedelta(hours=12)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Immich image platform."""

    data = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(data["images"])


class BaseImmichImage(ImageEntity):
    """Base image entity for Immich. Subclasses define asset pools."""

    _attr_has_entity_name = True
    _attr_should_poll = True

    _attr_available = True
    _current_image_bytes: bytes | None = None
    _cached_available_asset_ids: list[str] | None = None
    _available_asset_ids_last_updated: datetime | None = None

    def __init__(self, hass: HomeAssistant, hub: ImmichHub, device_info: DeviceInfo | None = None) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass=hass)
        self.hub = hub
        self.hass = hass
        self._attr_device_info = device_info

        self._attr_extra_state_attributes = {}
        self._listeners: list = []

    @property
    def current_asset_attributes(self) -> dict:
        """Return the attributes of the current asset."""
        return self._attr_extra_state_attributes or {}

    async def async_added_to_hass(self) -> None:
        """Register a listener to update the image when the entity updates."""
        await super().async_added_to_hass()
        self.hass.async_create_task(self._load_and_cache_next_image())

    def async_add_listener(self, update_callback) -> "callable":
        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def _write_state_and_notify(self) -> None:
        """Write the state and notify listeners."""
        self.async_write_ha_state()
        for update_callback in list(self._listeners):
            update_callback()

    async def async_update(self) -> None:
        """Force a refresh of the image."""
        await self._load_and_cache_next_image()

    async def async_image(self) -> bytes | None:
        """Return the current image. If no image is available, load and cache it."""
        if self._current_image_bytes is None:
            await self._load_and_cache_next_image()

        return self._current_image_bytes

    def _set_unavailable(self) -> None:
        """Set the entity as unavailable and clear cached image."""
        if self._attr_available or self._current_image_bytes is not None:
            self._current_image_bytes = None
            self._attr_available = False
            self._attr_extra_state_attributes = {}
            self._attr_image_last_updated = None
            self._write_state_and_notify()

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        raise NotImplementedError

    async def _get_next_asset_id(self) -> str | None:
        """Get the asset id of the next image we want to display."""
        now = datetime.now(timezone.utc)

        # Determine if cache is missing or older than interval limit
        need_refresh = (
                not self._available_asset_ids_last_updated
                or (now - self._available_asset_ids_last_updated) > _ID_LIST_REFRESH_INTERVAL
        )

        # Extra Check: If calendar day rolled over, clear cache regardless of hours passed
        if not need_refresh and self._available_asset_ids_last_updated:
            if now.date() != self._available_asset_ids_last_updated.date():
                need_refresh = True

        if need_refresh:
            _LOGGER.debug("Refreshing available asset IDs")
            try:
                self._cached_available_asset_ids = await self._refresh_available_asset_ids()
                self._available_asset_ids_last_updated = now
            except Exception as err:
                _LOGGER.error("Failed to refresh asset IDs: %s", err)
                if not self._cached_available_asset_ids:
                    return None

        if not self._cached_available_asset_ids:
            return None

        return random.choice(self._cached_available_asset_ids)

    async def _load_and_cache_next_image(self) -> None:
        """Download and cache the image safely without infinite loops."""
        asset_bytes = None
        attempts = 0
        max_attempts = 5  # Prevents hanging HA event loop if multiple items fail

        while not asset_bytes and attempts < max_attempts:
            attempts += 1
            asset_id = await self._get_next_asset_id()

            if not asset_id:
                self._set_unavailable()
                return

            try:
                asset_bytes = await self.hub.download_asset(asset_id)

                if not asset_bytes:
                    _LOGGER.warning("Failed to download asset %s, retrying", asset_id)
                    await asyncio.sleep(0.5)
                    continue

                asset_info = await self.hub.get_asset_info(asset_id)
                if not asset_info:
                    asset_bytes = None
                    continue

                # Set attributes safely
                self._attr_extra_state_attributes["media_filename"] = (
                        asset_info.get("originalFileName") or ""
                )
                self._attr_extra_state_attributes["media_exif"] = (
                        asset_info.get("exifInfo") or ""
                )
                self._attr_extra_state_attributes["media_localdatetime"] = (
                        asset_info.get("localDateTime") or ""
                )

                self._current_image_bytes = asset_bytes
                self._attr_image_last_updated = datetime.now(timezone.utc)
                self._attr_available = True
                self._write_state_and_notify()

            except Exception as exception:
                _LOGGER.error("Error processing asset %s: %s", asset_id, exception)
                asset_bytes = None
                await asyncio.sleep(0.5)


class ImmichImageFavorite(BaseImmichImage):
    """Image entity for Immich that displays a random image from the user's favorites."""

    _attr_unique_id = "favorite_image"
    _attr_name = "Random favorite image"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        return [image["id"] for image in await self.hub.list_favorite_images()]


class ImmichImageAlbum(BaseImmichImage):
    """Image entity for Immich that displays a random image from a specific album."""

    def __init__(self, hass: HomeAssistant, hub: ImmichHub, device_info: DeviceInfo | None = None,
                 *, album_id: str, album_name: str) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass, hub, device_info)
        self._album_id = album_id
        self._attr_unique_id = album_id
        self._attr_name = album_name

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        return [
            image["id"] for image in await self.hub.list_album_images(self._album_id)
        ]


class ImmichImageMemoryLane(BaseImmichImage):
    """Displays random 'memory lane' images for today."""

    _attr_unique_id = "memory_lane_image"
    _attr_name = "Memory Lane"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        return [asset["id"] for asset in await self.hub.list_memory_lane_images()]
