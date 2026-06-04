import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.config_entries import OptionsFlowWithReload
from .const import DOMAIN

class AIMediaTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self):
        self._flow_data = {}

    async def async_step_user(self, user_input=None):
        """Step 1: Core VLM Server Configuration."""
        if user_input is not None:
            self._flow_data = {
                "ollama_url": user_input["ollama_url"],
                "model_name": user_input["model_name"],
                "devices": []
            }
            return await self.async_step_add_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("ollama_url", default="http://localhost:11434"): str,
                vol.Required("model_name", default="qwen3.5-vl:2b"): str,
            })
        )

    async def async_step_add_device(self, user_input=None):
        """Step 2: Track device and silently extract networking keys from parent configs."""
        dev_reg = dr.async_get(self.hass)
        eligible_devices = {}
        device_domains = {}
        resolved_ips = {}

        # Scan and catalog domains automatically
        for device_id, device_entry in dev_reg.devices.items():
            registered_domains = {ident[0] for ident in device_entry.identifiers}
            name = device_entry.name_by_user or device_entry.name or device_id
            
            if "apple_tv" in registered_domains:
                eligible_devices[device_id] = f"{name} (Apple TV)"
                device_domains[device_id] = "apple_tv"
                
            elif "androidtv_remote" in registered_domains:
                eligible_devices[device_id] = f"{name} (Google TV)"
                device_domains[device_id] = "androidtv_remote"
                
                # --- AUTOMATED IP EXTRACTION LOGIC ---
                for entry_id in device_entry.config_entries:
                    parent_entry = self.hass.config_entries.async_get_entry(entry_id)
                    if parent_entry and parent_entry.domain == "androidtv_remote":
                        host_ip = parent_entry.data.get("host")
                        if host_ip:
                            resolved_ips[device_id] = host_ip
                            break

        if user_input is not None:
            target_device_id = user_input["device_id"]
            detected_domain = device_domains.get(target_device_id, "unknown")
            hardware_type = "Apple TV" if detected_domain == "apple_tv" else "Google TV"
            
            dev_entry = dev_reg.async_get(target_device_id)
            clean_name = dev_entry.name_by_user or dev_entry.name if dev_entry else "Unknown Room"
            clean_name = clean_name.replace(" Apple TV", "").replace(" Google TV", "").replace(" TV", "")

            final_adb_ip = resolved_ips.get(target_device_id) or user_input.get("fallback_adb_ip", "")

            self._flow_data["devices"].append({
                "device_id": target_device_id,
                "name": clean_name,
                "hardware_type": hardware_type,
                "stream_source": user_input.get("stream_source", ""),
                "adb_ip": final_adb_ip,
                "area_id": dev_entry.area_id if dev_entry else None,
                "enabled": True
            })

            self._flow_data["resolved"] = {}
            self._flow_data["vlm"] = {}
            
            if user_input.get("add_another"):
                return await self.async_step_add_device()
                
            return self.async_create_entry(title="AI Media Center", data=self._flow_data)

        # Build schema
        schema_dict = {vol.Required("device_id"): vol.In(eligible_devices)}
        schema_dict[vol.Optional("stream_source", description="RTSP URL (Only required for Apple TVs via Encoders)")] = str
        
        has_unresolved_googletvs = any(
            device_domains[d_id] == "androidtv_remote" and d_id not in resolved_ips 
            for d_id in eligible_devices
        )
        if has_unresolved_googletvs:
            schema_dict[vol.Optional("fallback_adb_ip", description="Google TV IP (Fallback only if auto-detection misses)")] = str

        schema_dict[vol.Optional("add_another", default=False)] = bool

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema(schema_dict)
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Link the runtime configuration Options Flow to this integration instance."""
        return AIMediaTrackerOptionsFlow()


class AIMediaTrackerOptionsFlow(OptionsFlowWithReload):
    """Handle runtime adjustments via the Configure button with automatic reloads."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the configuration options screen."""
        if user_input is not None:
            devices = self.config_entry.data.get("devices", [])
            new_devices = []
            for dev in devices:
                new_dev = dict(dev)
                key = f"stream_source_{dev['device_id']}"
                if key in user_input:
                    new_dev["stream_source"] = user_input[key]
                new_devices.append(new_dev)
            
            new_data = dict(self.config_entry.data)
            new_data["devices"] = new_devices
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data=user_input)

        # Read active settings
        current_url = self.config_entry.options.get("ollama_url", self.config_entry.data.get("ollama_url", "http://localhost:11434"))
        current_model = self.config_entry.options.get("model_name", self.config_entry.data.get("model_name", "qwen3.5-vl:2b"))
        current_tmdb = self.config_entry.options.get("tmdb_api_key", "")
        current_logo_dev = self.config_entry.options.get("logo_dev_api_key", "")
        current_gracenote = self.config_entry.options.get("gracenote_api_key", "")
        current_guardrail_tags = self.config_entry.options.get("guardrail_tags", "")
        current_advanced_cast_app_id = self.config_entry.options.get("advanced_cast_app_id", "")
        
        tracked_devices = self.config_entry.data.get("devices", [])
        tracked_players = [dev.get("name") for dev in tracked_devices]
        current_guardrail_players = self.config_entry.options.get("guardrail_players", [])
        
        from homeassistant.components.media_player import MediaPlayerEntityFeature
        entity_registry = er.async_get(self.hass)
        
        BANNED_PLATFORMS = {"androidtv_remote", "androidtv", "plex", "jellyfin", "emby"}
        
        platforms = set()
        device_classes = set()
        
        for state in self.hass.states.async_all("media_player"):
            if state.entity_id.startswith("media_player.ai_media_") or state.entity_id.startswith("media_player.ai_tracker_"):
                continue
                
            features = state.attributes.get("supported_features", 0)
            if features & MediaPlayerEntityFeature.PLAY_MEDIA:
                entity = entity_registry.async_get(state.entity_id)
                platform = entity.platform if entity else "unknown"
                
                if platform in BANNED_PLATFORMS:
                    continue
                    
                dclass = state.attributes.get("device_class")
                device_classes.add(dclass if dclass else "unknown")
                platforms.add(platform)
                
        current_platforms = self.config_entry.options.get("allowed_cast_platforms", [])
        current_classes = self.config_entry.options.get("allowed_cast_device_classes", [])
        
        for p in current_platforms:
            if p not in platforms: platforms.add(p)
        for c in current_classes:
            if c not in device_classes: device_classes.add(c)
            
        platform_options = {p: p.replace("_", " ").title() for p in sorted(list(platforms))}
        class_options = {c: "Generic / Unspecified" if c == "unknown" else c.title() for c in sorted(list(device_classes))}
        
        import homeassistant.helpers.config_validation as cv

        schema_dict = {
            vol.Required("ollama_url", default=current_url): str,
            vol.Required("model_name", default=current_model): str,
            vol.Optional("tmdb_api_key", default=current_tmdb): str,
            vol.Optional("logo_dev_api_key", default=current_logo_dev): str,
            vol.Optional("gracenote_api_key", default=current_gracenote): str,
            vol.Optional("guardrail_tags", default=current_guardrail_tags, description="Comma-separated restricted tags/ratings (e.g. TV-MA, R, Gore)"): str,
            vol.Optional("guardrail_players", default=current_guardrail_players, description="Players to enforce guardrails on"): cv.multi_select(tracked_players),
            vol.Optional("allowed_cast_platforms", default=current_platforms, description="Allowed Integrations for Cast (Leave empty for all)"): cv.multi_select(platform_options),
            vol.Optional("allowed_cast_device_classes", default=current_classes, description="Allowed Device Types for Cast (Leave empty for all)"): cv.multi_select(class_options),
            vol.Optional("advanced_cast_app_id", default=current_advanced_cast_app_id, description="Advanced Custom Cast App ID (Phase 5)"): str,
        }
        
        # Inject stream source editors for each device at the bottom
        for dev in tracked_devices:
            key_source = f"stream_source_{dev['device_id']}"
            key_delay = f"cast_audio_delay_{dev['device_id']}"
            current_val_source = self.config_entry.options.get(key_source, dev.get("stream_source", ""))
            current_val_delay = self.config_entry.options.get(key_delay, 0.0)
            
            schema_dict[vol.Optional(key_source, default=current_val_source, description=f"{dev['name']} Stream URL")] = str
            schema_dict[vol.Optional(key_delay, default=current_val_delay, description=f"{dev['name']} Cast Audio Delay (seconds)")] = vol.Coerce(float)

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))