import logging
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.const import STATE_IDLE
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    bucket = hass.data[DOMAIN][entry.entry_id]
    devices = entry.data.get("devices", [])
    entities = []

    for dev in devices:
        name = dev.get("name")
        if not name:
            continue
        ent = AIMediaPlayer(hass, entry, name)
        entities.append(ent)
        bucket["players"][name] = ent

    async_add_entities(entities)

class AIMediaPlayer(MediaPlayerEntity):
    _attr_should_poll = False

    def __init__(self, hass, entry, player_name):
        self.hass = hass
        self.entry = entry
        self.player_name = player_name
        slug = player_name.lower().replace(" ", "_")
        self._attr_name = f"AI Media {player_name}"
        self._attr_unique_id = f"ai_media_tracker_player_{slug}"
        self.entity_id = f"media_player.ai_media_{slug}"

    async def async_added_to_hass(self):
        """Run when entity is added to hass."""
        from homeassistant.core import callback
        
        @callback
        def on_state_changed(event):
            # Only trigger on newly added media players
            if event.data.get("old_state") is None and event.data.get("entity_id", "").startswith("media_player."):
                self.async_write_ha_state()
                
        self.async_on_remove(
            self.hass.bus.async_listen("state_changed", on_state_changed)
        )

    def _meta(self):
        try:
            return self.hass.data[DOMAIN][self.entry.entry_id]["metadata_mgr"].get(self.player_name)
        except Exception as e:
            _LOGGER.error("Failed checking operational runtime metadata maps: %s", e)
            return None

    @property
    def state(self) -> MediaPlayerState | str:
        media_state = self._meta()
        if not media_state: return STATE_IDLE
        s = getattr(media_state, "state", "idle")
        if s == "playing": return MediaPlayerState.PLAYING
        if s == "paused": return MediaPlayerState.PAUSED
        return STATE_IDLE

    @property
    def media_title(self) -> str | None:
        media_state = self._meta()
        return getattr(media_state, "title", None) if media_state else None

    @property
    def media_series_title(self) -> str | None:
        media_state = self._meta()
        return getattr(media_state, "series_title", None) if media_state else None

    @property
    def entity_picture(self) -> str | None:
        media_state = self._meta()
        return getattr(media_state, "entity_picture", None) if media_state else None

    def _compute_cast_targets(self):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.components.media_player import MediaPlayerEntityFeature
        
        allowed_platforms = self.entry.options.get("allowed_cast_platforms", [])
        allowed_classes = self.entry.options.get("allowed_cast_device_classes", [])
        
        BANNED_PLATFORMS = {"androidtv_remote", "androidtv", "plex", "jellyfin", "emby"}
        
        valid_targets = []
        entity_registry = er.async_get(self.hass)
        
        for state in self.hass.states.async_all("media_player"):
            if state.entity_id.startswith("media_player.ai_media_") or state.entity_id.startswith("media_player.ai_tracker_"):
                continue
            
            features = state.attributes.get("supported_features", 0)
            if not (features & MediaPlayerEntityFeature.PLAY_MEDIA):
                continue
                
            entity = entity_registry.async_get(state.entity_id)
            platform = entity.platform if entity else "unknown"
            
            if platform in BANNED_PLATFORMS:
                continue
                
            if allowed_classes:
                dclass = state.attributes.get("device_class", "unknown")
                if dclass not in allowed_classes and ("unknown" not in allowed_classes or dclass):
                    # We only continue if it didn't match the specific class, AND (unknown isn't allowed OR dclass isn't None)
                    # Actually, if dclass is None, it maps to "unknown".
                    dclass_key = dclass if dclass else "unknown"
                    if dclass_key not in allowed_classes:
                        continue
                    
            if allowed_platforms:
                if platform not in allowed_platforms:
                    continue
                    
            valid_targets.append(state.entity_id)
            
        return valid_targets

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes of the virtual player."""
        data = self.hass.data[DOMAIN][self.entry.entry_id]
        meta_mgr = data.get("metadata_mgr")
        media_state = meta_mgr.get(self.player_name) if meta_mgr else None

        slug = self.player_name.lower().replace(" ", "_")
        camera_entity_id = f"camera.ai_tracker_screen_{slug}"
        camera_state = self.hass.states.get(camera_entity_id)
        camera_stream_url = None
        
        if camera_state and camera_state.attributes.get("entity_picture"):
            ep = camera_state.attributes.get("entity_picture")
            camera_stream_url = ep.replace("/api/camera_proxy/", "/api/camera_proxy_stream/")

        if not media_state:
            return {"proxy_player": True, "player_name": self.player_name, "camera_stream_url": camera_stream_url, "camera_entity_id": camera_entity_id, "cast_targets": self._compute_cast_targets()}

        return {
            "scene_analysis": getattr(media_state, "scene_analysis", {}),
            "channel_platform_name": getattr(media_state, "channel_platform_name", None),
            "app_name": getattr(media_state, "app_name", None),
            "network_name": getattr(media_state, "network_name", None),
            "sport": getattr(media_state, "sport", None),
            "league": getattr(media_state, "league", None),
            "logo_url": getattr(media_state, "logo_url", None),
            "sports_data": getattr(media_state, "sports_data", {}),
            "show_info": getattr(media_state, "show_info", {}),
            "venue": getattr(media_state, "venue", None),
            "tournament_name": getattr(media_state, "tournament_name", None),
            "proxy_player": True,
            "player_name": self.player_name,
            "camera_stream_url": camera_stream_url,
            "camera_entity_id": camera_entity_id,
            "cast_targets": self._compute_cast_targets(),
        }
