def extract_native_state(state):
    """Parses standard Home Assistant player state wrappers into native results."""
    if not state:
        return None

    title = state.attributes.get("media_title")

    return {
        "title": title,
        "app_name": (
            state.attributes.get("app_name")
            or state.attributes.get("source")
        ),
        "state": state.state,
        "source": "native",
        "confidence": 0.95,
    }