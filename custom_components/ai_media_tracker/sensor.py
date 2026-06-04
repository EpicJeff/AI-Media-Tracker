import logging
import datetime
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up AI Media Tracker sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    storage_mgr = data.get("storage_mgr")
    
    if not storage_mgr:
        _LOGGER.error("StorageManager not found in integration data")
        return

    devices = entry.data.get("devices", [])
    sensors = []
    
    for dev in devices:
        room_name = dev.get("name")
        if room_name:
            sensors.append(ViewingHistorySensor(hass, room_name, storage_mgr))
            
    async_add_entities(sensors, update_before_add=True)


class ViewingHistorySensor(SensorEntity):
    """Sensor that tracks daily watch time for a room."""

    def __init__(self, hass, room_name, storage_mgr):
        """Initialize the sensor."""
        self.hass = hass
        self._room_name = room_name
        self._storage_mgr = storage_mgr
        
        slug = room_name.lower().replace(" ", "_")
        self._attr_unique_id = f"ai_tracker_watch_time_{slug}"
        self._attr_name = f"{room_name} Watch Time"
        
        # We present in minutes so it's easy to read/graph
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_icon = "mdi:television-classic"
        self._attr_state_class = "measurement"

    @property
    def native_value(self):
        """Return the daily watch time in minutes."""
        data = self._storage_mgr._data.get(self._room_name, {})
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # If the last reset was not today, the daily watch time is effectively 0
        if data.get("last_reset") != today:
            return 0
            
        return data.get("daily_minutes", 0)

    @property
    def extra_state_attributes(self):
        """Return the detailed viewing history."""
        data = self._storage_mgr._data.get(self._room_name, {})
        return {
            "history": data.get("history", [])
        }

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        async def on_state_changed(event):
            # Only update if the event pertains to this room
            if event.data.get("player_name") == self._room_name:
                self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen("ai_media_tracker_state_changed", on_state_changed)
        )
