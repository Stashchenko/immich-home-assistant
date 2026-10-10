"""Constants for the immich integration."""

DOMAIN = "immich"
CONF_WATCHED_ALBUMS = "watched_albums"
CONF_DATE_FORMAT = "date_format"
DEFAULT_DATE_FORMAT = "%d %b %Y"

DATE_FORMAT_OPTIONS = [
    {"value": "%d-%B-%Y", "label": "10-October-2026"},
    {"value": "%d.%m.%Y", "label": "10.10.2026"},
    {"value": "%Y-%m-%d", "label": "2026-10-10"},
    {"value": "%d %b %Y", "label": "10 Oct 2026"},
    {
        "value": "%A, %d %B %Y",
        "label": "Saturday, 10 October 2026",
    },
]
