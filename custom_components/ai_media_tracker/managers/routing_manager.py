import logging
import asyncio
from homeassistant.helpers.network import get_url
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from aiohttp import web
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

AUDIO_ONLY_PLATFORMS = {"sonos", "mass", "heos", "bluesound", "squeezebox", "spotify"}

class AudioProxyView(HomeAssistantView):
    url = "/api/ai_media_tracker/audio/{slug}"
    name = "api:ai_media_tracker:audio"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, slug):
        from ..const import DOMAIN
        
        # Strip extensions if present
        slug = slug.replace(".mp3", "").replace(".ts", "").replace(".mp4", "")
        
        stream_source = None
        for entry_id, data in self.hass.data[DOMAIN].items():
            if not isinstance(data, dict): continue
            cameras = data.get("cameras", {})
            for name, cam in cameras.items():
                if cam._attr_unique_id == f"ai_tracker_camera_{slug}":
                    stream_source = cam._stream_source
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry:
                        for dev in entry.data.get("devices", []):
                            if dev["name"] == name:
                                cast_source = dev.get("cast_stream_source")
                                if cast_source:
                                    stream_source = cast_source
                                break
                    break
            if stream_source:
                break
                
        if not stream_source:
            return web.Response(status=404)

        response = web.StreamResponse()
        response.content_type = "audio/mpeg"
        await response.prepare(request)
        
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        cmd = [
            ffmpeg_bin,
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", stream_source,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-f", "mp3",
            "pipe:1"
        ]
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                await response.write(chunk)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            await response.write_eof()
            
        return response

class VideoProxyView(HomeAssistantView):
    url = "/api/ai_media_tracker/video/{slug}"
    name = "api:ai_media_tracker:video"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, slug):
        from ..const import DOMAIN
        import logging
        _LOGGER = logging.getLogger(__name__)
        
        # Strip extensions if present
        slug = slug.replace(".ts", "").replace(".mp4", "").replace(".m3u8", "")
        _LOGGER.error(f"VideoProxyView requested for parsed slug: {slug}")
        
        stream_source = None
        device_id = None
        audio_delay = 0.0
        
        for entry_id, data in self.hass.data[DOMAIN].items():
            if not isinstance(data, dict): continue
            cameras = data.get("cameras", {})
            for name, cam in cameras.items():
                if cam._attr_unique_id == f"ai_tracker_camera_{slug}":
                    stream_source = cam._stream_source
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry:
                        for dev in entry.data.get("devices", []):
                            if dev["name"] == name:
                                cast_source = dev.get("cast_stream_source")
                                if cast_source:
                                    stream_source = cast_source
                                device_id = dev.get("device_id")
                                audio_delay = entry.options.get(f"cast_audio_delay_{device_id}", 0.0)
                                break
                    break
            if stream_source:
                break
                
        if not stream_source:
            _LOGGER.error("Stream source is None! Returning 404")
            return web.Response(status=404)

        response = web.StreamResponse()
        response.content_type = "video/mp2t"
        await response.prepare(request)
        
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        cmd = [ffmpeg_bin]
        
        if audio_delay > 0:
            cmd.extend(["-itsoffset", str(audio_delay), "-i", stream_source])
            cmd.extend(["-i", stream_source])
            cmd.extend(["-map", "1:v", "-map", "0:a"])
        elif audio_delay < 0:
            cmd.extend(["-itsoffset", str(abs(audio_delay)), "-i", stream_source])
            cmd.extend(["-i", stream_source])
            cmd.extend(["-map", "0:v", "-map", "1:a"])
        else:
            if stream_source.startswith("rtsp"):
                cmd.extend(["-rtsp_transport", "tcp"])
            cmd.extend(["-i", stream_source])
            
        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-f", "mpegts",
            "pipe:1"
        ])
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                await response.write(chunk)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            await response.write_eof()
            
        return response

