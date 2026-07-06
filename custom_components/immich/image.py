"""Image device for Immich integration."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_WATCHED_ALBUMS, DOMAIN
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

    hub: ImmichHub = hass.data[DOMAIN][config_entry.entry_id]

    # Create entity for random favorite image and memory lane
    async_add_entities(
        [
            ImmichImageFavorite(hass, hub),
            ImmichImageMemoryLane(hass, hub),
        ]
    )

    # Create entities for random image from each watched album safely
    watched_albums = config_entry.options.get(CONF_WATCHED_ALBUMS, [])
    try:
        albums = await hub.list_all_albums()
        async_add_entities(
            [
                ImmichImageAlbum(
                    hass, hub, album_id=album["id"], album_name=album["albumName"]
                )
                for album in albums
                if album["id"] in watched_albums
            ]
        )
    except Exception as err:
        _LOGGER.error("Failed to fetch albums during setup: %s", err)

    config_entry.async_on_unload(config_entry.add_update_listener(update_listener))


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(config_entry.entry_id)


class BaseImmichImage(ImageEntity):
    """Base image entity for Immich. Subclasses define asset pools."""

    _attr_has_entity_name = True
    _attr_should_poll = True

    _current_image_bytes: bytes | None = None
    _cached_available_asset_ids: list[str] | None = None
    _available_asset_ids_last_updated: datetime | None = None

    def __init__(self, hass: HomeAssistant, hub: ImmichHub) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass=hass)
        self.hub = hub
        self.hass = hass

        self._attr_extra_state_attributes = {}

    async def async_update(self) -> None:
        """Force a refresh of the image."""
        await self._load_and_cache_next_image()

    async def async_image(self) -> bytes | None:
        """Return the current image. If no image is available, load and cache it."""
        if not self._current_image_bytes:
            await self._load_and_cache_next_image()

        return self._current_image_bytes

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
                self.async_write_ha_state()

            except Exception as exception:
                _LOGGER.error("Error processing asset %s: %s", asset_id, exception)
                asset_bytes = None
                await asyncio.sleep(0.5)


class ImmichImageFavorite(BaseImmichImage):
    """Image entity for Immich that displays a random image from the user's favorites."""

    _attr_unique_id = "favorite_image"
    _attr_name = "Immich: Random favorite image"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        return [image["id"] for image in await self.hub.list_favorite_images()]


class ImmichImageAlbum(BaseImmichImage):
    """Image entity for Immich that displays a random image from a specific album."""

    def __init__(
            self, hass: HomeAssistant, hub: ImmichHub, album_id: str, album_name: str
    ) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass, hub)
        self._album_id = album_id
        self._attr_unique_id = album_id
        self._attr_name = f"Immich: {album_name}"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        return [
            image["id"] for image in await self.hub.list_album_images(self._album_id)
        ]


class ImmichImageMemoryLane(BaseImmichImage):
    """Displays random 'memory lane' images for today."""

    _attr_unique_id = "memory_lane_image"
    _attr_name = "Immich: Memory Lane"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        return [asset["id"] for asset in await self.hub.list_memory_lane_images()]
