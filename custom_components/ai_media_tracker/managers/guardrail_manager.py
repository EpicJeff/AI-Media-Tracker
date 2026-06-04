import logging

_LOGGER = logging.getLogger(__name__)

class GuardrailManager:
    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry
        self.restricted_tags = self._parse_tags(
            config_entry.options.get("guardrail_tags", "")
        )
        self.target_players = config_entry.options.get("guardrail_players", [])

    def _parse_tags(self, tags_str: str):
        if not tags_str:
            return set()
        return {t.strip().lower() for t in tags_str.split(",") if t.strip()}

    def evaluate(self, player_name: str, resolved_data: dict) -> str:
        """
        Evaluate if the resolved metadata violates the guardrails.
        Returns the matched tag if a violation occurs, otherwise None.
        """
        if player_name not in self.target_players:
            return None
            
        if not self.restricted_tags:
            return None

        # Check content rating
        show_info = resolved_data.get("extra_attributes", {}).get("show_info", {})
        rating = show_info.get("rating", "").strip().lower()
        if rating and rating in self.restricted_tags:
            return rating
            
        # Check mood tags
        scene_analysis = resolved_data.get("extra_attributes", {}).get("scene_analysis", {})
        mood_tags = scene_analysis.get("mood_tags", [])
        if isinstance(mood_tags, list):
            for tag in mood_tags:
                tag_lower = tag.strip().lower()
                if tag_lower in self.restricted_tags:
                    return tag
                
        # Check title
        title = resolved_data.get("title", "").strip().lower()
        if title:
            for r_tag in self.restricted_tags:
                if r_tag in title:
                    return r_tag

        return None
