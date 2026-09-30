"""Constants for the Pawfly Aquarium Light integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pawfly"

MANUFACTURER: Final = "Pawfly"
MODEL: Final = "PY4C Bluetooth Aquarium Light"
MODEL_ID: Final = "PY4C"

# --- config entry data ---------------------------------------------------------------
# ``CONF_ADDRESS`` / ``CONF_PASSWORD`` come from homeassistant.const.
#: ESPHome node name (``scanner.adapter``) of the proxy that carried the link last.
CONF_LAST_HOLDING_PROXY: Final = "last_holding_proxy"
#: A new password written to the light whose proof (a session opened with it) is still outstanding.
#: The light may already want it, so it is tried first on the next connect and settled there.
CONF_PENDING_PASSWORD: Final = "pending_password"

# --- options -------------------------------------------------------------------------
CONF_KEEP_CONNECTED: Final = "keep_connected"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_PREFERRED_PROXY: Final = "preferred_proxy"
#: The options form's value for "no preferred proxy". A real value, because a select cannot be set
#: back to an empty string once something else was chosen; it is stored as an empty string.
PROXY_AUTOMATIC: Final = "automatic"

DEFAULT_PASSWORD: Final = "12345678"
PASSWORD_LENGTH: Final = 8

DEFAULT_KEEP_CONNECTED: Final = True
DEFAULT_POLL_INTERVAL: Final = 60
MIN_POLL_INTERVAL: Final = 10
MAX_POLL_INTERVAL: Final = 3600

# --- time sync -----------------------------------------------------------------------
#: The light forgets the time on power loss and has no time zone: HA pushes local time on
#: every (re)connect and once a day (this also follows DST changes).
TIME_SYNC_HOUR: Final = 3
TIME_SYNC_MINUTE: Final = 17

# --- release_link --------------------------------------------------------------------
ATTR_RESUME_AFTER: Final = "resume_after"
DEFAULT_RESUME_AFTER: Final = 180
MAX_RESUME_AFTER: Final = 900
#: Upper bound for unloading one entry during release_link.
RELEASE_DEADLINE: Final = 20.0

# --- light ---------------------------------------------------------------------------
EFFECT_DEMO: Final = "Demo"

# --- services ------------------------------------------------------------------------
SERVICE_GET_PROGRAM: Final = "get_program"
SERVICE_SET_PROGRAM: Final = "set_program"
SERVICE_PREVIEW: Final = "preview"
SERVICE_SET_CHANNEL: Final = "set_channel"
SERVICE_SYNC_TIME: Final = "sync_time"
SERVICE_RENAME: Final = "rename"
SERVICE_CHANGE_PASSWORD: Final = "change_password"
SERVICE_RELEASE_LINK: Final = "release_link"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_PROGRAM: Final = "program"
ATTR_POINTS: Final = "points"
ATTR_END: Final = "end"
ATTR_NAME: Final = "name"
ATTR_NEW_PASSWORD: Final = "new_password"
