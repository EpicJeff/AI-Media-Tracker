import logging
import asyncio
from homeassistant.components.camera import Camera
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# A simple 1x1 black pixel JPEG as fallback
BLANK_JPEG = b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xd2\xcf\xff\xd9'

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the camera platform from a config entry."""
    devices = entry.data.get("devices", [])
    entities = []
    
    # Initialize the cameras storage bucket securely
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id].setdefault("cameras", {})

    for dev_config in devices:
        cam = LinkedZoneCamera(hass, entry, dev_config)
        entities.append(cam)
        hass.data[DOMAIN][entry.entry_id]["cameras"][dev_config["name"]] = cam
        
    async_add_entities(entities, update_before_add=True)

class LinkedZoneCamera(Camera):
    """An AI Tracker pseudo-camera rendering live feeds from RTSP streams."""

    def __init__(self, hass, entry, dev_config):
        """Initialize the camera."""
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._dev_config = dev_config
        self._name = dev_config["name"]
        self._hardware_type = dev_config.get("hardware_type", "Apple TV")
        self._stream_source = dev_config.get("stream_source", "")
        
        slug = self._name.lower().replace(" ", "_")
        self._attr_unique_id = f"ai_tracker_camera_{slug}"
        self._attr_name = f"AI Tracker Screen ({self._name})"

    async def async_camera_image(self, width=None, height=None) -> bytes:
        """Fetch a single frame forcing TCP protocols with modern FFmpeg timeout syntax."""
        if not self._stream_source:
            _LOGGER.debug("No stream source configured for camera %s", self._name)
            return BLANK_JPEG

        from homeassistant.components.ffmpeg import get_ffmpeg_manager
        
        ffmpeg_manager = get_ffmpeg_manager(self.hass)
        ffmpeg_bin = ffmpeg_manager.binary

        # Order matters perfectly here for hardware encoders:
        cmd = [
            ffmpeg_bin,
            "-rtsp_transport", "tcp",
            "-timeout", "5000000",       # 5 seconds in microseconds
            "-y",
            "-an",                      # Drop audio instantly
            "-skip_frame", "nokey",     # Only parse valid keyframes
            "-i", self._stream_source,
            "-vframes", "1",
            "-f", "image2pipe",         # FIXED: Force the specialized image pipe multiplexer
            "pipe:1"                    # FIXED: Explicitly stream directly out of stdout 1
        ]

        try:
            # Native asyncio process loop
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL  # Reverting to DEVNULL since the syntax error is solved
            )
            
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
            
            if stdout and len(stdout) > 100:
                return stdout
                
            _LOGGER.warning("FFmpeg returned empty frame for %s via custom clean pipe", self._name)
            return BLANK_JPEG

        except asyncio.TimeoutError:
            _LOGGER.error("FFmpeg clean pipe frame grab timed out for %s", self._name)
            return BLANK_JPEG
        except Exception as err:
            _LOGGER.error("FFmpeg clean pipe critical failure for %s: %s", self._name, err)
            return BLANK_JPEG