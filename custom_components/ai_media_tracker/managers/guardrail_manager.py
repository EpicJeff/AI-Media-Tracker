import logging

_LOGGER = logging.getLogger(__name__)

class GuardrailManager:
    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry

    def evaluate(self, player_name: str, resolved_data: dict) -> str:
        """
        Evaluate if the resolved metadata violates the guardrails.
        Returns the matched tag/rating if a violation occurs, otherwise None.
        """
        # Get specific policy for this device
        policy = self.config_entry.options.get(f"guardrails_{player_name}")
        if not policy:
            return None

        show_info = resolved_data.get("extra_attributes", {}).get("show_info", {})
        if not show_info and "show_info" in resolved_data:
            show_info = resolved_data["show_info"]
            
        rating = show_info.get("content_rating") or show_info.get("rating", "")
        rating = str(rating).strip().lower()
        
        scene_analysis = resolved_data.get("extra_attributes", {}).get("scene_analysis", {})
        if not scene_analysis and "scene_analysis" in resolved_data:
            scene_analysis = resolved_data["scene_analysis"]
        mood_tags = scene_analysis.get("mood_tags", [])
        if not isinstance(mood_tags, list):
            mood_tags = []
            
        title = resolved_data.get("title", "").strip().lower()

        p_ratings = [r.strip().lower() for r in policy.get("ratings", [])]
        p_tags = [t.strip().lower() for t in policy.get("tags", [])]
        
        custom_str = policy.get("custom_tags", "")
        p_custom = [t.strip().lower() for t in custom_str.split(",") if t.strip()]
        
        # 1. Check content rating
        if rating and rating in p_ratings:
            return rating.upper()
            
        # 2. Check mood tags
        for tag in mood_tags:
            tag_lower = tag.strip().lower()
            if tag_lower in p_tags or tag_lower in p_custom:
                return tag.title()
                
        # 3. Check title against custom tags/titles
        if title:
            for c_tag in p_custom:
                if c_tag in title:
                    return c_tag.title()
            # Also check standard tags in title just in case
            for p_tag in p_tags:
                if p_tag in title:
                    return p_tag.title()

        return None
