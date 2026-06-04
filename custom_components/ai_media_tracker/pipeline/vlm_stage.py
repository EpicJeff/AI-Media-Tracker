import io
from PIL import Image
import logging

_LOGGER = logging.getLogger(__name__)

def optimize_image_for_vlm(raw_bytes: bytes) -> bytes:
    """Creates a vertical composite: Full Frame (Context) + Top/Bottom Strips (Detail)."""
    try:
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        w, h = img.size
        
        full_frame = img.resize((1024, int(1024 * (h/w))), Image.Resampling.LANCZOS)
        
        top_strip_raw = img.crop((0, 0, w, int(h * 0.20)))
        bottom_strip_raw = img.crop((0, int(h * 0.75), w, h))
        
        top_strip = top_strip_raw.resize((1024, int(1024 * (top_strip_raw.height / top_strip_raw.width))), Image.Resampling.LANCZOS)
        bottom_strip = bottom_strip_raw.resize((1024, int(1024 * (bottom_strip_raw.height / bottom_strip_raw.width))), Image.Resampling.LANCZOS)

        total_h = full_frame.height + top_strip.height + bottom_strip.height
        composite = Image.new("RGB", (1024, total_h))
        
        composite.paste(full_frame, (0, 0))
        composite.paste(top_strip, (0, full_frame.height))
        composite.paste(bottom_strip, (0, full_frame.height + top_strip.height))

        output = io.BytesIO()
        composite.save(output, format="JPEG", quality=80, optimize=True)
        return output.getvalue()
    except Exception as e:
        _LOGGER.error("Optimization failed inside vlm_stage: %s", e)
        return raw_bytes


BAD_TITLES = {
    "your tv",
    "television",
    "live tv",
    "sports",
    "directv",
    "tnt sports",
}


def validate_vlm_result(result):
    show_info = result.get("show_info", {})
    episode_val = show_info.get("episode")
    
    ep_str = episode_val[0] if isinstance(episode_val, list) and episode_val else episode_val
    ep_title = str(ep_str).strip().lower() if ep_str else ""
    
    main_title = str(result.get("title", "")).strip().lower()

    if main_title in BAD_TITLES or ep_title in BAD_TITLES:
        return False

    return True
