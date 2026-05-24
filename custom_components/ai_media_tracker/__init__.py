import base64
import json
import logging
import os
import io
import voluptuous as vol
import asyncio
from datetime import timedelta

# Official async Ollama engine client
from ollama import AsyncClient

from homeassistant.core import HomeAssistant, ServiceCall, Event
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util
from .artwork_manager import ArtworkManager
from homeassistant.helpers.event import async_track_utc_time_change
import hashlib

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.CAMERA]
DOMAIN = "ai_media_tracker"

BLANK_JPEG = b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xd2\xcf\xff\xd9'

PROCESS_IMAGE_SCHEMA = vol.Schema({
    vol.Required("image_path"): cv.string,
}, extra=vol.ALLOW_EXTRA)

def is_blank_or_black_image(image_bytes: bytes) -> bool:
    """Detect if an image is empty or consists of a solid dark/black frame."""
    if not image_bytes or image_bytes == BLANK_JPEG:
        return True
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        small_img = img.resize((8, 8)).convert("L")
        pixels = list(small_img.getdata())
        avg_brightness = sum(pixels) / len(pixels)
        if avg_brightness < 15:
            return True
    except Exception:
        pass
    return False

def is_likely_commercial(image_bytes: bytes) -> bool:
    """
    Detect if an image is likely a commercial based on high-entropy color distribution.
    Sports typically have dominant colors (green, wood, ice) while ads are visually noisy.
    """
    try:
        from PIL import Image, ImageStat
        img = Image.open(io.BytesIO(image_bytes))
        stat = ImageStat.Stat(img)
        
        # High standard deviation across RGB channels indicates high visual complexity/noise
        # typical of fast-paced, high-contrast advertisements.
        avg_stddev = sum(stat.stddev) / len(stat.stddev)
        
        _LOGGER.debug("Commercial entropy check: stddev=%f", avg_stddev)
        if avg_stddev > 85: # Threshold for 'high noise'
            return True
    except Exception:
        pass
    return False

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI Media Tracker from a config entry."""
    # Ensure the root domain space exists
    hass.data.setdefault(DOMAIN, {})
    
    # SAFE INITIALIZATION: Create the entry bucket first if it doesn't exist
    if entry.entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id] = {}

    # Now we can safely check for or create the artwork manager instance
    artwork_mgr = hass.data[DOMAIN][entry.entry_id].get("artwork_manager")
    if not artwork_mgr:
        artwork_mgr = ArtworkManager(hass, entry)

    # Re-verify the full storage schema structure is fully mapped out
    hass.data[DOMAIN][entry.entry_id].update({
        "players": hass.data[DOMAIN][entry.entry_id].get("players", {}),
        "cameras": hass.data[DOMAIN][entry.entry_id].get("cameras", {}),
        "metadata": hass.data[DOMAIN][entry.entry_id].get("metadata", {}),
        "artwork_manager": artwork_mgr
    })

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    tracked_media_players = []
    entity_to_player_name_map = {}
    last_trigger_times = {}
    _kickstarting_entities = set()
    _pending_settle_tasks = {}

    def get_device_offset(entity_id: str) -> int:
        """
        Generate a deterministic delay (0-120 seconds) based on the entity_id.
        This ensures the same device always uses the same offset.
        """
        hash_val = int(hashlib.md5(entity_id.encode()).hexdigest(), 16)
        return hash_val % 120 # 0 to 120 seconds offset

    async def trigger_vlm_analysis(entity_id: str):
        """Trigger the VLM analysis for a specific entity."""
        player_name = entity_to_player_name_map.get(entity_id)
        if not player_name:
            return

        cameras = hass.data[DOMAIN][entry.entry_id].get("cameras", {})
        cam_obj = cameras.get(player_name)

        if not cam_obj:
            return

        try:
            image_bytes = await cam_obj.async_camera_image()
            if image_bytes:
                await async_analyze_frame_internal(hass, entry, player_name, image_bytes)
        except Exception as ex:
            _LOGGER.error("Failed executing VLM analysis for %s: %s", player_name, ex)

    async def _periodic_vlm_scan(now):
        """
        The main listener that triggers the staggered analysis.
        """
        _LOGGER.info("Periodic VLM sync: Initiating staggered check at %s", now)

        async def _analyze_after_delay(entity_id, state):
            delay = get_device_offset(entity_id)
            _LOGGER.debug("Staggering analysis for %s by %d seconds", entity_id, delay)
            await asyncio.sleep(delay)
            
            # Re-check state after delay to ensure it's still playing
            current_state = hass.states.get(entity_id)
            if not current_state or current_state.state != "playing":
                return

            # Check if metadata is sufficient before triggering VLM
            if is_metadata_sufficient(current_state):
                _LOGGER.debug("Periodic check: Native metadata found for %s. Skipping VLM.", entity_id)
                await update_proxy_from_native(entity_id, current_state)
                return

            await trigger_vlm_analysis(entity_id)

        for entity_id in tracked_media_players:
            state = hass.states.get(entity_id)
            if state and state.state == "playing":
                # Fire a background task for each device so they don't block each other
                hass.async_create_task(_analyze_after_delay(entity_id, state))

    # --- Hooking it up in async_setup_entry ---
    # Trigger at :28 and :58 (2 minutes before the hour/half-hour)
    async_track_utc_time_change(hass, _periodic_vlm_scan, minute=[0, 30], second=0)

    async def _setup_event_listeners():
        entity_reg = er.async_get(hass)
        devices_list = entry.data.get("devices", [])
        
        for dev_config in devices_list:
            dev_id = dev_config.get("device_id")
            if not dev_id:
                continue

            child_entries = er.async_entries_for_device(entity_reg, dev_id)
            media_player_entity = next((e for e in child_entries if e.domain == "media_player"), None)

            if media_player_entity:
                entity_id = media_player_entity.entity_id
                tracked_media_players.append(entity_id)
                player_name = dev_config.get("name")
                entity_to_player_name_map[entity_id] = player_name
                _LOGGER.info("AI Media Tracker hooked device state listener for %s (%s)", player_name, entity_id)

        def is_app_silent(state) -> bool:
            """Check if the app is reporting no info or just its own name."""
            if not state:
                return True
            title = (state.attributes.get("media_title") or "").lower().strip()
            if not title:
                return True
            
            blacklisted_titles = {
                "unknown", "none", "home", "dashboard", "top charts", "tv", 
                "apple tv", "android tv", "google tv", "youtube",
                "netflix", "disney+", "disney plus", "hulu", "hbo max", "max", 
                "paramount+", "peacock", "prime video", "amazon", "vudu"
            }
            return title in blacklisted_titles

        def is_metadata_sufficient(state) -> bool:
            """Check if the native media player state already has enough info to skip VLM."""
            if not state:
                return False

            title = state.attributes.get("media_title")
            if not title:
                return False

            title_lower = title.lower().strip()

            # 1. SILENT CHECK: If it's a blacklisted title, it's not sufficient.
            if is_app_silent(state):
                return False

            # 2. SPORTS CHECK: 
            # Even if the title looks like a matchup (e.g., "Lakers vs Celtics"), 
            # it is NOT sufficient for our artwork engine. We want VLM to identify 
            # the specific sport and league so we can find the correct logos/colors.
            known_sports = {
                "baseball", "softball", "football", "basketball", "hockey", "soccer", 
                "cricket", "nascar", "f1", "golf", "tennis", "mma", "ufc", "boxing",
                "rugby", "volleyball", "lacrosse"
            }
            title_words = set(title_lower.split())
            is_sport = title_words.intersection(known_sports) or any(marker in title_lower for marker in [" vs ", " @ ", " v ", " at "])
            
            if is_sport:
                _LOGGER.info("Sports detected in metadata: '%s'. Triggering VLM for league/logo enrichment.", title)
                return False

            # 3. NATIVE METADATA (Movies/Shows):
            # If it's a long title (likely a movie or show) and not a sport, it's sufficient.
            if len(title) > 15:
                return True

            return True

        async def update_proxy_from_native(entity_id: str, state):
            """Update the proxy player directly from the source's native attributes."""
            player_name = entity_to_player_name_map.get(entity_id)
            if not player_name:
                return

            title = state.attributes.get("media_title")
            app_name = state.attributes.get("app_name") or state.attributes.get("source")

            _LOGGER.info("Using native metadata for %s: %s (App: %s)", player_name, title, app_name)

            artwork_manager = hass.data[DOMAIN][entry.entry_id]["artwork_manager"]
            resolved_poster_url = await artwork_manager.async_get_artwork(
                title, 
                channel_name=app_name
            )

            metadata_bucket = hass.data[DOMAIN][entry.entry_id]["metadata"]
            metadata_bucket[player_name] = {
                "title": title,
                "state": state.state,
                "entity_picture": resolved_poster_url,
                "extra_attributes": {
                    "app_name": app_name,
                    "native_metadata": True,
                    "vlm_analysis": False
                }
            }

            players = hass.data[DOMAIN][entry.entry_id].get("players", {})
            player_obj = players.get(player_name)
            if player_obj:
                player_obj.async_write_ha_state()

        async def kickstart_metadata(entity_id: str):
            """Perform a quick pause/play cycle to force OSD visibility for VLM."""
            if entity_id in _kickstarting_entities:
                return

            player_name = entity_to_player_name_map.get(entity_id)
            _LOGGER.info("Kick-starting metadata OSD for %s...", player_name)

            _kickstarting_entities.add(entity_id)
            try:
                # 1. Send Pause
                await hass.services.async_call(
                    "media_player", "media_pause", 
                    {"entity_id": entity_id}, 
                    blocking=True
                )

                # 2. Wait for OSD to animate in
                await asyncio.sleep(1.5)

                # 3. Capture the frame while OSD is visible
                cameras = hass.data[DOMAIN][entry.entry_id].get("cameras", {})
                cam_obj = cameras.get(player_name)
                if cam_obj:
                    image_bytes = await cam_obj.async_camera_image()
                    if image_bytes:
                        # Process via VLM
                        hass.async_create_task(
                            async_analyze_frame_internal(hass, entry, player_name, image_bytes)
                        )

                # 4. Resume Playback
                await hass.services.async_call(
                    "media_player", "media_play", 
                    {"entity_id": entity_id}, 
                    blocking=True
                )

            except Exception as ex:
                _LOGGER.error("Failed kick-starting metadata for %s: %s", player_name, ex)
            finally:
                # Give the play command a moment to propagate before clearing the guard
                await asyncio.sleep(2.0)
                _kickstarting_entities.discard(entity_id)
        async def _handle_streaming_state_change(event: Event) -> None:
            entity_id = event.data.get("entity_id")
            
            # GUARD: Ignore all state changes while this entity is currently undergoing a Kick-Start
            if entity_id in _kickstarting_entities:
                return

            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")

            if not new_state:
                return

            player_name = entity_to_player_name_map.get(entity_id)
            metadata_bucket = hass.data[DOMAIN][entry.entry_id]["metadata"]
            current_meta = metadata_bucket.get(player_name, {})
            current_state = current_meta.get("state", "playing")

            if new_state.state in ("off", "standby", "unavailable"):
                if current_state != "idle":
                    _LOGGER.info("Device event: %s changed to %s. Moving proxy to idle.", player_name, new_state.state)
                    metadata_bucket[player_name] = {
                        "title": "System Standby",
                        "state": "idle",
                        "entity_picture": None,
                        "extra_attributes": {}
                    }
                    players = hass.data[DOMAIN][entry.entry_id].get("players", {})
                    player_obj = players.get(player_name)
                    if player_obj:
                        player_obj.async_write_ha_state()
                return

            if new_state.state == "paused" and old_state and old_state.state == "paused":
                return

            state_changed_to_playing = (new_state.state == "playing" and (not old_state or old_state.state != "playing"))
            is_resume_from_pause = (new_state.state == "playing" and old_state and old_state.state == "paused")
            
            old_app = old_state.attributes.get("app_name") if old_state else None
            new_app = new_state.attributes.get("app_name")
            app_switched = (new_app is not None and new_app != old_app)

            old_title = old_state.attributes.get("media_title") if old_state else None
            new_title = new_state.attributes.get("media_title")
            metadata_changed = (new_title is not None and new_title != old_title)

            if state_changed_to_playing or app_switched or metadata_changed:
                now = dt_util.utcnow()
                last_run = last_trigger_times.get(entity_id)

                # DEBOUNCE LOGIC
                if last_run and (now - last_run) < timedelta(seconds=8):
                    if not (state_changed_to_playing or app_switched):
                        _LOGGER.debug("Suppressing rapid remote control telemetry click variance on %s", player_name)
                        return

                last_trigger_times[entity_id] = now
                
                # CANCEL PENDING TASKS: If an analysis timer is already running for this entity, 
                # cancel it. We only want the *latest* state change to drive the analysis.
                if entity_id in _pending_settle_tasks:
                    _pending_settle_tasks[entity_id].cancel()
                
                # Diagnostic logging for trigger state
                _LOGGER.debug(
                    "Analysis Trigger [%s]: %s -> %s | resume=%s, app_switch=%s, meta_change=%s",
                    player_name, 
                    old_state.state if old_state else "None", 
                    new_state.state, 
                    is_resume_from_pause, 
                    app_switched, 
                    metadata_changed
                )
                
                # --- METADATA FIRST CHECK ---
                if is_metadata_sufficient(new_state):
                    _LOGGER.info("Metadata update detected for %s. Updating proxy.", player_name)
                    hass.async_create_task(update_proxy_from_native(entity_id, new_state))
                    return

                # --- METADATA SETTLING DELAY ---
                async def _settle_and_trigger():
                    try:
                        _LOGGER.debug("Metadata insufficient for %s. Waiting 2.5s for EPG/Metadata to settle...", player_name)
                        await asyncio.sleep(2.5)
                        
                        # GUARD: If a kickstart was launched by another process while we slept, abort.
                        if entity_id in _kickstarting_entities:
                            return

                        fresh_state = hass.states.get(entity_id)
                        if not fresh_state or fresh_state.state != "playing":
                            return

                        if is_metadata_sufficient(fresh_state):
                            _LOGGER.info("Metadata settled for %s. Bypassing VLM.", player_name)
                            await update_proxy_from_native(entity_id, fresh_state)
                            return

                        has_existing_title = current_meta.get("title") not in (None, "Unknown", "System Standby", "")
                        silent = is_app_silent(fresh_state)
                        
                        if not is_resume_from_pause and silent and (app_switched or current_state == "idle" or metadata_changed or not has_existing_title):
                            _LOGGER.info("Truly silent app detected on %s. Initiating Kick-Start...", player_name)
                            await kickstart_metadata(entity_id)
                        else:
                            _LOGGER.info("Enriching metadata via standard VLM for %s (Silent=%s, AppSwitch=%s).", 
                                         player_name, silent, app_switched)
                            await trigger_vlm_analysis(entity_id)
                    except asyncio.CancelledError:
                        _LOGGER.debug("Settling task for %s was superseded by a newer event. Cancelling.", player_name)
                    finally:
                        _pending_settle_tasks.pop(entity_id, None)

                # Store the task so it can be cancelled if another event arrives
                _pending_settle_tasks[entity_id] = hass.async_create_task(_settle_and_trigger())
        if tracked_media_players:
            entry.async_on_unload(
                async_track_state_change_event(hass, tracked_media_players, _handle_streaming_state_change)
            )

    hass.async_create_task(_setup_event_listeners())

    if not hass.services.has_service(DOMAIN, "process_test_image"):
        async def handle_process_test_image(call: ServiceCall):
            target_entities = call.data.get("entity_id", [])
            if not target_entities:
                return
            
            target_entity_id = target_entities[0] if isinstance(target_entities, list) else target_entities
            image_path = call.data["image_path"]

            if not await hass.async_add_executor_job(os.path.exists, image_path):
                return

            target_player_name = None
            target_entry_id = None
            
            for entry_id, storage_bucket in hass.data[DOMAIN].items():
                for p_name, p_obj in storage_bucket.get("players", {}).items():
                    if p_obj.entity_id == target_entity_id:
                        target_player_name = p_name
                        target_entry_id = entry_id
                        break
                if target_player_name:
                    break

            if not target_player_name:
                return

            config_entry = hass.config_entries.async_get_entry(target_entry_id)
            if not config_entry:
                return

            def _read_image():
                with open(image_path, "rb") as f:
                    return f.read()
            
            image_bytes = await hass.async_add_executor_job(_read_image)
            await async_analyze_frame_internal(hass, config_entry, target_player_name, image_bytes)

        hass.services.async_register(DOMAIN, "process_test_image", handle_process_test_image, schema=PROCESS_IMAGE_SCHEMA)
    
        # Extract the connection configuration from the entry
        ollama_url = entry.data.get("ollama_url") or entry.options.get("ollama_url")
        model_name = entry.data.get("model_name") or entry.options.get("model_name")

        # Set up your platforms, local services, etc.
        # ...

        # Fire the primer call in the background using the extracted strings
        if ollama_url and model_name:
            entry.async_create_background_task(
                hass,
                async_preload_ollama_model(hass, ollama_url, model_name),
                "ollama-model-preload"
            )

    return True

