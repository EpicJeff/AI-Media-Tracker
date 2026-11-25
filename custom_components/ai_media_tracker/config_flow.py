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
                "cast_stream_source": user_input.get("cast_stream_source", ""),
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
        schema_dict[vol.Optional("cast_stream_source", description="High-Quality Cast Stream URL (Optional)")] = str
        
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

    def __init__(self):
        self._current_options = {}
        self._selected_device_id = None

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the configuration options screen."""
        self._current_options = dict(self.config_entry.options)
        
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "devices"]
        )

    async def async_step_general(self, user_input=None) -> FlowResult:
        """Handle general settings."""
        if user_input is not None:
            self._current_options.update(user_input)
            return self.async_create_entry(title="", data=self._current_options)

        current_url = self._current_options.get("ollama_url", self.config_entry.data.get("ollama_url", "http://localhost:11434"))
        current_model = self._current_options.get("model_name", self.config_entry.data.get("model_name", "qwen3.5-vl:2b"))
        current_provider = self._current_options.get("vlm_provider", self.config_entry.data.get("vlm_provider", "ollama"))
        current_gemini_key = self._current_options.get("gemini_api_key", self.config_entry.data.get("gemini_api_key", ""))
        current_escalate = self._current_options.get("gemini_escalate", True)
        current_allow_gemma = self._current_options.get("gemini_allow_gemma", False)
        current_tmdb = self._current_options.get("tmdb_api_key", "")
        current_logo_dev = self._current_options.get("logo_dev_api_key", "")
        current_gracenote = self._current_options.get("gracenote_api_key", "")
        current_advanced_cast_app_id = self._current_options.get("advanced_cast_app_id", "")
        
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
                
        current_platforms = self._current_options.get("allowed_cast_platforms", [])
        current_classes = self._current_options.get("allowed_cast_device_classes", [])
        
        for p in current_platforms:
            if p not in platforms: platforms.add(p)
        for c in current_classes:
            if c not in device_classes: device_classes.add(c)
            
        platform_options = {p: p.replace("_", " ").title() for p in sorted(list(platforms))}
        class_options = {c: "Generic / Unspecified" if c == "unknown" else c.title() for c in sorted(list(device_classes))}
        
        import homeassistant.helpers.config_validation as cv
        from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectOptionDict

        provider_selector = SelectSelector(SelectSelectorConfig(options=[
            SelectOptionDict(value="ollama", label="Ollama (local)"),
            SelectOptionDict(value="gemini", label="Google Gemini API (cloud)"),
        ]))

        schema_dict = {
            vol.Required("vlm_provider", default=current_provider): provider_selector,
            vol.Optional("gemini_api_key", default=current_gemini_key): str,
            vol.Optional("gemini_escalate", default=current_escalate): bool,
            vol.Optional("gemini_allow_gemma", default=current_allow_gemma): bool,
            vol.Required("ollama_url", default=current_url): str,
            vol.Required("model_name", default=current_model): str,
            vol.Optional("tmdb_api_key", default=current_tmdb): str,
            vol.Optional("logo_dev_api_key", default=current_logo_dev): str,
            vol.Optional("gracenote_api_key", default=current_gracenote): str,
            vol.Optional("allowed_cast_platforms", default=current_platforms, description="Allowed Integrations for Cast (Leave empty for all)"): cv.multi_select(platform_options),
            vol.Optional("allowed_cast_device_classes", default=current_classes, description="Allowed Device Types for Cast (Leave empty for all)"): cv.multi_select(class_options),
            vol.Optional("advanced_cast_app_id", default=current_advanced_cast_app_id, description="Advanced Custom Cast App ID (Phase 5)"): str,
        }
        return self.async_show_form(step_id="general", data_schema=vol.Schema(schema_dict))

    async def async_step_devices(self, user_input=None) -> FlowResult:
        """Manage list of devices."""
        from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode, SelectOptionDict
        tracked_devices = self.config_entry.data.get("devices", [])
        
        if user_input is not None:
            action = user_input.get("action")
            if action == "add_new":
                return await self.async_step_add_device()
            else:
                self._selected_device_id = action
                return await self.async_step_edit_device()
                
        options = [SelectOptionDict(value="add_new", label="➕ Add New Device")]
        for dev in tracked_devices:
            options.append(SelectOptionDict(value=dev['device_id'], label=f"Manage {dev['name']}"))
            
        schema = vol.Schema({
            vol.Required("action", default="add_new"): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        })
        return self.async_show_form(step_id="devices", data_schema=schema)

    async def async_step_add_device(self, user_input=None) -> FlowResult:
        """Add a new device from the options flow."""
        dev_reg = dr.async_get(self.hass)
        eligible_devices = {}
        device_domains = {}
        resolved_ips = {}

        for device_id, device_entry in dev_reg.devices.items():
            registered_domains = {ident[0] for ident in device_entry.identifiers}
            name = device_entry.name_by_user or device_entry.name or device_id
            
            if "apple_tv" in registered_domains:
                eligible_devices[device_id] = f"{name} (Apple TV)"
                device_domains[device_id] = "apple_tv"
                
            elif "androidtv_remote" in registered_domains:
                eligible_devices[device_id] = f"{name} (Google TV)"
                device_domains[device_id] = "androidtv_remote"
                
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

            new_devices = list(self.config_entry.data.get("devices", []))
            if not any(d["device_id"] == target_device_id for d in new_devices):
                new_devices.append({
                    "device_id": target_device_id,
                    "name": clean_name,
                    "hardware_type": hardware_type,
                    "stream_source": user_input.get("stream_source", ""),
                    "cast_stream_source": user_input.get("cast_stream_source", ""),
                    "adb_ip": final_adb_ip,
                    "area_id": dev_entry.area_id if dev_entry else None,
                    "enabled": True
                })
                new_data = dict(self.config_entry.data)
                new_data["devices"] = new_devices
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            
            if user_input.get("add_another"):
                return await self.async_step_add_device()
                
            return self.async_create_entry(title="", data=self._current_options)

        schema_dict = {vol.Required("device_id"): vol.In(eligible_devices)}
        schema_dict[vol.Optional("stream_source", description="RTSP URL (Only required for Apple TVs via Encoders)")] = str
        schema_dict[vol.Optional("cast_stream_source", description="High-Quality Cast Stream URL (Optional)")] = str
        
        has_unresolved_googletvs = any(
            device_domains[d_id] == "androidtv_remote" and d_id not in resolved_ips 
            for d_id in eligible_devices
        )
        if has_unresolved_googletvs:
            schema_dict[vol.Optional("fallback_adb_ip", description="Google TV IP (Fallback only if auto-detection misses)")] = str

        schema_dict[vol.Optional("add_another", default=False)] = bool

        return self.async_show_form(step_id="add_device", data_schema=vol.Schema(schema_dict))

    async def async_step_edit_device(self, user_input=None) -> FlowResult:
        """Edit Stream Source and Guardrails for a specific device."""
        import homeassistant.helpers.config_validation as cv
        
        tracked_devices = self.config_entry.data.get("devices", [])
        device_obj = next((d for d in tracked_devices if d["device_id"] == self._selected_device_id), None)
        if not device_obj:
            return self.async_abort(reason="device_not_found")
            
        dev_name = device_obj["name"]
        
        if user_input is not None:
            if user_input.get("delete_device"):
                new_devices = [d for d in tracked_devices if d["device_id"] != self._selected_device_id]
                new_data = dict(self.config_entry.data)
                new_data["devices"] = new_devices
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                
                self._current_options.pop(f"cast_audio_delay_{self._selected_device_id}", None)
                self._current_options.pop(f"guardrails_{dev_name}", None)
                return self.async_create_entry(title="", data=self._current_options)

            new_devices = []
            for d in tracked_devices:
                new_dev = dict(d)
                if d["device_id"] == self._selected_device_id:
                    new_dev["stream_source"] = user_input.get("stream_source", "")
                    new_dev["cast_stream_source"] = user_input.get("cast_stream_source", "")
                new_devices.append(new_dev)
                
            new_data = dict(self.config_entry.data)
            new_data["devices"] = new_devices
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            
            self._current_options[f"cast_audio_delay_{self._selected_device_id}"] = user_input.get("cast_audio_delay", 0.0)
            
            self._current_options[f"guardrails_{dev_name}"] = {
                "ratings": user_input.get("ratings", []),
                "tags": user_input.get("tags", []),
                "custom_tags": user_input.get("custom_tags", "")
            }
            return self.async_create_entry(title="", data=self._current_options)
            
        RATINGS = {"TV-Y":"TV-Y", "TV-Y7":"TV-Y7", "TV-G":"TV-G", "TV-PG":"TV-PG", "TV-14":"TV-14", "TV-MA":"TV-MA", "G":"G", "PG":"PG", "PG-13":"PG-13", "R":"R", "NC-17":"NC-17"}
        TAGS = {"Violence":"Violence", "Gore":"Gore", "Nudity":"Nudity", "Sexual Content":"Sexual Content", "Profanity":"Profanity", "Horror":"Horror", "Frightening":"Frightening", "Drugs":"Drugs"}
        
        current_source = device_obj.get("stream_source", "")
        current_cast_source = device_obj.get("cast_stream_source", "")
        current_delay = self._current_options.get(f"cast_audio_delay_{self._selected_device_id}", 0.0)
        
        guardrail = self._current_options.get(f"guardrails_{dev_name}", {})
        current_ratings = guardrail.get("ratings", [])
        current_tags = guardrail.get("tags", [])
        current_custom = guardrail.get("custom_tags", "")
        
        schema = vol.Schema({
            vol.Optional("stream_source", default=current_source): str,
            vol.Optional("cast_stream_source", default=current_cast_source): str,
            vol.Optional("cast_audio_delay", default=current_delay): vol.Coerce(float),
            vol.Optional("ratings", default=current_ratings): cv.multi_select(RATINGS),
            vol.Optional("tags", default=current_tags): cv.multi_select(TAGS),
            vol.Optional("custom_tags", default=current_custom): str,
            vol.Optional("delete_device", default=False): bool
        })
        
        return self.async_show_form(step_id="edit_device", data_schema=schema, description_placeholders={"name": dev_name})