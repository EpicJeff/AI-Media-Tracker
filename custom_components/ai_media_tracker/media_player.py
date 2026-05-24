import logging
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerDeviceClass, MediaPlayerEntityFeature
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the media player platform from a config entry."""
    devices = entry.data.get("devices", [])
    entities = []
    
    for dev_config in devices:
        player = AIMediaPlayer(hass, entry, dev_config)
        entities.append(player)
        hass.data[DOMAIN][entry.entry_id]["players"][dev_config["name"]] = player
        
    async_add_entities(entities)

class AIMediaPlayer(MediaPlayerEntity):
    """An AI Managed Media Player proxy tracking state changes from video parsers."""

    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
    
    def __init__(self, hass, entry, dev_config):
        """Initialize the media player tracker."""
        self.hass = hass
        self._entry = entry
        self._dev_config = dev_config
        self._name = dev_config["name"]
        self.slug = self._name.lower().replace(" ", "_")
        
        self._attr_unique_id = f"ai_media_{self.slug}"
        self._attr_name = f"AI Media ({self._name})"

    @property
    def state(self):
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if entry_data and entry_data.get("metadata", {}).get(self._name):
            return entry_data["metadata"][self._name].get("state", "idle")
        return "idle"

    @property
    def media_title(self):
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if entry_data and entry_data.get("metadata", {}).get(self._name):
            return entry_data["metadata"][self._name].get("title")
        return None

    @property
    def media_image_url(self) -> str | None:
        """Return the external URL or local proxy path for artwork."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if entry_data and entry_data.get("metadata", {}).get(self._name):
            device_meta = entry_data["metadata"][self._name]

            # Check primary dictionary locations
            for key in ("entity_picture", "media_image_url", "image_url", "poster_path"):
                if device_meta.get(key):
                    return device_meta[key]

            # Check nested extra_attributes locations (where VLM metrics live)
            extra_attrs = device_meta.get("extra_attributes", {})
            for key in ("entity_picture", "media_image_url", "image_url", "poster_path"):
                if extra_attrs.get(key):
                    return extra_attrs[key]

        return None

    @property
    def extra_state_attributes(self):
        """Expose deep VLM metrics natively to custom Lovelace cards."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

        if entry_data and entry_data.get("metadata", {}).get(self._name):
            device_meta = entry_data["metadata"][self._name]
            return device_meta.get("extra_attributes", {})

        return {}