async def async_preload_ollama_model(hass: HomeAssistant, ollama_url: str, model_name: str):
    """Warm up the Ollama server by pre-loading the selected model into VRAM."""
    session = async_get_clientsession(hass)
    endpoint_url = f"{ollama_url.rstrip('/')}/api/generate"
    
    payload = {
        "model": str(model_name),
        "prompt": "",  # Empty prompt tells Ollama to just load the model
        "stream": False
    }
    
    _LOGGER.info("Pre-loading Ollama model into VRAM: %s", model_name)
    try:
        # Give it a generous timeout since VRAM allocation can take a moment
        async with asyncio.timeout(30):
            async with session.post(endpoint_url, json=payload) as response:
                if response.status == 200:
                    _LOGGER.info("Ollama model %s successfully loaded into VRAM.", model_name)
                else:
                    _LOGGER.warning("Ollama primer call returned status: %d", response.status)
    except Exception as err:
        _LOGGER.warning("Failed to pre-load Ollama model on startup: %s", err)

async def async_analyze_frame_internal(hass: HomeAssistant, entry: ConfigEntry, player_name: str, image_bytes: bytes):
    """Core VLM visual analysis using direct raw HTTP posting to bypass library serialization bugs."""
    artwork_manager = hass.data[DOMAIN][entry.entry_id]["artwork_manager"]
    
    ollama_url = entry.options.get("ollama_url", entry.data.get("ollama_url", "http://localhost:11434"))
    model_name = entry.options.get("model_name", entry.data.get("model_name", "qwen3-vl:2b"))

    if not image_bytes:
        _LOGGER.error("Camera image capture returned an empty binary array for %s.", player_name)
        return

    if is_blank_or_black_image(image_bytes):
        _LOGGER.debug("Blank or black frame detected for %s. Setting state to idle.", player_name)
        metadata_bucket = hass.data[DOMAIN][entry.entry_id]["metadata"]
        metadata_bucket[player_name] = {
            "title": "System Standby",
            "state": "idle",
            "entity_picture": None,
            "extra_attributes": {}
        }
        
        players = hass.data[DOMAIN][entry.entry_id].get("players", {})
        player_obj = players.get(player_name)
        if player_obj:
            player_obj.async_write_ha_state()
        return

    if is_likely_commercial(image_bytes):
        _LOGGER.info("High-entropy frame detected for %s. Likely a commercial break. Skipping VLM round-trip.", player_name)
        metadata_bucket = hass.data[DOMAIN][entry.entry_id]["metadata"]
        current_meta = metadata_bucket.get(player_name, {})
        metadata_bucket[player_name] = {
            "title": current_meta.get("title", "Commercial Break"),
            "state": "playing",
            "entity_picture": current_meta.get("entity_picture"),
            "extra_attributes": {
                **current_meta.get("extra_attributes", {}),
                "is_commercial": True
            }
        }
        players = hass.data[DOMAIN][entry.entry_id].get("players", {})
        player_obj = players.get(player_name)
        if player_obj:
            player_obj.async_write_ha_state()
        return

    _LOGGER.info("Starting raw HTTP VLM round-trip for %s using %s", player_name, model_name)

    try:
        from PIL import Image
        
        def optimize_image_for_vlm(raw_bytes: bytes):
            try:
                img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
                w, h = img.size
                
                # 1. Full Frame Context (Resized to fit top of composite)
                full_frame = img.resize((1024, int(1024 * (h/w))), Image.Resampling.LANCZOS)
                
                # 2. Detail Strips (Capturing logos/bugs at higher relative resolution)
                # We take the top 15% and bottom 15% of the original image
                top_strip = img.crop((0, 0, w, int(h * 0.15)))
                bottom_strip = img.crop((0, int(h * 0.85), w, h))
                
                # Resize strips to match the width of our context frame
                top_strip = top_strip.resize((1024, int(1024 * (top_strip.height / top_strip.width))), Image.Resampling.LANCZOS)
                bottom_strip = bottom_strip.resize((1024, int(1024 * (bottom_strip.height / bottom_strip.width))), Image.Resampling.LANCZOS)

                # 3. Create Vertical Composite
                # Total height = Full Frame + Top Detail + Bottom Detail
                total_h = full_frame.height + top_strip.height + bottom_strip.height
                composite = Image.new("RGB", (1024, total_h))
                
                composite.paste(full_frame, (0, 0))
                composite.paste(top_strip, (0, full_frame.height))
                composite.paste(bottom_strip, (0, full_frame.height + top_strip.height))

                output = io.BytesIO()
                composite.save(output, format="JPEG", quality=80)
                output.seek(0)
                return output.getvalue()
            except Exception as image_err:
                _LOGGER.error("Pillow optimization failed: %s", image_err)
                return raw_bytes

        optimized_bytes = await hass.async_add_executor_job(optimize_image_for_vlm, image_bytes)

        # Explicitly encode the image bytes to a clean base64 string
        b64_image = base64.b64encode(optimized_bytes).decode("utf-8")

        raw_prompt_template = """<image>
Analyze this image, which is a vertical composite of a TV screen:
- The TOP portion is the FULL FRAME for overall scene context.
- The MIDDLE and BOTTOM portions are high-resolution DETAIL STRIPS of the top and bottom margins (where logos, score bugs, and tickers live). Ignore tickers and upcoming content promotions.

Identify the media content (Movie, Show, or Sport) and the channel or platform. If it is a sports event, identify the teams and league. Ignore tickers and scrolls.

Output ONLY a JSON object that strictly follows this schema. Do not include markdown code blocks or conversational text.

Example format:
{
  "title": "Lakers vs Celtics",
  "extra_attributes": {
    "content_type": "sports",
    "channel_platform_name": "ESPN",
    "sport": "basketball",
    "league": "nba",
    "news_segment": "weather report",
    "episode": "S5 E6 Through the Heavens Fall",
    "is_commercial": "boolean: true if this frame appears to be an advertisement"
  }
}

JSON Output:"""

        # Perform explicit string swapping instead of dynamic evaluation blocks
        prompt = raw_prompt_template.replace("MODEL_NAME_PLACEHOLDER", str(model_name))

        # Construct the exact JSON payload the Ollama Go server expects natively
        payload = {
            "model": str(model_name),
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
            "thinking": False,
            "format": "json",  # Enforce structural grammar syntax tracking
            "options": {
                "temperature": 0.0,
                "num_ctx": 256000,
                "num_predict": 12000
            }
        }

        # Use Home Assistant's native aiohttp client session helper
        session = async_get_clientsession(hass)
        endpoint_url = f"{ollama_url.rstrip('/')}/api/generate"

        async with asyncio.timeout(60):
            async with session.post(endpoint_url, json=payload) as response:
                if response.status != 200:
                    _LOGGER.error("Ollama server returned bad HTTP status code: %d", response.status)
                    return
                    
                result_json = await response.json()
                
                # Extract standard response text
                raw_text = result_json.get("response", "").strip()
                
                # FALLBACK: If standard response is empty, check if the model wrote the JSON inside the thinking block
                if not raw_text and "thinking" in result_json:
                    _LOGGER.debug("Ollama standard response empty; falling back to thinking block data.")
                    raw_text = result_json.get("thinking", "").strip()
                
                # CRITICAL DIAGNOSTIC FALLBACK: Trace the engine map if *both* are completely missing
                if not raw_text:
                    _LOGGER.warning("Ollama API hit returned absolutely no text data in response or thinking. Full payload: %s", json.dumps(result_json))

        if not raw_text:
            return

        # Clean up JSON wrappers if the model injected them despite instructions
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
        elif "{" in raw_text:
            raw_text = raw_text[raw_text.find("{"):raw_text.rfind("}") + 1].strip()

        parsed_payload = json.loads(raw_text)
        if not parsed_payload:
            return
        title_detected = parsed_payload.get("title", "Unknown")
        extra_attrs = parsed_payload.get("extra_attributes", {})
        
        sport = extra_attrs.get("sport", "")
        league = extra_attrs.get("league", "")
        app_name = extra_attrs.get("app_name") or extra_attrs.get("channel_platform_name", "")
        _LOGGER.debug("VLM detected channel or platform as: %s", app_name or "")

        # Provide safe string defaults if any variables are None
        safe_title = str(title_detected or "Unknown")
        safe_app = str(app_name or "")
        safe_sport = str(sport or "")
        safe_league = str(league or "")

        _LOGGER.debug("Requesting artwork for title='%s', channel='%s'", safe_title, safe_app)
        assets = await artwork_manager.async_get_artwork(
            safe_title, 
            channel_name=safe_app, 
            sport=safe_sport, 
            league=safe_league
        )
        _LOGGER.debug("Artwork resolved to assets: %s", assets)

        state_to_set = "playing"
        cameras = hass.data[DOMAIN][entry.entry_id].get("cameras", {})
        cam_obj = cameras.get(player_name)
        if cam_obj:
            device_id = cam_obj._dev_config.get("device_id")
            if device_id:
                entity_reg = er.async_get(hass)
                entries = er.async_entries_for_device(entity_reg, device_id)
                parent_media_player = next((e.entity_id for e in entries if e.domain == "media_player"), None)
                if parent_media_player:
                    p_state = hass.states.get(parent_media_player)
                    if p_state and p_state.state == "paused":
                        state_to_set = "paused"

        metadata_bucket = hass.data[DOMAIN][entry.entry_id]["metadata"]
        metadata_bucket[player_name] = {
            "title": title_detected,
            "state": state_to_set,
            "entity_picture": assets.get("poster"),
            "extra_attributes": {
                **extra_attrs,
                "logo_url": assets.get("logo")
            }
        }

        players = hass.data[DOMAIN][entry.entry_id].get("players", {})
        player_obj = players.get(player_name)
        if player_obj:
            player_obj.async_write_ha_state()
            _LOGGER.info("Successfully pushed VLM metrics downstream to %s", player_obj.entity_id)

    except json.JSONDecodeError as jde:
        _LOGGER.error("Model failed to output parseable JSON format. Error: %s. Raw response text was: %s", jde, raw_text)
    except Exception as exc:
        _LOGGER.exception("Critical error processing frame via raw HTTP handler: %s", exc)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and sweep memory footprints."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN] and hass.services.has_service(DOMAIN, "process_test_image"):
            hass.services.async_remove(DOMAIN, "process_test_image")
            
    return unload_ok