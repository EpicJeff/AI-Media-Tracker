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
        # Cleanly instantiate with zero arguments. 
        # OptionsFlowWithReload natively populates self.config_entry for us.
        return AIMediaTrackerOptionsFlow()


class AIMediaTrackerOptionsFlow(OptionsFlowWithReload):
    """Handle runtime adjustments via the Configure button with automatic reloads."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the configuration options screen."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Read active settings, ignoring the obsolete polling key
        current_url = self.config_entry.options.get(
            "ollama_url", self.config_entry.data.get("ollama_url", "http://localhost:11434")
        )
        current_model = self.config_entry.options.get(
            "model_name", self.config_entry.data.get("model_name", "qwen3.5-vl:2b")
        )
        current_tmdb = self.config_entry.options.get("tmdb_api_key", "")

        # Pristine schema focusing strictly on endpoint values and API keys
        options_schema = vol.Schema({
            vol.Required("ollama_url", default=current_url): str,
            vol.Required("model_name", default=current_model): str,
            vol.Optional("tmdb_api_key", default=current_tmdb): str,
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)