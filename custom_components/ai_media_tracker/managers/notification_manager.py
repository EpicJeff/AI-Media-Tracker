import logging
import uuid
from datetime import datetime
from homeassistant.helpers.storage import Store
from homeassistant.core import callback

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ai_media_tracker.notifications"

class NotificationManager:
    def __init__(self, hass):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._notifications = []
        self._update_callbacks = []

    async def async_load(self):
        self._notifications = await self._store.async_load() or []

    async def async_save(self):
        await self._store.async_save(self._notifications)
        self._notify_listeners()

    def register_listener(self, update_callback):
        self._update_callbacks.append(update_callback)
        # Call immediately to set initial state
        update_callback()
        
        def remove_listener():
            if update_callback in self._update_callbacks:
                self._update_callbacks.remove(update_callback)
        return remove_listener

    @callback
    def _notify_listeners(self):
        for update_callback in self._update_callbacks:
            try:
                update_callback()
            except Exception as e:
                _LOGGER.error("Error updating notification listener: %s", e)

    @property
    def notifications(self):
        # Sort descending by timestamp
        return sorted(self._notifications, key=lambda x: x.get("timestamp", ""), reverse=True)

    @property
    def unread_count(self):
        return sum(1 for n in self._notifications if not n.get("read", False))

    async def async_create_notification(self, title: str, message: str, notif_type: str = "info", image_url: str = None, player_name: str = None):
        notif = {
            "id": uuid.uuid4().hex,
            "timestamp": datetime.now().isoformat(),
            "title": title,
            "message": message,
            "type": notif_type,
            "image_url": image_url,
            "player_name": player_name,
            "read": False
        }
        self._notifications.append(notif)
        
        # Cap at 50 notifications to prevent unbounded growth
        if len(self._notifications) > 50:
            # Remove oldest
            self._notifications.sort(key=lambda x: x.get("timestamp", ""))
            self._notifications = self._notifications[-50:]
            
        await self.async_save()
        return notif["id"]

    async def async_mark_read(self, notif_id: str = None):
        updated = False
        if notif_id:
            for n in self._notifications:
                if n["id"] == notif_id and not n.get("read", False):
                    n["read"] = True
                    updated = True
        else:
            # Mark all as read
            for n in self._notifications:
                if not n.get("read", False):
                    n["read"] = True
                    updated = True
                    
        if updated:
            await self.async_save()

    async def async_clear_notification(self, notif_id: str = None):
        if notif_id:
            original_len = len(self._notifications)
            self._notifications = [n for n in self._notifications if n["id"] != notif_id]
            if len(self._notifications) != original_len:
                await self.async_save()
        else:
            # Clear all
            if self._notifications:
                self._notifications = []
                await self.async_save()
