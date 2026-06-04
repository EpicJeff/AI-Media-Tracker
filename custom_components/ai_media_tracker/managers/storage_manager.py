import logging
import time
from datetime import datetime
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ai_media_tracker.history"

class StorageManager:
    def __init__(self, hass):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data = {}
        self._active_sessions = {}
        self._unsub_listener = None

    async def async_load(self):
        self._data = await self._store.async_load() or {}
        
        async def on_state_changed(event):
            player_name = event.data.get("player_name")
            state = event.data.get("state")
            metadata = event.data.get("metadata", {})
            
            if not player_name:
                return

            if state == "playing":
                await self.start_session(player_name, metadata)
            else:
                await self.end_session(player_name)

        self._unsub_listener = self.hass.bus.async_listen("ai_media_tracker_state_changed", on_state_changed)

    def async_unload(self):
        if self._unsub_listener:
            self._unsub_listener()

    async def async_save(self):
        await self._store.async_save(self._data)

    async def start_session(self, player_name, metadata):
        # End existing session if active (e.g. title changed)
        if player_name in self._active_sessions:
            await self.end_session(player_name)
            
        self._active_sessions[player_name] = {
            "start_time": time.time(),
            "metadata": metadata
        }

    async def end_session(self, player_name):
        session = self._active_sessions.pop(player_name, None)
        if not session:
            return

        duration_sec = time.time() - session["start_time"]
        if duration_sec < 60:
            return  # Ignore very short sessions (< 1 minute)
            
        duration_min = round(duration_sec / 60, 2)

        room_data = self._data.setdefault(player_name, {
            "daily_minutes": 0,
            "last_reset": datetime.now().strftime("%Y-%m-%d"),
            "history": []
        })

        # Check for daily reset
        today = datetime.now().strftime("%Y-%m-%d")
        if room_data["last_reset"] != today:
            room_data["daily_minutes"] = 0
            room_data["last_reset"] = today

        room_data["daily_minutes"] += duration_min
        
        meta = session["metadata"]
        
        # Build clean history entry with full metadata payload
        history_entry = {
            "start_time": datetime.fromtimestamp(session["start_time"]).isoformat(),
            "duration_minutes": duration_min,
            **meta
        }
        
        room_data["history"].append(history_entry)
        
        # Keep history bounded to last 100 sessions per room to prevent unbounded storage growth
        room_data["history"] = room_data["history"][-100:]

        await self.async_save()