class StreamMultiplexer:
    def __init__(self, hass):
        self.hass = hass
        self.streams = {}

    async def add_client(self, slug, ws, stream_source, audio_delay):
        if slug not in self.streams:
            await self._start_stream(slug, stream_source, audio_delay)
        
        self.streams[slug]["clients"].append(ws)
        _LOGGER.info("Client connected to multiplexer for %s. Total clients: %d", slug, len(self.streams[slug]["clients"]))
        
    async def remove_client(self, slug, ws):
        if slug in self.streams:
            if ws in self.streams[slug]["clients"]:
                self.streams[slug]["clients"].remove(ws)
            _LOGGER.info("Client disconnected from multiplexer for %s. Remaining: %d", slug, len(self.streams[slug]["clients"]))
            if not self.streams[slug]["clients"]:
                await self._stop_stream(slug)

    async def _start_stream(self, slug, stream_source, audio_delay):
        _LOGGER.info("Starting FFmpeg multiplexer for %s", slug)
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        cmd = [
            ffmpeg_bin,
            "-fflags", "nobuffer",
            "-flags", "low_delay"
        ]
        
        if audio_delay > 0:
            cmd.extend(["-itsoffset", str(audio_delay), "-i", stream_source, "-i", stream_source, "-map", "1:v", "-map", "0:a"])
        elif audio_delay < 0:
            cmd.extend(["-itsoffset", str(abs(audio_delay)), "-i", stream_source, "-i", stream_source, "-map", "0:v", "-map", "1:a"])
        else:
            if stream_source.startswith("rtsp"):
                cmd.extend(["-rtsp_transport", "tcp"])
            cmd.extend(["-i", stream_source])
            
        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-f", "mpegts",
            "-flush_packets", "1",
            "pipe:1"
        ])
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        
        async def broadcast_loop():
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    clients = list(self.streams[slug]["clients"])
                    for client in clients:
                        try:
                            await client.send_bytes(chunk)
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass
            finally:
                if proc.returncode is None:
                    try: proc.terminate()
                    except ProcessLookupError: pass
                
                if slug in self.streams:
                    clients = list(self.streams[slug]["clients"])
                    for client in clients:
                        try: await client.close()
                        except Exception: pass
                    self.streams.pop(slug, None)

        task = self.hass.loop.create_task(broadcast_loop())
        self.streams[slug] = {"proc": proc, "clients": [], "task": task}

    async def _stop_stream(self, slug):
        _LOGGER.info("Stopping FFmpeg multiplexer for %s", slug)
        if slug in self.streams:
            stream = self.streams.pop(slug)
            stream["task"].cancel()
            proc = stream["proc"]
            if proc.returncode is None:
                try: proc.terminate()
                except ProcessLookupError: pass

class WebSocketProxyView(HomeAssistantView):
    url = "/api/ai_media_tracker/ws/{slug}"
    name = "api:ai_media_tracker:ws"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, slug):
        from ..const import DOMAIN
        import logging
        _LOGGER = logging.getLogger(__name__)
        
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        
        slug = slug.replace(".ts", "").replace(".mp4", "").replace(".m3u8", "")
        _LOGGER.info(f"WebSocket requested for slug: {slug}")
        
        stream_source = None
        device_id = None
        audio_delay = 0.0
        
        for entry_id, data in self.hass.data[DOMAIN].items():
            if not isinstance(data, dict): continue
            cameras = data.get("cameras", {})
            for name, cam in cameras.items():
                if cam._attr_unique_id == f"ai_tracker_camera_{slug}":
                    stream_source = cam._stream_source
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry:
                        for dev in entry.data.get("devices", []):
                            if dev["name"] == name:
                                cast_source = dev.get("cast_stream_source")
                                if cast_source:
                                    stream_source = cast_source
                                device_id = dev.get("device_id")
                                audio_delay = entry.options.get(f"cast_audio_delay_{device_id}", 0.0)
                                break
                    break
            if stream_source: break
                
        if not stream_source:
            await ws.close()
            return ws

        if "multiplexer" not in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN]["multiplexer"] = StreamMultiplexer(self.hass)
            
        multiplexer = self.hass.data[DOMAIN]["multiplexer"]
        await multiplexer.add_client(slug, ws, stream_source, audio_delay)
        
        try:
            # Keep connection open until client disconnects
            async for msg in ws:
                pass
        except Exception:
            pass
        finally:
            await multiplexer.remove_client(slug, ws)
            
        return ws


