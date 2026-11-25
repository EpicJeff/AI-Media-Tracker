import logging
import asyncio
import hashlib
import json
import base64
import re
from collections import Counter, deque
from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, Event, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, STATE_PLAYING, STATE_ON
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

# Local Imports
from .const import DOMAIN
from .artwork_manager import ArtworkManager
from .managers.metadata_manager import MetadataManager
from .managers.analysis_manager import AnalysisManager
from .managers.sports_manager import SportsManager
from .managers.metadata_hydrator import MetadataHydrator
from .managers.guide_manager import GuideManager
from .managers.guardrail_manager import GuardrailManager
from .pipeline.resolver import resolve_media
from .pipeline.native_stage import extract_native_state
from .pipeline.vlm_stage import optimize_image_for_vlm, validate_vlm_result
from .vlm_client import async_build_client

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.CAMERA, Platform.SENSOR]

def is_sport_title(title: str) -> bool:
    """Helper to quickly block TMDB/Guide checks if the title implies a sporting event."""
    if not title: return False
    title_lower = title.lower().strip()
    known_sports = {"baseball", "softball", "football", "basketball", "hockey", "soccer", "cricket", "nascar", "f1", "golf", "tennis", "mma", "ufc", "boxing", "rugby", "volleyball", "lacrosse"}
    known_leagues = {"mlb", "nba", "nfl", "nhl", "mls", "premier league", "champions league", "atp", "wta", "ncaa", "wnba", "pga", "lpga", "fifa", "fa cup"}
    title_words = set(title_lower.split())
    return bool(title_words.intersection(known_sports) or title_words.intersection(known_leagues) or any(marker in title_lower for marker in [" vs ", " @ ", " at "]))

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI Media Tracker integration with full orchestration logic."""
    hass.data.setdefault(DOMAIN, {})
    
    meta_mgr = MetadataManager(hass)
    anal_mgr = AnalysisManager()
    artwork_mgr = ArtworkManager(hass, entry)
    sports_mgr = SportsManager(hass, entry.entry_id)
    gracenote_key = entry.options.get("gracenote_api_key", entry.data.get("gracenote_api_key"))
    guide_mgr = GuideManager(hass, gracenote_key)
    
    tmdb_key = entry.options.get("tmdb_api_key", entry.data.get("tmdb_api_key"))
    logo_key = entry.options.get("logo_dev_api_key", entry.data.get("logo_dev_api_key"))
    meta_hydrator = MetadataHydrator(hass, tmdb_key)
    guardrail_mgr = GuardrailManager(hass, entry)
    
    from .managers.routing_manager import AudioProxyView, VideoProxyView, WebSocketProxyView, ActiveCastProxyView, RoutingManager
    hass.http.register_view(AudioProxyView(hass))
    hass.http.register_view(VideoProxyView(hass))
    hass.http.register_view(WebSocketProxyView(hass))
    hass.http.register_view(ActiveCastProxyView(hass))
    
    from .managers.storage_manager import StorageManager
    storage_mgr = StorageManager(hass)
    await storage_mgr.async_load()
    
    from .managers.notification_manager import NotificationManager
    notif_mgr = NotificationManager(hass)
    await notif_mgr.async_load()
    
    from .managers.group_manager import GroupManager
    group_mgr = GroupManager(hass)
    await group_mgr.async_load()

    vlm_client = await async_build_client(hass, entry)

    hass.data[DOMAIN][entry.entry_id] = {
        "vlm_client": vlm_client,
        "metadata_mgr": meta_mgr,
        "storage_mgr": storage_mgr,
        "analysis_mgr": anal_mgr,
        "artwork_mgr": artwork_mgr,
        "sports_mgr": sports_mgr,
        "meta_hydrator": meta_hydrator,
        "guide_mgr": guide_mgr,
        "guardrail_mgr": guardrail_mgr,
        "notif_mgr": notif_mgr,
        "group_mgr": group_mgr,
        "players": {},       
        "active_routes": {},
        "cameras": {},       
        "player_to_source": {}, 
        "unsub_listeners": [],
        "watchdogs": {},
        "pending_settle_tasks": {},
        "pending_state_handlers": {}
    }

    _kickstarting_entities = set()
    _active_watchdogs = hass.data[DOMAIN][entry.entry_id]["watchdogs"]
    _pending_settle_tasks = hass.data[DOMAIN][entry.entry_id]["pending_settle_tasks"]
    _pending_state_handlers = hass.data[DOMAIN][entry.entry_id]["pending_state_handlers"]
    last_trigger_times = {}

    ent_reg = er.async_get(hass)
    tracked_media_players = []
    entity_to_player_name_map = {}
    player_to_source = {}

    configured_devices = entry.data.get("devices", [])
    for dev_conf in configured_devices:
        dev_id = dev_conf.get("device_id")
        if not dev_id: continue

        child_entries = er.async_entries_for_device(ent_reg, dev_id)
        media_player_entity = next((e for e in child_entries if e.domain == "media_player"), None)

        if media_player_entity:
            entity_id = media_player_entity.entity_id
            tracked_media_players.append(entity_id)
            player_name = dev_conf.get("name")
            entity_to_player_name_map[entity_id] = player_name
            player_to_source[player_name] = entity_id
            _LOGGER.info("AI Media Tracker hooked device: %s", player_name)

    hass.data[DOMAIN][entry.entry_id]["player_to_source"] = player_to_source
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def is_app_silent(state) -> bool:
        if not state: return True
        title = (state.attributes.get("media_title") or "").lower().strip()
        series_title = (state.attributes.get("media_series_title") or "").lower().strip()
        blacklisted = {"unknown", "none", "home", "dashboard", "top charts", "tv", "apple tv", "android tv", "google tv", "youtube", "netflix", "disney+", "disney plus", "hulu", "hbo max", "max", "paramount+", "peacock", "prime video", "amazon", "vudu"}
        
        if series_title and series_title not in blacklisted:
            return False
            
        return not title or title in blacklisted



    def is_metadata_sufficient(state) -> bool:
        if not state: return False
        title = state.attributes.get("media_title")
        series_title = state.attributes.get("media_series_title")
        
        if not title and not series_title: return False

        check_title = series_title if series_title else title
        title_lower = check_title.lower().strip()
        
        if is_app_silent(state): return False

        live_tv_apps = {"directv", "directv stream", "youtube tv", "hulu", "fubo", "sling"}
        app_name = (state.attributes.get("app_name") or state.attributes.get("source") or "").lower().strip()
        if any(live_app in app_name for live_app in live_tv_apps):
            return False
            
        svod_apps_missing_series = {"hbo max", "max", "peacock"}
        if any(svod in app_name for svod in svod_apps_missing_series) and not series_title:
            return False

        known_sports = {"baseball", "softball", "football", "basketball", "hockey", "soccer", "cricket", "nascar", "f1", "golf", "tennis", "mma", "ufc", "boxing", "rugby", "volleyball", "lacrosse"}
        title_words = set(title_lower.split())
        is_sport = title_words.intersection(known_sports) or any(marker in title_lower for marker in [" vs ", " @ ", " v ", " at "])
        
        if is_sport: return False
        if len(check_title) > 15: return True
        return True

    async def enforce_guardrails(player_name, title, series_title, resolved_data_dict):
        guardrail_mgr = hass.data[DOMAIN][entry.entry_id].get("guardrail_mgr")
        if not guardrail_mgr: return None
        
        violation_tag = guardrail_mgr.evaluate(player_name, resolved_data_dict)
        if violation_tag:
            _LOGGER.warning("Content Guardrail Violation for %s! Tag: %s", player_name, violation_tag)
            source_entity_id = hass.data[DOMAIN][entry.entry_id].get("player_to_source", {}).get(player_name)
            if source_entity_id:
                hass.async_create_task(
                    hass.services.async_call("media_player", "media_pause", {"entity_id": source_entity_id}, blocking=False)
                )
            
            notif_mgr = hass.data[DOMAIN][entry.entry_id].get("notif_mgr")
            if notif_mgr:
                msg_title = series_title if series_title else title
                await notif_mgr.async_create_notification(
                    title=f"Blocked: {msg_title}",
                    message=f"A show matching the restricted '{violation_tag}' criteria started playing on {player_name}.",
                    notif_type="violation",
                    image_url=resolved_data_dict.get("entity_picture"),
                    player_name=player_name
                )
            
            hass.bus.async_fire("ai_media_tracker_guardrail_violation", {
                "player_name": player_name,
                "entity_id": source_entity_id,
                "violation_tag": violation_tag,
                "title": title,
                "series_title": series_title
            })
            return violation_tag
        return None

    async def update_proxy_from_native(entity_id: str, state):
        player_name = entity_to_player_name_map.get(entity_id)
        if not player_name: return

        original_title = state.attributes.get("media_title")
        series_title = state.attributes.get("media_series_title")
        
        search_title = series_title if series_title else original_title

        app_name = state.attributes.get("app_name") or state.attributes.get("source")
        app_name_lower = (app_name or "").lower().strip()

        sports_mgr.stop_tracking(player_name)
        sports_mgr.clear_sports_data(player_name)

        # 1. Deterministic EPG TV Guide Cross-Reference
        resolved_channel = app_name
        candidate_networks = await guide_mgr.get_candidate_channels(search_title)
        if candidate_networks:
            resolved_channel = candidate_networks[0]
            _LOGGER.info("EPG matched program '%s' directly with network: %s", search_title, resolved_channel)

        is_missing_series = (app_name_lower in {"hbo max", "max"} and not series_title)
        rich_metadata = {}

        # PREVENT UNNECESSARY SHOW DATA PULLS FOR SPORTS OR INCOMPLETE SVOD METADATA
        if not is_missing_series and search_title not in ("Browsing...", "Unknown"):
            if not is_sport_title(search_title):
                # 1. Deterministic TV Guide Cross-Referencing Match
                candidate_networks = await guide_mgr.get_candidate_channels(search_title)
                if candidate_networks:
                    resolved_channel = candidate_networks[0]
                    _LOGGER.info("EPG matched program '%s' directly with network: %s", search_title, resolved_channel)

                # 2. TMDB Hydration
                rich_metadata = await meta_hydrator.hydrate(search_title)
            else:
                _LOGGER.debug("Skipping EPG and TMDB fetch for suspected sports broadcast: '%s'", search_title)
                
        # Always fetch artwork to ensure logos load, but drop title if missing series to avoid bad poster grabs
        safe_title = "" if is_missing_series or search_title in ("Browsing...", "Unknown") else search_title
        assets = await artwork_mgr.async_get_artwork(safe_title, channel_name=resolved_channel)

        meta_mgr.merge(player_name, {
            "title": original_title, 
            "native_title": original_title,
            "series_title": series_title,
            "state": state.state, 
            "entity_picture": assets.get("poster"), 
            "logo_url": assets.get("logo"),
            "extra_attributes": {
                "app_name": app_name, 
                "channel_platform_name": resolved_channel,
                "native_metadata": True, 
                "vlm_analysis": False,
                "show_info": rich_metadata
            }
        })
        
        current_data = meta_mgr.get(player_name).__dict__
        tag = await enforce_guardrails(player_name, original_title, series_title, current_data)
        if tag:
            meta_mgr.merge(player_name, {
                "is_content_blocked": True,
                "content_violation_tag": tag
            })
        else:
            meta_mgr.merge(player_name, {
                "is_content_blocked": False,
                "content_violation_tag": None
            })
        
        if player_obj := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name):
            player_obj.async_write_ha_state()

    def _start_watchdog_loop(entity_id: str, player_name: str, force_full_parse: bool = False):
        if entity_id in _active_watchdogs:
            _active_watchdogs[entity_id].cancel()
            
        cam = hass.data[DOMAIN][entry.entry_id].get("cameras", {}).get(player_name)
        if not cam: return
        
        _active_watchdogs[entity_id] = hass.async_create_task(
            async_watchdog_loop(hass, entry, entity_id, player_name, cam, force_full_parse)
        )

    async def kickstart_metadata(entity_id: str):
        if entity_id in _kickstarting_entities: return
        player_name = entity_to_player_name_map.get(entity_id)
        _kickstarting_entities.add(entity_id)
        
        try:
            await hass.services.async_call("media_player", "media_pause", {"entity_id": entity_id}, blocking=True)
            await asyncio.sleep(1.0)
            _start_watchdog_loop(entity_id, player_name)
            await hass.services.async_call("media_player", "media_play", {"entity_id": entity_id}, blocking=True)
        except Exception as ex:
            _LOGGER.error("Failed kickstart for %s: %s", player_name, ex)
        finally:
            async def _cleanup():
                await asyncio.sleep(2.0)
                _kickstarting_entities.discard(entity_id)
            hass.async_create_task(_cleanup())

    @callback
    def _handle_streaming_state_change(event: Event) -> None:
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if not new_state: return

        player_name = entity_to_player_name_map.get(entity_id)
        if not player_name: return

        app_name_attr = new_state.attributes.get("app_name")
        display_app = app_name_attr
        app_name = display_app
        
        _LOGGER.warning("MASTER DEBUG: Received event for %s. State: %s, HA Native App: %s", player_name, new_state.state, app_name_attr)

        current_meta = meta_mgr.get(player_name)
        current_state = current_meta.state

        new_duration = new_state.attributes.get("media_duration")

        async def _async_resolve_browsing_state():
            true_app_name = None
            query_success = False
            try:
                from homeassistant.helpers import entity_registry as er
                registry = er.async_get(hass)
                entity = registry.async_get(entity_id)
                if not entity: _LOGGER.info("PyATV Debug: Entity not found")
                elif not entity.config_entry_id: _LOGGER.info("PyATV Debug: config_entry_id missing")
                else:
                    atv = None
                    # Method 1: New HA ConfigEntry runtime_data
                    config_entry = hass.config_entries.async_get_entry(entity.config_entry_id)
                    if config_entry:
                        manager = getattr(config_entry, "runtime_data", None)
                        if manager: atv = getattr(manager, "atv", None)
                        
                    # Method 2: Entity Component extraction
                    if not atv:
                        component = hass.data.get("entity_components", {}).get("media_player")
                        if component:
                            entity_obj = component.get_entity(entity_id)
                            if entity_obj:
                                manager = getattr(entity_obj, "manager", None)
                                if manager: atv = getattr(manager, "atv", None)
                                
                    if atv:
                        app = atv.metadata.app
                        query_success = True
                        if app:
                            true_app_name = app.name
                            _LOGGER.info("PyATV Debug: pyatv returned app: %s id: %s", app.name, app.identifier)
                        else:
                            _LOGGER.info("PyATV Debug: pyatv returned None app!")
                    else:
                        _LOGGER.info("PyATV Debug: completely failed to locate the PyATV instance inside HA.")
            except Exception as e:
                _LOGGER.info("PyATV Debug: PyATV exception: %s", e)
            
            # If pyatv query succeeded, trust it completely (even if it returns None, meaning Home Screen).
            # Otherwise fallback to HA's display_app
            final_app = true_app_name if query_success else display_app
            
            # --- GHOST SESSION FILTER ---
            ghost_app_name = None
            if old_state and new_state.state in ("paused", "idle"):
                old_app = current_meta.app_name
                # If the app changed to a DIFFERENT valid app, it is almost certainly a ghost fallback.
                # We catch both 'paused' and 'idle' fallbacks.
                if old_app and final_app and final_app != old_app:
                    _LOGGER.warning("AI Media Tracker: Ghost session detected! Apple TV abruptly fell back to '%s' (%s). Forcing it to remain on '%s'.", final_app, new_state.state, old_app)
                    final_app = old_app
                    ghost_app_name = old_app
            # ----------------------------
            
            is_browsing = False
            
            if new_state.state in ("idle", "standby") and final_app:
                # If it's a silent app, it might just be buffering or paused. We don't want to instantly assume browsing.
                if not is_app_silent(new_state):
                    is_browsing = True
                
            if new_state.state == "paused" and final_app:
                # Silent apps never have duration, so we can't use 'not new_duration' to assume they are browsing.
                # We must let VLM run to figure out if it's browsing or a paused movie.
                if not is_app_silent(new_state) and not new_duration:
                    is_browsing = True
                
            if ghost_app_name:
                is_browsing = True
                
            if not final_app and new_state.state in ("idle", "paused", "standby"):
                is_browsing = True

            if old_state and old_state.state != new_state.state:
                if entity_id in _active_watchdogs and entity_id not in _kickstarting_entities:
                    _active_watchdogs[entity_id].cancel()
                    _active_watchdogs.pop(entity_id, None)
                    _LOGGER.info("AI Media Tracker: Adaptive Watchdog Loop for %s cancelled due to state change (%s -> %s).", player_name, old_state.state, new_state.state)
            if new_state.state in ("off", "standby", "unavailable") or is_browsing:
                if entity_id in _active_watchdogs:
                    _active_watchdogs[entity_id].cancel()
                    _active_watchdogs.pop(entity_id, None)
                    _LOGGER.info("AI Media Tracker: Adaptive Watchdog Loop for %s cancelled.", player_name)

                sports_mgr.stop_tracking(player_name)
                sports_mgr.clear_sports_data(player_name)

                if is_browsing:
                    meta_mgr.merge(player_name, {
                        "title": "Browsing...",
                        "series_title": None,
                        "state": "paused",
                        "entity_picture": None,
                        "is_content_blocked": False,
                        "content_violation_tag": None,
                        "extra_attributes": {
                            "app_name": final_app,
                            "channel_platform_name": final_app,
                            "native_metadata": False,
                            "vlm_analysis": False
                        }
                    })
                    
                    async def fetch_logo_and_update():
                        assets = await hass.data[DOMAIN][entry.entry_id]["artwork_mgr"].async_get_artwork("", channel_name=final_app)
                        if assets.get("logo"):
                            meta_mgr.merge(player_name, {"logo_url": assets.get("logo")})
                            if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name): p.async_write_ha_state()
                    hass.async_create_task(fetch_logo_and_update())
                    
                    if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name): p.async_write_ha_state()
                elif current_state != "idle":
                    meta_mgr.merge(player_name, {"title": "System Standby", "state": "idle", "entity_picture": None, "logo_url": None, "is_content_blocked": False, "content_violation_tag": None, "extra_attributes": {}})
                    if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name): p.async_write_ha_state()
                return True
            return False

        async def _async_handle_state_change():
            exited_early = await _async_resolve_browsing_state()
            if exited_early: return

            if new_state.state == "paused" and old_state and old_state.state == "paused": return

            state_changed_to_playing = (new_state.state == "playing" and (not old_state or old_state.state != "playing"))
            state_changed_to_paused_idle = (new_state.state in ("idle", "paused") and (not old_state or old_state.state not in ("idle", "paused")))
            is_resume_from_pause = (new_state.state == "playing" and old_state and old_state.state == "paused")
            old_app = old_state.attributes.get("app_name") if old_state else None
            new_app = new_state.attributes.get("app_name")
            app_switched = (new_app != old_app)

            old_title = old_state.attributes.get("media_title") if old_state else None
            new_title = new_state.attributes.get("media_title")
            metadata_changed = (new_title != old_title)

            old_duration = old_state.attributes.get("media_duration") if old_state else None
            new_duration = new_state.attributes.get("media_duration")
            duration_changed = (new_duration != old_duration)

            if is_resume_from_pause and not (app_switched or metadata_changed or duration_changed):
                return

            if state_changed_to_playing or app_switched or metadata_changed or duration_changed or state_changed_to_paused_idle:
                now = dt_util.utcnow()
                last_run = last_trigger_times.get(entity_id)

                if last_run and (now - last_run) < timedelta(seconds=8):
                    if not (state_changed_to_playing or app_switched or metadata_changed or duration_changed): return

                last_trigger_times[entity_id] = now
                
                is_only_duration_change = duration_changed and not (state_changed_to_playing or app_switched or metadata_changed or state_changed_to_paused_idle)
                if is_only_duration_change and player_name in sports_mgr._active_tasks:
                    return

                sports_mgr.stop_tracking(player_name)
                
                if not is_app_silent(new_state):
                     hass.async_create_task(update_proxy_from_native(entity_id, new_state))
                
                if entity_id in _pending_settle_tasks: _pending_settle_tasks[entity_id].cancel()
                if entity_id in _active_watchdogs:
                    _active_watchdogs[entity_id].cancel()
                    _active_watchdogs.pop(entity_id, None)
                
                app_name_new = (new_state.attributes.get("app_name") or new_state.attributes.get("source") or "").lower().strip()
                series_title_new = new_state.attributes.get("media_series_title")
                needs_vlm_kick_initial = is_app_silent(new_state) or (app_name_new in {"hbo max", "max"} and not series_title_new)
                
                if is_metadata_sufficient(new_state) and not needs_vlm_kick_initial:
                    return

                async def _settle_and_trigger(duration_changed_flag=False, metadata_changed_flag=False):
                    try:
                        await asyncio.sleep(2.0)
                        if entity_id in _kickstarting_entities: return

                        fresh_state = hass.states.get(entity_id)
                        # Process playing, paused, AND idle states so VLM can analyze menus and paused shows
                        if not fresh_state or fresh_state.state not in ("playing", "paused", "idle"): return

                        silent = is_app_silent(fresh_state)
                        
                        app_name_attr = fresh_state.attributes.get("app_name")
                        display_app = app_name_attr or "App"
                        app_name_fresh = display_app

                        if not silent:
                            await update_proxy_from_native(entity_id, fresh_state)
                        else:
                            has_rich_title = current_meta.title and current_meta.title not in ("Browsing...", "Loading Info...", "System Standby", "Unknown", "", "Home Screen")
                            
                            if (app_switched or state_changed_to_playing) and not has_rich_title:
                                meta_mgr.merge(player_name, {
                                    "title": "Loading Info...",
                                    "series_title": None,
                                    "state": fresh_state.state,
                                    "entity_picture": None,
                                    "is_content_blocked": False,
                                    "content_violation_tag": None,
                                    "extra_attributes": {
                                        "app_name": app_name_fresh,
                                        "channel_platform_name": app_name_fresh,
                                        "native_metadata": False,
                                        "vlm_analysis": False
                                    }
                                })
                                async def fetch_logo_and_update():
                                    assets = await hass.data[DOMAIN][entry.entry_id]["artwork_mgr"].async_get_artwork("", channel_name=app_name_fresh)
                                    if assets.get("logo"):
                                        meta_mgr.merge(player_name, {"logo_url": assets.get("logo")})
                                        if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name): p.async_write_ha_state()
                                hass.async_create_task(fetch_logo_and_update())
                                if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(player_name): p.async_write_ha_state()

                        has_existing_title = current_meta.title not in (None, "Unknown", "System Standby", "", "Browsing...", "Loading Info...", "Home Screen")
                        
                        app_name = (fresh_state.attributes.get("app_name") or fresh_state.attributes.get("source") or "").lower().strip()
                        series_title = fresh_state.attributes.get("media_series_title")
                        needs_vlm_kick = silent or (app_name in {"hbo max", "max"} and not series_title)
                        
                        if fresh_state.state == "playing" and not needs_vlm_kick and is_metadata_sufficient(fresh_state):
                            return

                        is_from_browsing = (current_meta.title == "Browsing...")
                        if fresh_state.state == "playing":
                            import time
                            recently_kickstarted = (time.time() - current_meta.last_kickstart) < 10.0
                            if not recently_kickstarted and needs_vlm_kick and (app_switched or current_state == "idle" or is_from_browsing or metadata_changed_flag or duration_changed_flag or not has_existing_title or not current_meta.vlm_analysis):
                                current_meta.last_kickstart = time.time()
                                meta_mgr.update(player_name, current_meta)
                                await kickstart_metadata(entity_id)
                            else:
                                _start_watchdog_loop(entity_id, player_name)
                        elif fresh_state.state in ("paused", "idle"):
                            cam_obj = hass.data[DOMAIN][entry.entry_id].get("cameras", {}).get(player_name)
                            if cam_obj:
                                img_bytes = await cam_obj.async_camera_image()
                                if img_bytes:
                                    has_prog = await _check_for_progress_bar(hass, entry, player_name, img_bytes)
                                    if has_prog:
                                        if current_meta.title in ("Browsing...", "Loading Info...", "Unknown", "", None):
                                            result = await _single_frame_analysis(hass, entry, player_name, img_bytes, False)
                                            if result and result.get("title") != "Browsing...":
                                                await _apply_consensus_to_ui(hass, entry, player_name, result)
                                    else:
                                        await _apply_consensus_to_ui(hass, entry, player_name, {"title": "Browsing..."})

                    except asyncio.CancelledError: pass
                    finally: _pending_settle_tasks.pop(entity_id, None)

                _pending_settle_tasks[entity_id] = hass.async_create_task(_settle_and_trigger(duration_changed_flag=duration_changed, metadata_changed_flag=metadata_changed))

        # Call the async handler we defined above
        if entity_id in _pending_state_handlers:
            _pending_state_handlers[entity_id].cancel()
        _pending_state_handlers[entity_id] = hass.async_create_task(_async_handle_state_change())



    if tracked_media_players:
        unsub = async_track_state_change_event(hass, tracked_media_players, _handle_streaming_state_change)
        hass.data[DOMAIN][entry.entry_id]["unsub_listeners"].append(unsub)
        
    async def _async_target_state_change_listener(event):
        new_state = event.data.get("new_state")
        if not new_state: return
        entity_id = event.data.get("entity_id")
        if not entity_id or not entity_id.startswith("media_player."): return
        
        active_routes = hass.data[DOMAIN][entry.entry_id].get("active_routes", {})
        if entity_id in active_routes:
            should_drop = False
            if new_state.state in ("off", "unavailable"):
                should_drop = True
                
            app_id = new_state.attributes.get("app_id")
            app_name = new_state.attributes.get("app_name")
            
            # CC1AD845 is the Default Media Receiver (used when fallback play_media runs)
            valid_app_ids = ["35101F1D", "CC1AD845", None] 
            valid_app_names = [
                "Default Media Receiver", 
                "AI Media Tracker", 
                "HA AV Over IP Receiver", 
                "Media Assistant Cast", 
                "Home Assistant", 
                "Roku Media Player"
            ]
            
            if app_id and app_id not in valid_app_ids:
                should_drop = True
            elif app_name and app_name not in valid_app_names:
                should_drop = True
                
            if should_drop:
                source_player = active_routes.pop(entity_id, None)
                if source_player:
                    _LOGGER.info("Target %s stopped casting from %s (state: %s, app_id: %s, app_name: %s)", entity_id, source_player, new_state.state, app_id, app_name)
                    if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(source_player):
                        p.async_write_ha_state()
            elif new_state.state in ("idle", "standby") and not app_id and not app_name:
                # If the device goes idle without an app_id or app_name, it might be returning to the home screen.
                # We wait 5 seconds to ensure it isn't just a transient buffering state during cast initialization.
                import asyncio
                async def _verify_idle_drop():
                    await asyncio.sleep(5)
                    current = hass.states.get(entity_id)
                    if current and current.state in ("idle", "standby") and not current.attributes.get("app_id") and not current.attributes.get("app_name"):
                        source_player = active_routes.pop(entity_id, None)
                        if source_player:
                            _LOGGER.info("Target %s stayed idle for 5s, assuming cast exited from %s", entity_id, source_player)
                            if p := hass.data[DOMAIN][entry.entry_id].get("players", {}).get(source_player):
                                p.async_write_ha_state()
                hass.async_create_task(_verify_idle_drop())

    unsub_target = hass.bus.async_listen("state_changed", _async_target_state_change_listener)
    hass.data[DOMAIN][entry.entry_id]["unsub_listeners"].append(unsub_target)

    # Warms the local model so the first frame does not pay a cold-load
    # penalty. A no-op when the Gemini path is the only one configured.
    entry.async_create_background_task(hass, vlm_client.preload(), "vlm-preload")

    async def handle_refresh_analysis(call):
        entity_id = call.data.get("entity_id")
        if not entity_id: return
        player_name = entity_to_player_name_map.get(entity_id)
        
        if not player_name:
            for name, proxy in hass.data[DOMAIN][entry.entry_id].get("players", {}).items():
                if proxy.entity_id == entity_id:
                    player_name = name
                    entity_id = hass.data[DOMAIN][entry.entry_id].get("player_to_source", {}).get(name)
                    break

        if player_name and entity_id:
            _LOGGER.info("Manual refresh requested for %s", player_name)
            hass.data[DOMAIN][entry.entry_id]["sports_mgr"].stop_tracking(player_name)
            _start_watchdog_loop(entity_id, player_name, force_full_parse=True)

    async def handle_follow_me(call):
        source_entity_id = call.data.get("entity_id")
        target_receivers = call.data.get("target_receivers", [])
        if not source_entity_id or not target_receivers: return
        if isinstance(source_entity_id, list): source_entity_id = source_entity_id[0]
        if isinstance(target_receivers, str): target_receivers = [target_receivers]
        
        player_name = entity_to_player_name_map.get(source_entity_id)
        if not player_name:
            for name, proxy in hass.data[DOMAIN][entry.entry_id].get("players", {}).items():
                if proxy.entity_id == source_entity_id:
                    player_name = name
                    break
                    
        if player_name:
            _LOGGER.info("Follow Me requested from %s to %s", player_name, target_receivers)
            from .managers.routing_manager import RoutingManager
            await RoutingManager.cast_stream(hass, entry, player_name, target_receivers)

    async def handle_stop_cast(call):
        source_entity_id = call.data.get("entity_id")
        target_receivers = call.data.get("target_receivers", [])
        if not source_entity_id or not target_receivers: return
        if isinstance(source_entity_id, list): source_entity_id = source_entity_id[0]
        if isinstance(target_receivers, str): target_receivers = [target_receivers]
        
        player_name = entity_to_player_name_map.get(source_entity_id)
        if not player_name:
            for name, proxy in hass.data[DOMAIN][entry.entry_id].get("players", {}).items():
                if proxy.entity_id == source_entity_id:
                    player_name = name
                    break
                    
        if player_name:
            _LOGGER.info("Stop Cast requested for %s to %s", player_name, target_receivers)
            from .managers.routing_manager import RoutingManager
            await RoutingManager.stop_cast(hass, entry, player_name, target_receivers)

    async def handle_create_notification(call):
        notif_mgr = hass.data[DOMAIN][entry.entry_id].get("notif_mgr")
        if notif_mgr:
            await notif_mgr.async_create_notification(
                title=call.data.get("title", "Notification"),
                message=call.data.get("message", ""),
                notif_type=call.data.get("type", "info"),
                image_url=call.data.get("image_url"),
                player_name=call.data.get("player_name")
            )

    async def handle_clear_notification(call):
        notif_mgr = hass.data[DOMAIN][entry.entry_id].get("notif_mgr")
        if notif_mgr:
            await notif_mgr.async_clear_notification(call.data.get("notification_id"))

    async def handle_mark_notifications_read(call):
        notif_mgr = hass.data[DOMAIN][entry.entry_id].get("notif_mgr")
        if notif_mgr:
            await notif_mgr.async_mark_read(call.data.get("notification_id"))

    async def handle_group_targets(call):
        group_mgr = hass.data[DOMAIN][entry.entry_id].get("group_mgr")
        if group_mgr:
            parent = call.data.get("parent_entity_id")
            children = call.data.get("child_entity_ids", [])
            if isinstance(children, str): children = [children]
            if parent and children:
                flattened_added = await group_mgr.async_group_targets(parent, children)
                for p in hass.data[DOMAIN][entry.entry_id].get("players", {}).values():
                    p.async_write_ha_state()
                
                # Automatically route stream if parent is active
                active_routes = hass.data[DOMAIN][entry.entry_id].get("active_routes", {})
                if parent in active_routes and flattened_added:
                    source_player = active_routes[parent]
                    await RoutingManager.cast_stream(hass, entry, source_player, flattened_added)

    async def handle_ungroup_targets(call):
        group_mgr = hass.data[DOMAIN][entry.entry_id].get("group_mgr")
        if group_mgr:
            children = call.data.get("child_entity_ids", [])
            if isinstance(children, str): children = [children]
            if children:
                # Find parents before ungrouping to know if we need to stop cast
                parents_to_stop = {}
                for child in children:
                    parent = group_mgr.get_parent_for_child(child)
                    if parent:
                        if parent not in parents_to_stop: parents_to_stop[parent] = []
                        parents_to_stop[parent].append(child)
                        
                await group_mgr.async_ungroup_targets(children)
                for p in hass.data[DOMAIN][entry.entry_id].get("players", {}).values():
                    p.async_write_ha_state()
                    
                # Automatically stop routing if parent was active
                active_routes = hass.data[DOMAIN][entry.entry_id].get("active_routes", {})
                for parent, parent_children in parents_to_stop.items():
                    if parent in active_routes:
                        source_player = active_routes[parent]
                        await RoutingManager.stop_cast(hass, entry, source_player, parent_children)

    hass.services.async_register(DOMAIN, "refresh_analysis", handle_refresh_analysis)
    hass.services.async_register(DOMAIN, "follow_me", handle_follow_me)
    hass.services.async_register(DOMAIN, "stop_cast", handle_stop_cast)
    hass.services.async_register(DOMAIN, "create_notification", handle_create_notification)
    hass.services.async_register(DOMAIN, "clear_notification", handle_clear_notification)
    hass.services.async_register(DOMAIN, "mark_notifications_read", handle_mark_notifications_read)
    hass.services.async_register(DOMAIN, "group_targets", handle_group_targets)
    hass.services.async_register(DOMAIN, "ungroup_targets", handle_ungroup_targets)

    return True

def _is_drastic_score_change(old_state: dict, new_state: dict) -> bool:
    if not old_state or not new_state: return False
    
    old_sets = old_state.get("tennis_sets")
    new_sets = new_state.get("tennis_sets")
    if isinstance(old_sets, list) and isinstance(new_sets, list):
        if len(new_sets) < len(old_sets): return True
        if old_sets and new_sets:
            old_last = old_sets[-1]
            new_last = new_sets[-1]
            if isinstance(old_last, dict) and isinstance(new_last, dict):
                old_total = old_last.get("competitor_a", 0) + old_last.get("competitor_b", 0)
                new_total = new_last.get("competitor_a", 0) + new_last.get("competitor_b", 0)
                if old_total - new_total >= 2:
                    return True

    old_period = old_state.get("period")
    new_period = new_state.get("period")
    if old_period and new_period and str(old_period).isdigit() and str(new_period).isdigit():
        if int(new_period) < int(old_period): return True
            
    return False

async def async_watchdog_loop(hass: HomeAssistant, entry: ConfigEntry, entity_id: str, player_name: str, cam_obj, force_full_parse_next: bool = False):
    _LOGGER.info("AI Media Tracker: Starting Adaptive Watchdog Loop for %s", player_name)
    rolling_cache = deque(maxlen=5)
    current_consensus_str = None
    polls_taken = 0
    fast_poll_limit = 3      
    fast_poll_interval = 30  
    slow_poll_interval = 120 

    try:
        while True:
            state = hass.states.get(entity_id)
            if not state or state.state in ("off", "standby", "unavailable", "idle"):
                hass.data[DOMAIN][entry.entry_id]["sports_mgr"].stop_tracking(player_name)
                break
                
            if state.state == "paused":
                await asyncio.sleep(2)
                continue

            img_bytes = await cam_obj.async_camera_image()
            if img_bytes:
                result = await _single_frame_analysis(hass, entry, player_name, img_bytes, force_full_parse_next)
                force_full_parse_next = False
                
                if result:
                    res_str = json.dumps(result, sort_keys=True)
                    rolling_cache.append(res_str)
                    
                    vote_counts = Counter(rolling_cache)
                    top_vote_str, count = vote_counts.most_common(1)[0]
                    
                    is_sport = bool(result.get("sports_info", {}).get("sport"))
                    
                    if top_vote_str != current_consensus_str and count >= (2 if len(rolling_cache) >= 3 and not is_sport else 1):
                        
                        if is_sport and current_consensus_str:
                            old_game_state = json.loads(current_consensus_str).get("sports_info", {}).get("game_state", {})
                            new_game_state = json.loads(top_vote_str).get("sports_info", {}).get("game_state", {})
                            if _is_drastic_score_change(old_game_state, new_game_state):
                                _LOGGER.info("Drastic score change detected for %s. Forcing full parse on next poll.", player_name)
                                force_full_parse_next = True
                        
                        _LOGGER.info("New consensus reached for %s: %s", player_name, top_vote_str)
                        current_consensus_str = top_vote_str
                        try:
                            await _apply_consensus_to_ui(hass, entry, player_name, json.loads(top_vote_str))
                        except Exception as e:
                            import traceback
                            _LOGGER.error("CRASH in _apply_consensus_to_ui for %s: %s\n%s", player_name, e, traceback.format_exc())
                        
            polls_taken += 1
            
            is_sport_consensus = False
            if current_consensus_str:
                try:
                    is_sport_consensus = bool(json.loads(current_consensus_str).get("sports_info", {}).get("sport"))
                except: pass

            if is_sport_consensus:
                await asyncio.sleep(20)
            else:
                await asyncio.sleep(fast_poll_interval if polls_taken < fast_poll_limit else slow_poll_interval)
                
    except asyncio.CancelledError:
        _LOGGER.info("Adaptive Watchdog Loop for %s cancelled.", player_name)

async def _apply_consensus_to_ui(hass, entry, player_name, vlm_result):
    data = hass.data[DOMAIN][entry.entry_id]
    source_entity_id = data.get("player_to_source", {}).get(player_name, f"media_player.{player_name.lower().replace(' ', '_')}")
    native_state_raw = hass.states.get(source_entity_id)
    native_state = extract_native_state(native_state_raw)
    
    if vlm_result and vlm_result.get("sports_info") and "show_info" not in vlm_result:
        player_obj = data.get("players", {}).get(player_name)
        if player_obj and player_obj.media_title:
            vlm_result["show_info"] = {"episode": [player_obj.media_title]}
            if player_obj.media_series_title:
                vlm_result["show_info"]["title"] = player_obj.media_series_title
    
    resolved_data = await resolve_media(native_state, vlm_result)
    if not resolved_data:
        resolved_data = {}
        
    if not resolved_data.get("title") and not resolved_data.get("series_title") and not resolved_data.get("sport"):
        # If VLM failed and there's no native title, clear "Loading Info..."
        resolved_data["title"] = "Unknown"

    if resolved_data.get("title") or resolved_data.get("series_title") or resolved_data.get("sport"):
        title = resolved_data.get("title")
        if title not in ("Unknown", "Browsing..."):
            if native_state_raw and native_state_raw.state == "paused":
                resolved_data["state"] = "paused"
            else:
                resolved_data["state"] = "playing"
        elif title == "Browsing...":
            resolved_data["state"] = "paused"
    elif "state" not in resolved_data and native_state_raw:
        resolved_data["state"] = native_state_raw.state

    if "extra_attributes" not in resolved_data:
        resolved_data["extra_attributes"] = {}

    is_sport = bool(resolved_data.get("sport") and resolved_data.get("league"))
    vlm_sports = resolved_data.get("extra_attributes", {}).get("sports_info", {})
    competitors = vlm_sports.get("competitors", [])

    if is_sport:
        data["sports_mgr"].start_tracking(
            player_name, 
            resolved_data.get("sport"), 
            resolved_data.get("league"), 
            resolved_data.get("title"),
            competitors,
            vlm_sports.get("game_state", {})
        )
    else:
        if data["sports_mgr"]._tracked_events.get(player_name):
            # We were tracking a sport, it ended (frozen), and the VLM no longer sees a scorebug
            # (e.g. post-game show on same channel). DO NOT clear the UI!
            resolved_data["title"] = data["players"].get(player_name).media_title or ""
            return
            
        data["sports_mgr"].stop_tracking(player_name)
        
        player_obj = data["players"].get(player_name)
        should_clear = True
        
        if player_obj:
            new_title = resolved_data.get("title", "")
            new_series = resolved_data.get("series_title", "")
            old_title = player_obj.media_title or ""
            old_series = player_obj.media_series_title or ""
            
            if new_title.lower() in ["commercial", "advertisement", "ad", "commercials"]:
                should_clear = False
            elif new_title == old_title and new_series == old_series:
                should_clear = False
                
        if should_clear:
            data["sports_mgr"].clear_sports_data(player_name)
        search_title = resolved_data.get("series_title") or resolved_data.get("title", "")
        rich_metadata = await data["meta_hydrator"].hydrate(search_title)
        if rich_metadata:
            if "show_info" not in resolved_data["extra_attributes"]:
                resolved_data["extra_attributes"]["show_info"] = {}
            for k, v in rich_metadata.items():
                resolved_data["extra_attributes"]["show_info"][k] = v
                
            if net_name := rich_metadata.get("network_name"):
                resolved_data["extra_attributes"]["network_name"] = net_name

    # Guardrail evaluation
    tag = await enforce_guardrails(player_name, resolved_data.get("title", ""), resolved_data.get("series_title", ""), resolved_data)
    if tag:
        resolved_data["is_content_blocked"] = True
        resolved_data["content_violation_tag"] = tag
    else:
        resolved_data["is_content_blocked"] = False
        resolved_data["content_violation_tag"] = None

    search_title = resolved_data.get("series_title") or resolved_data.get("title", "")
    assets = await data["artwork_mgr"].async_get_artwork(
        media_title=search_title,
        channel_name=resolved_data.get("channel_platform_name") or resolved_data.get("extra_attributes", {}).get("network_name", "") or resolved_data.get("app_name", ""),
        sport=resolved_data.get("sport", ""),
        league=resolved_data.get("league", ""),
        competitors=competitors
    )
    resolved_data.update({"entity_picture": assets.get("poster"), "logo_url": assets.get("logo")})
    
    if "sports_data" in assets:
        resolved_data["sports_data"] = assets["sports_data"]
    
    _LOGGER.info("Resolved data before merge for %s: %s", player_name, resolved_data)
    data["metadata_mgr"].merge(player_name, resolved_data)
    if player_obj := data["players"].get(player_name):
        player_obj.async_write_ha_state()

async def _check_for_progress_bar(hass, entry, player_name, image_bytes) -> bool:
    def _crop_progress_bar(img_bytes):
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        width, height = img.size
        # Crop to the bottom 33%
        cropped = img.crop((0, int(height * 0.66), width, height)).convert("RGB")
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG")
        return buf.getvalue()

    try:
        cropped_bytes = await hass.async_add_executor_job(_crop_progress_bar, image_bytes)
    except Exception as e:
        _LOGGER.error("Failed to crop image for progress bar check: %s", e)
        return False


    prompt = "Does this image contain a video progress bar or scrubber timeline? Reply strictly with a JSON object: {\"has_progress_bar\": true} or {\"has_progress_bar\": false}"

    client = hass.data[DOMAIN][entry.entry_id].get("vlm_client")
    if not client:
        return False

    try:
        # 30s rather than the old 10s: a local Ollama model needs far longer
        # than 10s per frame, so the previous timeout meant this check always
        # failed and silently reported "no progress bar".
        result = await client.analyze(prompt, cropped_bytes, timeout=30)
        if result:
            return bool(result.get("has_progress_bar", False))
    except Exception as e:
        _LOGGER.error("Progress bar check failed for %s: %s", player_name, e)
    return False

async def _single_frame_analysis(hass, entry, player_name, image_bytes, force_full_parse=False) -> dict:
    try:
        optimized_bytes = await hass.async_add_executor_job(optimize_image_for_vlm, image_bytes)
    except Exception as e:
        _LOGGER.error("Image optimization failed for %s: %s", player_name, e)
        return {}

    data = hass.data[DOMAIN][entry.entry_id]
    client = data.get("vlm_client")
    if not client:
        _LOGGER.error("No VLM client configured for %s", player_name)
        return {}
    source_entity_id = data.get("player_to_source", {}).get(player_name, f"media_player.{player_name.lower().replace(' ', '_')}")
    native_state_raw = hass.states.get(source_entity_id)
    native_state = extract_native_state(native_state_raw) or {}
    
    native_title = native_state.get("title", "")
    native_app = (native_state.get("app_name") or "").lower().strip()
    
    is_sports = is_sport_title(native_title)
    live_tv_apps = {"directv", "directv stream", "youtube tv", "hulu", "fubo", "sling"}
    generic_inputs = {"hdmi 1", "hdmi 2", "hdmi 3", "hdmi 4", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "live tv", "tv", "input", "antenna", "cable", "satellite", "unknown"}
    is_live_tv = (native_app in live_tv_apps or native_app in generic_inputs) and not is_sports
    
    active_sports_task = data.get("sports_mgr")._tracked_events.get(player_name) if "sports_mgr" in data else None
    if force_full_parse:
        active_sports_task = None

    context_msgs = []
    if native_title:
        context_msgs.append(f"Title: '{native_title}'")
    if native_app:
        context_msgs.append(f"App/Input: '{native_app}'")
    context_str = ""
    if context_msgs:
        context_str = "The Apple TV reports the following native state (this may be inaccurate or stale, please verify using the image):\n- " + "\n- ".join(context_msgs) + "\n\n"

    for attempt in range(2):
        if active_sports_task:
            tracked_sport_slug = active_sports_task[0]
            prompt = (
                f"Analyze this video frame from a live {tracked_sport_slug} broadcast.\n"
                "ONLY extract the live game score and state from the scorebug. Do NOT extract team names or tournament info.\n\n"
                "### CRITICAL INSTRUCTION:\n"
            )
            if tracked_sport_slug == "tennis":
                prompt += "1. For TENNIS: Carefully read the score bug. The current game score (e.g. 15, 30, 40, AD, or tiebreak points like 4, 5, 6) is completely separate and usually on the far right. NEVER combine the game score or tiebreak points with the set scores. Look for a serving indicator (a dot or tennis ball icon) next to a player's name. Extract ALL sets played so far in chronological order.\n"
                schema_game_state = "      \"tennis_sets\": [{\"competitor_a\": 6, \"competitor_b\": 4}, {\"competitor_a\": 1, \"competitor_b\": 2}],\n      \"tennis_game\": \"30-40 or null\",\n      \"server\": \"name of serving player or null\""
            elif tracked_sport_slug == "baseball":
                prompt += "1. For BASEBALL: Extract inning (e.g., 'Top 4th'), outs, balls, strikes, and runners on base (on_first, on_second, on_third as true/false) from the scorebug.\n"
                schema_game_state = "      \"inning\": \"Top 4th or null\",\n      \"outs\": \"2 or null\",\n      \"balls\": \"3 or null\",\n      \"strikes\": \"2 or null\",\n      \"on_first\": true,\n      \"on_second\": false,\n      \"on_third\": false"
            elif tracked_sport_slug == "basketball":
                prompt += "1. For BASKETBALL: Extract quarter (period) and game clock (e.g. 10:24) from the scorebug.\n"
                schema_game_state = "      \"period\": \"3 or null\",\n      \"clock\": \"10:24 or null\""
            elif tracked_sport_slug == "football":
                prompt += "1. For FOOTBALL: Extract quarter (period), game clock, down, distance (e.g. '3rd & 4'), and possession.\n"
                schema_game_state = "      \"period\": \"3 or null\",\n      \"clock\": \"10:24 or null\",\n      \"down\": \"3 or null\",\n      \"distance\": \"4 or null\",\n      \"possession\": \"team name or null\""
            elif tracked_sport_slug == "soccer":
                prompt += "1. For SOCCER: Extract half (period) and game clock (e.g. 45:00) from the scorebug.\n"
                schema_game_state = "      \"period\": \"2 or null\",\n      \"clock\": \"45:00 or null\""
            elif tracked_sport_slug == "hockey":
                prompt += "1. For HOCKEY: Extract period and game clock (e.g. 10:24) from the scorebug.\n"
                schema_game_state = "      \"period\": \"3 or null\",\n      \"clock\": \"10:24 or null\""
            elif tracked_sport_slug == "golf":
                prompt += "1. For GOLF: Extract the current hole.\n"
                schema_game_state = "      \"hole\": \"14 or null\""
            else:
                prompt += "1. Extract any visible game state metrics from the score bug.\n"
                schema_game_state = "      \"clock\": \"10:24 or null\",\n      \"period\": \"3 or null\",\n      \"inning\": \"Top 4th or null\",\n      \"outs\": \"2 or null\",\n      \"count\": \"3-2 or null\",\n      \"hole\": \"14 or null\""
                
            prompt += (
                "Output JSON ONLY using this exact structure:\n"
                "{\n"
                "  \"sports_info\": {\n"
                f"    \"sport\": \"{tracked_sport_slug} if a scorebug is visible, otherwise null\",\n"
                f"    \"league\": \"{active_sports_task[1]}\",\n"
                "    \"game_state\": {\n"
f"{schema_game_state}\n"
                "    }\n"
                "  }\n"
                "}"
            )
        elif is_sports:
            prompt = (
                f"Analyze this video frame from a sports broadcast and return a strict JSON object.\n\n{context_str}"
                "### CRITICAL INSTRUCTION:\n"
                "1. Identify the sport and league (e.g., 'tennis' and 'atp', or 'basketball' and 'nba').\n"
                "2. Identify the Tournament Name or Event Name.\n"
                "3. Identify the Venue or Stadium name if visible.\n"
                "4. For team sports, set 'show_info.episode' to 'Team A vs Team B'.\n"
                "5. For individual sports, set 'show_info.episode' to 'Player A vs Player B' or 'Tournament Name'.\n"
                "6. For TENNIS: Carefully read the score bug. Completed sets are in the left columns, the current set is next. The current game score (e.g. 15, 30, 40, AD, or tiebreak points like 4, 5, 6) is completely separate and usually on the far right or in a separate box. NEVER combine the game score or tiebreak points with the set scores. Look for a serving indicator (a dot or tennis ball icon) next to a player's name.\n"
                "7. Extract any visible game state metrics from the score bug. For tennis, extract ALL sets played so far in chronological order.\n\n"
                "Output JSON ONLY using this exact structure:\n"
                "{\n"
                "  \"sports_info\": {\n"
                "    \"sport\": \"sport name or null\",\n"
                "    \"league\": \"league name or null\",\n"
                "    \"tournament\": \"tournament name or null\",\n"
                "    \"venue\": \"venue name or null\",\n"
                "    \"game_state\": {\n"
                "      \"clock\": \"10:24 or null\",\n"
                "      \"period\": \"3 or null\",\n"
                "      \"inning\": \"Top 4th or null\",\n"
                "      \"outs\": \"2 or null\",\n"
                "      \"count\": \"3-2 or null\",\n"
                "      \"hole\": \"14 or null\",\n"
                "      \"tennis_sets\": [{\"competitor_a\": 6, \"competitor_b\": 4}, {\"competitor_a\": 1, \"competitor_b\": 2}],\n"
                "      \"tennis_game\": \"30-40 or null\",\n"
                "      \"server\": \"name of serving player or null\"\n"
                "    },\n"
                "    \"competitors\": [\n"
                "      {\"name\": \"Competitor A\"},\n"
                "      {\"name\": \"Competitor B\"}\n"
                "    ]\n"
                "  },\n"
                "  \"show_info\": {\n"
                "    \"episode\": [\"Team A vs Team B\"]\n"
                "  }\n"
                "}"
            )
        elif is_live_tv:
            prompt = (
                f"Analyze this video frame from a live TV broadcast and return a strict JSON object.\n\n{context_str}"
                "### CRITICAL INSTRUCTION:\n"
                "1. If the image shows the main Apple TV home screen (a grid of app icons like Netflix, Settings, Photos, App Store, etc., with no specific media playing or highlighted), simply return 'Browsing...' as the title and output nothing else.\n"
                "2. IMPORTANT: Check for a full-width video scrubber/timeline (usually at the absolute bottom edge of the screen, indicating current timestamp and total duration). If this full-width scrubber is visible, it means a video is actively paused. In this case, DO NOT return 'Browsing...'—proceed to identify the channel.\n"
                "3. If there is NO full-width video scrubber (ignore small progress bars underneath episode thumbnails or 'Resume' buttons), and the screen is a menu, grid, browsing interface, or an episode/movie details screen, simply return 'Browsing...' as the title.\n"
                "4. If it is not a menu or browsing interface, identify the TV CHANNEL or STREAMING APP from the WATERMARK LOGO usually in the bottom right or top corner.\n"
                "5. DO NOT guess the channel if no logo or text is present.\n"
                "6. Extract any age rating (e.g., 'TV-PG', 'TV-MA', 'PG-13') if visible.\n"
                "7. ESCAPE HATCH: If this is actually a live sports broadcast, simply set 'is_sports_broadcast': true and return.\n\n"
                "Output JSON ONLY using this exact structure:\n"
                "{\n"
                "  \"channel_platform_name\": \"Name of network or app\",\n"
                "  \"is_sports_broadcast\": false,\n"
                "  \"show_info\": {\n"
                "    \"rating\": \"TV-PG\"\n"
                "  },\n"
                "  \"scene_analysis\": {\n"
                "    \"mood_tags\": [\"Action\", \"Drama\"],\n"
                "    \"visual_description\": \"Briefly describe what is happening\"\n"
                "  }\n"
                "}"
            )
        else:
            prompt = (
                f"Analyze this video frame from a TV show or movie and return a strict JSON object.\n\n{context_str}"
                "### CRITICAL INSTRUCTION:\n"
                "1. ESCAPE HATCH: If this is actually a live sports broadcast, simply set 'is_sports_broadcast': true and return.\n"
                "2. If the image shows the main Apple TV home screen (a grid of app icons like Netflix, Settings, Photos, App Store, etc., with no specific media playing or highlighted), simply return 'Browsing...' as the title and return.\n"
                "3. IMPORTANT: Check for a full-width video scrubber/timeline (usually at the absolute bottom edge of the screen, indicating current timestamp and total duration). If this full-width scrubber is visible, it means a video is actively paused. In this case, DO NOT return 'Browsing...'—proceed to identify the show.\n"
                "4. If there is NO full-width video scrubber (ignore small progress bars underneath episode thumbnails or 'Resume' buttons), and the screen is a menu, grid, browsing interface, or an episode/movie details screen, simply return 'Browsing...' as the title and return.\n"
                "5. If it is not a menu or details screen, identify the name of the specific episode if it is shown on screen (e.g., during an intro sequence).\n"
                "6. Identify any visible actors by name.\n"
                "7. Identify the main title of the series or movie.\n\n"
                "Output JSON ONLY using this exact structure:\n"
                "{\n"
                "  \"title\": \"Name of Movie or Show\",\n"
                "  \"is_sports_broadcast\": false,\n"
                "  \"show_info\": {\n"
                "    \"episode\": [\"Name of episode\"],\n"
                "    \"actors\": [\"Actor A\", \"Actor B\"]\n"
                "  },\n"
                "  \"scene_analysis\": {\n"
                "    \"mood_tags\": [\"Action\", \"Drama\"],\n"
                "    \"visual_description\": \"Briefly describe what is happening\"\n"
                "  }\n"
                "}"
            )
            
        _LOGGER.debug("Requesting VLM analysis for %s (sports=%s)", player_name, bool(is_sports))

        try:
            # Escalate to the higher-quality Flash tier only on a full
            # identification pass. Sports score polling reads a scorebug that
            # the primary tier handles reliably, and that path is far too
            # frequent to spend the small Flash budget on.
            allow_escalation = not active_sports_task and not is_sports
            result = await client.analyze(
                prompt, optimized_bytes, timeout=45, allow_escalation=allow_escalation
            )

            if not result:
                _LOGGER.debug("VLM returned no usable result for %s", player_name)
                return {}

            if result.get("is_sports_broadcast") is True:
                _LOGGER.info("VLM detected sports broadcast on VOD app. Falling back to sports prompt.")
                is_sports = True
                continue

            if active_sports_task and "sports_info" not in result:
                game_state = result.get("game_state", result)
                result = {
                    "sports_info": {
                        "sport": active_sports_task[0],
                        "league": active_sports_task[1],
                        "game_state": game_state
                    }
                }

            if not validate_vlm_result(result):
                return {}

            _LOGGER.debug("VLM response for %s: %s", player_name, result)
            return result
        except Exception as e:
            _LOGGER.error("VLM processing failure on %s: %s", player_name, e)

    return {}

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return True
    
    for task in data.get("watchdogs", {}).values():
        task.cancel()
    for task in data.get("pending_settle_tasks", {}).values():
        task.cancel()
    for task in data.get("pending_state_handlers", {}).values():
        task.cancel()
        
    if "sports_mgr" in data:
        data["sports_mgr"].stop_all()
    for unsub in data.get("unsub_listeners", []):
        unsub()
        
    # Remove from hass.data to prevent memory leaks
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return result


