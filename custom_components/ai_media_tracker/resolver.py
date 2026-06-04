import logging

_LOGGER = logging.getLogger(__name__)

class MediaResolver:
    """
    Deterministic resolver that merges:
    - app/device metadata (authoritative)
    - VLM observations (advisory only)
    """

    def resolve(self, base: dict, vlm: dict) -> dict:
        """
        Returns FINAL canonical state.
        VLM can only suggest, never override authoritative fields.
        """

        final = dict(base)  # start from truth

        # -----------------------------
        # 1. TITLE RESOLUTION (STRICT)
        # -----------------------------
        if base.get("media_title"):
            final["title"] = base["media_title"]
            final["title_source"] = "app"
        elif vlm.get("suggested_title") and vlm.get("confidence", 0) > 0.75:
            final["title"] = vlm["suggested_title"]
            final["title_source"] = "vlm"
        else:
            final["title"] = base.get("fallback_title", "Unknown")

        # -----------------------------
        # 2. CONTENT TYPE (WEIGHTED)
        # -----------------------------
        final["content_type"] = self._resolve_content_type(base, vlm)

        # -----------------------------
        # 3. SPORTS LOGIC (HYBRID RULES)
        # -----------------------------
        final["sports"] = self._resolve_sports(base, vlm)

        # -----------------------------
        # 4. ARTWORK SIGNALS (NO OVERRIDES)
        # -----------------------------
        final["artwork_hints"] = {
            "channel": base.get("app_name"),
            "league": vlm.get("league_hint"),
            "teams": vlm.get("teams_detected", [])
        }

        # -----------------------------
        # 5. CONFIDENCE (COMPOSITE)
        # -----------------------------
        final["confidence"] = self._compute_confidence(base, vlm)

        return final

    def _resolve_content_type(self, base, vlm):
        # App metadata wins if present
        if base.get("app_name") in ("ESPN", "Fox Sports", "NBA App"):
            return "sports"

        vlm_type = vlm.get("content_type")
        if vlm_type == "sports" and vlm.get("confidence", 0) > 0.7:
            return "sports"

        return base.get("content_type", "generic")

    def _resolve_sports(self, base, vlm):
        # NEVER let VLM override known sports apps
        if base.get("app_name") in ("ESPN", "Fox Sports"):
            return {
                "source": "app",
                "league": base.get("league"),
                "teams": base.get("teams", [])
            }

        # VLM only used as suggestion layer
        if vlm.get("sports_detected") and vlm.get("confidence", 0) > 0.8:
            return {
                "source": "vlm",
                "league": vlm.get("league_hint"),
                "teams": vlm.get("teams_detected", [])
            }

        return {"source": "unknown", "teams": []}

    def _compute_confidence(self, base, vlm):
        base_score = 1.0 if base.get("media_title") else 0.5
        vlm_score = vlm.get("confidence", 0.0)

        return round(min(1.0, base_score * 0.7 + vlm_score * 0.3), 3)