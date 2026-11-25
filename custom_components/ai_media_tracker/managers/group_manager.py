import logging
from homeassistant.helpers.storage import Store
from homeassistant.core import callback

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ai_media_tracker.target_groups"

class GroupManager:
    def __init__(self, hass):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # _groups maps parent_entity_id -> list of child_entity_ids
        self._groups = {}
        self._update_callbacks = []

    async def async_load(self):
        loaded_data = await self._store.async_load()
        if loaded_data:
            self._groups = loaded_data.get("groups", {})
        else:
            self._groups = {}

    async def async_save(self):
        await self._store.async_save({"groups": self._groups})
        self._notify_listeners()

    def register_listener(self, update_callback):
        self._update_callbacks.append(update_callback)
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
                _LOGGER.error("Error updating group listener: %s", e)

    @property
    def groups(self):
        # Return a deep copy so Home Assistant detects state attribute changes correctly
        return {k: list(v) for k, v in self._groups.items()}

    def get_parent_for_child(self, child_entity_id):
        for parent, children in self._groups.items():
            if child_entity_id in children:
                return parent
        return None

    async def async_group_targets(self, parent_entity_id: str, child_entity_ids: list):
        if parent_entity_id not in self._groups:
            self._groups[parent_entity_id] = []
            
        flattened_added = []
            
        for child_id in child_entity_ids:
            # Prevent circular dependencies
            if child_id == parent_entity_id:
                continue
                
            # If child is already a parent itself, flatten its children into the new parent
            if child_id in self._groups:
                grandchildren = self._groups.pop(child_id)
                for grandchild in grandchildren:
                    if grandchild != parent_entity_id and grandchild not in self._groups[parent_entity_id]:
                        self._groups[parent_entity_id].append(grandchild)
                        flattened_added.append(grandchild)
            
            # Remove child from any existing group first
            current_parent = self.get_parent_for_child(child_id)
            if current_parent and current_parent != parent_entity_id:
                self._groups[current_parent].remove(child_id)
                
            if child_id not in self._groups[parent_entity_id]:
                self._groups[parent_entity_id].append(child_id)
                flattened_added.append(child_id)
                
        # Clean up empty groups
        self._cleanup_empty_groups()
        await self.async_save()
        return flattened_added

    async def async_ungroup_targets(self, child_entity_ids: list):
        changed = False
        for child_id in child_entity_ids:
            parent = self.get_parent_for_child(child_id)
            if parent:
                self._groups[parent].remove(child_id)
                changed = True
                
        if changed:
            self._cleanup_empty_groups()
            await self.async_save()
            
    def _cleanup_empty_groups(self):
        keys_to_delete = [k for k, v in self._groups.items() if not v]
        for k in keys_to_delete:
            del self._groups[k]