class ActiveCastProxyView(HomeAssistantView):
    url = "/api/ai_media_tracker/active_cast"
    name = "api:ai_media_tracker:active_cast"
    requires_auth = False
    cors_allowed = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request):
        from ..const import DOMAIN
        # Find the most recently activated cast target across all entries
        active_slug = None
        for entry_id, data in self.hass.data[DOMAIN].items():
            if not isinstance(data, dict): continue
            if "active_cast_slug" in data:
                active_slug = data["active_cast_slug"]
                break
                
        # Also return the HA internal URL so the web app knows where to connect the WebSocket
        base_url = get_url(self.hass, prefer_internal=True)
        return web.json_response({"slug": active_slug, "base_url": base_url}, headers={"Access-Control-Allow-Origin": "*"})

class RoutingManager:
    @staticmethod
    async def cast_stream(hass, config_entry, source_player_name, target_receivers):
        from ..const import DOMAIN
        data = hass.data[DOMAIN][config_entry.entry_id]
        
        # Expand target_receivers to include children
        group_mgr = data.get("group_mgr")
        if group_mgr:
            expanded_receivers = set(target_receivers)
            for r in target_receivers:
                children = group_mgr.groups.get(r, [])
                expanded_receivers.update(children)
            target_receivers = list(expanded_receivers)
        
        slug = source_player_name.lower().replace(" ", "_")
        camera_entity_id = f"camera.ai_tracker_screen_{slug}"
        camera_state = hass.states.get(camera_entity_id)
        
        if not camera_state or not camera_state.attributes.get("entity_picture"):
            _LOGGER.error("Cannot cast stream for %s: no active camera stream found.", source_player_name)
            return

        hls_stream_url = camera_state.attributes.get("entity_picture").replace("/api/camera_proxy/", "/api/camera_proxy_stream/")
        mp3_stream_url = f"/api/ai_media_tracker/audio/{slug}.mp3"
        
        try:
            base_url = get_url(hass)
            full_mp3_url = f"{base_url}{mp3_stream_url}"
        except Exception as e:
            _LOGGER.error("Failed to get external URL for casting: %s", e)
            full_mp3_url = mp3_stream_url

        registry = er.async_get(hass)
        
        custom_app_id = config_entry.options.get("advanced_cast_app_id")
        base_stream_url = f"/api/ai_media_tracker/video/{slug}.ts"
        try:
            base_stream_url = f"{get_url(hass)}{base_stream_url}"
        except Exception:
            pass
        
        for receiver in target_receivers:
            data.setdefault("active_routes", {})[receiver] = source_player_name
            
            # Determine the underlying platform of the receiver
            entity_entry = registry.async_get(receiver)
            integration_platform = entity_entry.platform if entity_entry else "unknown"
            
            if integration_platform in AUDIO_ONLY_PLATFORMS:
                _LOGGER.info("Casting AUDIO stream via play_media from %s to %s (platform: %s)", source_player_name, receiver, integration_platform)
                await hass.services.async_call("media_player", "play_media", {
                    "entity_id": receiver,
                    "media_content_id": full_mp3_url,
                    "media_content_type": "music"
                }, blocking=False)
                continue

            # It's a video device
            if "active_cast_slug" not in hass.data[DOMAIN]:
                hass.data[DOMAIN]["active_cast_slug"] = None
            hass.data[DOMAIN]["active_cast_slug"] = slug
            
            if integration_platform == "apple_tv":
                _LOGGER.info("Casting VIDEO stream via camera.play_stream (HLS) to Apple TV %s", receiver)
                await hass.services.async_call("camera", "play_stream", {
                    "entity_id": camera_entity_id,
                    "media_player": receiver,
                    "format": "hls"
                }, blocking=False)
                continue

            if integration_platform == "cast" and custom_app_id:
                def send_cast_message():
                    mp_component = hass.data.get("media_player")
                    if mp_component:
                        entity = mp_component.get_entity(receiver)
                        _LOGGER.info("send_cast_message: Found entity %s: %s", receiver, type(entity))
                        if entity:
                            chromecast = getattr(entity, "_chromecast", None)
                            if not chromecast and hasattr(entity, "cast_device"):
                                chromecast = entity.cast_device
                            if not chromecast and hasattr(entity, "_get_chromecast"):
                                try:
                                    chromecast = entity._get_chromecast()
                                except Exception as e:
                                    _LOGGER.error("Failed to call _get_chromecast: %s", e)
                                    
                            if chromecast:
                                import pychromecast.controllers
                                import pychromecast.error
                                import time
                                
                                _LOGGER.info("Connecting to cast device %s...", receiver)
                                try:
                                    chromecast.wait(5)
                                except Exception as e:
                                    _LOGGER.warning("Wait for cast device failed (might still be connected): %s", e)
                                    
                                _LOGGER.info("Starting custom cast app %s on %s", custom_app_id, receiver)
                                try:
                                    chromecast.start_app(custom_app_id)
                                except pychromecast.error.RequestTimeout:
                                    _LOGGER.warning("Cast device %s timed out during start_app. It might just be slow to boot. Proceeding anyway...", receiver)
                                except Exception as e:
                                    if type(e).__name__ == "NotConnected":
                                        _LOGGER.error("Cast device %s is not connected. Failed to start app.", receiver)
                                    elif type(e).__name__ == "LaunchError":
                                        _LOGGER.error("Cast device %s actively rejected the App ID %s. Ensure it is registered and rebooted.", receiver, custom_app_id)
                                    else:
                                        _LOGGER.error("Unexpected error starting cast app: %s", e)
                                    return False
                                    
                                time.sleep(3.0) # Wait a bit longer for Android TVs to boot the app
                                
                                class AIMediaController(pychromecast.controllers.BaseController):
                                    def __init__(self):
                                        super().__init__("urn:x-cast:com.ai_media_tracker")
                                        
                                controller = AIMediaController()
                                chromecast.register_handler(controller)
                                
                                ws_url = base_stream_url.replace("http://", "ws://").replace("https://", "wss://").replace("/video/", "/ws/").replace(".ts", "")
                                _LOGGER.info("Sending WS URL to cast device: %s", ws_url)
                                controller.send_message({"type": "connect", "ws_url": ws_url})
                                return True
                            else:
                                _LOGGER.error("Entity %s does not have a chromecast object! Attributes: %s", receiver, dir(entity))
                    else:
                        _LOGGER.error("media_player component not loaded in hass.data!")
                    return False
                    
                sent = await hass.async_add_executor_job(send_cast_message)
                if sent:
                    continue
                    
                # If custom app fails, fallback to HLS
                _LOGGER.info("Custom app failed on %s, falling back to camera.play_stream (HLS)", receiver)
                await hass.services.async_call("camera", "play_stream", {
                    "entity_id": camera_entity_id,
                    "media_player": receiver,
                    "format": "hls"
                }, blocking=False)
                continue
                    
            # Fallback for standard TVs (Samsung, Roku, etc.) or when no custom app id is defined
            _LOGGER.info("Casting VIDEO stream via play_media (mp4/ts) to %s (platform: %s)", receiver, integration_platform)
            await hass.services.async_call("media_player", "play_media", {
                "entity_id": receiver,
                "media_content_id": base_stream_url,
                "media_content_type": "video/mp4"
            }, blocking=False)

                
        if p := data.get("players", {}).get(source_player_name):
            p.async_write_ha_state()

    @staticmethod
    async def stop_cast(hass, config_entry, source_player_name, target_receivers):
        from ..const import DOMAIN
        data = hass.data[DOMAIN][config_entry.entry_id]
        
        # Expand target_receivers to include children
        group_mgr = data.get("group_mgr")
        if group_mgr:
            expanded_receivers = set(target_receivers)
            for r in target_receivers:
                children = group_mgr.groups.get(r, [])
                expanded_receivers.update(children)
            target_receivers = list(expanded_receivers)
            
        for receiver in target_receivers:
            if receiver in data.get("active_routes", {}):
                del data["active_routes"][receiver]
            
            await hass.services.async_call("media_player", "turn_off", {
                "entity_id": receiver
            }, blocking=False)
            
        if p := data.get("players", {}).get(source_player_name):
            p.async_write_ha_state()
