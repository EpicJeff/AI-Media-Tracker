import logging

_LOGGER = logging.getLogger(__name__)

def merge_native_and_vlm(native: dict, vlm: dict) -> dict:
    """
    Combines authoritative native player state with descriptive VLM observations.
    Ensures all structured sports meta-attributes (competitors, venues) are preserved.
    """
    result = native.copy()

    if "extra_attributes" not in result:
        result["extra_attributes"] = {}

    vlm_sports = vlm.get("sports_info", {})
    vlm_sport = vlm_sports.get("sport") or vlm.get("sport")
    vlm_league = vlm_sports.get("league") or vlm.get("league")
    vlm_teams = vlm_sports.get("competitors") or vlm.get("teams") or []

    if vlm_sport:
        result["sport"] = vlm_sport
    if vlm_league:
        result["league"] = vlm_league
    if vlm_teams:
        result["teams"] = vlm_teams

    result["extra_attributes"]["sports_info"] = {
        "sport": vlm_sport,
        "league": vlm_league,
        "tournament": vlm_sports.get("tournament") or vlm.get("tournament_name") or vlm.get("tournament"),
        "venue": vlm_sports.get("venue") or vlm.get("venue"),
        "competitors": vlm_teams,
        "game_state": vlm_sports.get("game_state", {})
    }

    if vlm.get("scene_analysis"):
        result["extra_attributes"]["scene_analysis"] = vlm["scene_analysis"]

    if vlm.get("show_info"):
        if "show_info" not in result["extra_attributes"]:
            result["extra_attributes"]["show_info"] = {}
        for k, v in vlm["show_info"].items():
            if k == "rating":
                result["extra_attributes"]["show_info"]["content_rating"] = v
            else:
                result["extra_attributes"]["show_info"][k] = v

    native_app = native.get("app_name", "")
    is_authoritative_app = False
    if native_app:
        app_lower = native_app.lower().strip()
        generic_inputs = {
            "hdmi 1", "hdmi 2", "hdmi 3", "hdmi 4", "hdmi1", "hdmi2", "hdmi3", "hdmi4", 
            "live tv", "tv", "input", "antenna", "cable", "satellite", "unknown",
            "directv", "directv stream", "youtube tv", "hulu", "fubo", "sling"
        }
        if app_lower not in generic_inputs:
            is_authoritative_app = True
            result["channel_platform_name"] = native_app
            result["extra_attributes"]["channel_platform_name"] = native_app

    vlm_channel = vlm.get("channel_platform_name")
    if vlm_channel and not is_authoritative_app:
        result["channel_platform_name"] = vlm_channel
        result["extra_attributes"]["channel_platform_name"] = vlm_channel

    native_title = native.get("title") or ""
    vlm_title = vlm.get("title") or ""
    
    vlm_episode = None
    if vlm.get("show_info") and vlm["show_info"].get("episode"):
        ep = vlm["show_info"]["episode"]
        vlm_episode = ep[0] if isinstance(ep, list) and ep else ep

    if len(vlm_title) > 0 and vlm_episode:
        result["series_title"] = vlm_title
        result["title"] = vlm_episode
    elif len(vlm_title) > 0 and len(native_title) > 0 and vlm_title not in ("Browsing...", "Unknown"):
        # If native_title is populated, it usually holds the episode or movie name.
        # If the VLM found a show name but no episode name, assume native_title is the episode.
        result["series_title"] = vlm_title
        result["title"] = native_title
    elif len(vlm_title) > 0:
        result["title"] = vlm_title
    elif vlm_episode and len(native_title) > 0:
        result["series_title"] = native_title
        result["title"] = vlm_episode
    elif len(native_title) > 0:
        result["title"] = native_title

    result["vlm_analysis"] = True
    result["native_metadata"] = bool(len(native_title) > 4)

    return result
