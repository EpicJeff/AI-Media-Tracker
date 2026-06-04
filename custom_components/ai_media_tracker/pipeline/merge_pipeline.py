import logging

_LOGGER = logging.getLogger(__name__)

def merge_native_and_vlm(native: dict, vlm: dict) -> dict:
    """
    Combines authoritative native player state with descriptive VLM observations.
    Ensures all structured sports meta-attributes (competitors, venues) are preserved.
    """
    result = native.copy()

    # Create the structured extra_attributes container if missing
    if "extra_attributes" not in result:
        result["extra_attributes"] = {}

    # Extract VLM sports info payload
    vlm_sports = vlm.get("sports_info", {})
    vlm_sport = vlm_sports.get("sport") or vlm.get("sport")
    vlm_league = vlm_sports.get("league") or vlm.get("league")
    vlm_teams = vlm_sports.get("competitors") or vlm.get("teams") or []

    # Map core sports properties
    if vlm_sport:
        result["sport"] = vlm_sport
    if vlm_league:
        result["league"] = vlm_league
    if vlm_teams:
        result["teams"] = vlm_teams

    # Ensure VLM sports metadata maps directly into the output extra_attributes
    result["extra_attributes"]["sports_info"] = {
        "sport": vlm_sport,
        "league": vlm_league,
        "tournament": vlm_sports.get("tournament") or vlm.get("tournament_name") or vlm.get("tournament"),
        "venue": vlm_sports.get("venue") or vlm.get("venue"),
        "competitors": vlm_teams,
        "game_state": vlm_sports.get("game_state", {})
    }

    # Map Scene Analysis metadata
    if vlm.get("scene_analysis"):
        result["extra_attributes"]["scene_analysis"] = vlm["scene_analysis"]

    # Channel/App details
    vlm_channel = vlm.get("channel_platform_name")
    if vlm_channel:
        result["channel_platform_name"] = vlm_channel
        result["extra_attributes"]["channel_platform_name"] = vlm_channel

    # Handle Title Resolution
    native_title = native.get("title", "")
    vlm_title = vlm.get("title", "")

    if len(native_title) > 4:
        result["title"] = native_title
    elif len(vlm_title) > 0:
        result["title"] = vlm_title

    # Set flow control state flags
    result["vlm_analysis"] = True
    result["native_metadata"] = bool(len(native_title) > 4)

    return result