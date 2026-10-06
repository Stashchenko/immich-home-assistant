"""The immich integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_WATCHED_ALBUMS, DOMAIN
from .hub import ImmichHub, InvalidAuth
from .image import ImmichImageAlbum, ImmichImageFavorite, ImmichImageMemoryLane

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up immich from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    hub = ImmichHub(host=entry.data[CONF_HOST], api_key=entry.data[CONF_API_KEY])

    if not await hub.authenticate():
        raise InvalidAuth

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Immich",
        manufacturer="Immich",
        configuration_url=entry.data[CONF_HOST],
    )

    images: list = [
        ImmichImageFavorite(hass, hub, device_info),
        ImmichImageMemoryLane(hass, hub, device_info),
    ]

    watched_albums = entry.options.get(CONF_WATCHED_ALBUMS, [])
    try:
        albums = await hub.list_all_albums()
        images.extend(
            ImmichImageAlbum(
                hass,
                hub,
                device_info,
                album_id=album["id"],
                album_name=album["albumName"],
            )
            for album in albums
            if album["id"] in watched_albums
        )
    except Exception as err:
        _LOGGER.error("Failed to fetch albums during setup: %s", err)

    hass.data[DOMAIN][entry.entry_id] = {
        "hub": hub,
        "device_info": device_info,
        "images": images
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
