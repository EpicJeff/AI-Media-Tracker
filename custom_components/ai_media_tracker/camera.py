import logging
import asyncio
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BLANK_JPEG = b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xd2\xcf\xff\xd9'

async def async_setup_entry(hass, entry, async_add_entities):
    devices = entry.data.get("devices", [])
    entities = [LinkedZoneCamera(hass, entry, d) for d in devices]
    
    # Store camera objects in the existing domain data bucket
    hass.data[DOMAIN][entry.entry_id]["cameras"] = {d["name"]: e for d, e in zip(devices, entities)}
    async_add_entities(entities, update_before_add=True)

class LinkedZoneCamera(Camera):
    def __init__(self, hass, entry, dev_config):
        super().__init__()
        self.hass = hass
        self._name = dev_config["name"]
        self._stream_source = dev_config.get("stream_source", "")
        slug = self._name.lower().replace(" ", "_")
        self._attr_unique_id = f"ai_tracker_camera_{slug}"
        self._attr_name = f"AI Tracker Screen ({self._name})"

    @property
    def supported_features(self) -> CameraEntityFeature:
        return CameraEntityFeature.STREAM if self._stream_source else 0

    async def stream_source(self):
        """Return the source of the stream."""
        slug = self._name.replace("AI Tracker Screen (", "").replace(")", "").strip().lower().replace(" ", "_")
        from homeassistant.helpers.network import get_url
        try:
            base_url = get_url(self.hass, prefer_internal=True)
            return f"{base_url}/api/ai_media_tracker/video/{slug}.ts"
        except Exception:
            return f"http://127.0.0.1:{self.hass.config.api.port}/api/ai_media_tracker/video/{slug}.ts"

    async def async_camera_image(self, width=None, height=None) -> bytes:
        if not self._stream_source: return BLANK_JPEG
        
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        cmd = [ffmpeg_bin]
        if self._stream_source.startswith("rtsp"):
            cmd.extend(["-rtsp_transport", "tcp", "-timeout", "5000000"])
        
        cmd.extend([
            "-y", "-an", "-skip_frame", "nokey", "-i", self._stream_source,
            "-vframes", "1", "-f", "image2pipe", "pipe:1"
        ])
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
            return stdout if stdout and len(stdout) > 100 else BLANK_JPEG
        except Exception:
            return BLANK_JPEG