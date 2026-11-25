import logging
from ..models.media_state import MediaState

_LOGGER = logging.getLogger(__name__)

class MetadataManager:
    def __init__(self, hass):
        self.hass = hass
        self._states = {}

    def get(self, player_name: str) -> MediaState:
        return self._states.setdefault(player_name, MediaState())

    def exists(self, player_name: str) -> bool:
        return player_name in self._states

    def update(self, player_name: str, media_state: MediaState):
        self._states[player_name] = media_state

    def merge(self, player_name: str, partial: dict):
        _LOGGER.info("MetadataManager.merge called for %s with: %s", player_name, partial)
        current = self.get(player_name)
        
        is_native_update = partial.get("extra_attributes", {}).get("native_metadata") is True
        should_clear = False
        
        if is_native_update:
            new_native_title = partial.get("native_title")
            if new_native_title and new_native_title != current.native_title:
                should_clear = True
        else:
            new_title = partial.get("title")
            if new_title and new_title != current.title:
                should_clear = True

        if should_clear:
            fields_to_clear = [
                "sport", "league", "teams", "venue", "tournament_name",
                "scene_analysis", "sports_data", "show_info",
                "home_headshot", "away_headshot",
                "channel_platform_name", "network_name",
                "logo_url", "entity_picture", "series_title"
            ]
            current.last_announced_title = None
            for field in fields_to_clear:
                default = [] if field == "teams" else {} if field in ["scene_analysis", "sports_data", "show_info"] else None
                setattr(current, field, default)
            
            current.native_metadata = False
            current.vlm_analysis = False

        if "extra_attributes" in partial:
            extra = partial["extra_attributes"]
            
            if "sports_info" in extra:
                s_info = extra["sports_info"]
                if "sport" in s_info: setattr(current, "sport", s_info["sport"])
                if "league" in s_info: setattr(current, "league", s_info["league"])
                if "tournament" in s_info: setattr(current, "tournament_name", s_info["tournament"])
                if "venue" in s_info: setattr(current, "venue", s_info["venue"])
                if "competitors" in s_info: setattr(current, "teams", s_info["competitors"])

            fields_to_flatten = [
                "scene_analysis", "sports_data", "show_info", 
                "app_name", "channel_platform_name", "logo_url",
                "native_metadata", "vlm_analysis", "network_name"
            ]
            for field in fields_to_flatten:
                if field in extra and extra[field] is not None:
                    setattr(current, field, extra[field])

        for k, v in partial.items():
            if v is None or k == "extra_attributes":
                continue
                
            # Protect rich fields from being downgraded by native updates
            if is_native_update and current.vlm_analysis and not should_clear:
                if k in ("title", "series_title", "entity_picture", "logo_url"):
                    continue

            if hasattr(current, k):
                setattr(current, k, v)

        self.update(player_name, current)
        
        # Fire HA Event
        self.hass.bus.async_fire("ai_media_tracker_state_changed", {
            "player_name": player_name,
            "state": current.state,
            "metadata": current.__dict__
        })
        
        # Fire Semantic Events
        if current.state == "playing" and current.title not in ("Browsing...", "Loading Info...", "System Standby", "Unknown", "", "Home Screen"):
            if current.title != current.last_announced_title:
                event_type = "ai_media_tracker_sporting_event_started" if current.sport else "ai_media_tracker_show_started"
                current.last_announced_title = current.title
                self.update(player_name, current)
                
                self.hass.bus.async_fire(event_type, {
                    "player_name": player_name,
                    "metadata": current.__dict__
                })
        
        return current
