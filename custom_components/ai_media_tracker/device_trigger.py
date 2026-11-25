import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

from .const import DOMAIN

TRIGGER_TYPES = {"guardrail_violation", "room_followed", "show_started", "sporting_event_started"}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required("type"): vol.In(TRIGGER_TYPES),
    }
)

async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict]:
    """List device triggers for AI Media Tracker devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if not device:
        return []

    # Verify this is our device by checking identifiers
    has_our_identifier = False
    player_slug = None
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            has_our_identifier = True
            player_slug = identifier
            break

    if not has_our_identifier:
        return []

    # Since our devices map 1-to-1 with a proxy player, we can just return the triggers.
    return [
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device_id,
            "type": "guardrail_violation",
        },
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device_id,
            "type": "room_followed",
        },
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device_id,
            "type": "show_started",
        },
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device_id,
            "type": "sporting_event_started",
        },
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    trigger_type = config["type"]
    device_id = config["device_id"]

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    # To filter the event to THIS specific device, we need the player_name.
    # Our player_name was converted to a slug for the identifier.
    # But wait, the event payload contains "player_name" or "entity_id".
    # For ai_media_tracker_guardrail_violation, it passes "player_name" AND "entity_id" (which is the source_entity_id).
    # Actually, the proxy player name is in "player_name".

    player_name = None
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            # We don't have the original exact case name, but we can just filter by the entity_id
            player_name_slug = identifier
            break
            
    if trigger_type == "guardrail_violation":
        # The event we fire is "ai_media_tracker_guardrail_violation"
        # It has "player_name" in the payload. We can check if player_name.lower().replace(" ", "_") == slug.
        
        event_config = event_trigger.TRIGGER_SCHEMA(
            {
                event_trigger.CONF_PLATFORM: "event",
                event_trigger.CONF_EVENT_TYPE: "ai_media_tracker_guardrail_violation",
            }
        )
        
        # We wrap the action to only trigger if it matches our device
        @callback
        async def _handle_trigger(variables, context=None):
            event_obj = variables["trigger"]["event"]
            payload_player = event_obj.data.get("player_name", "")
            if payload_player.lower().replace(" ", "_") == player_name_slug:
                await action(variables, context)
                
        return await event_trigger.async_attach_trigger(
            hass, event_config, _handle_trigger, trigger_info
        )

    if trigger_type == "room_followed":
        event_config = event_trigger.TRIGGER_SCHEMA(
            {
                event_trigger.CONF_PLATFORM: "event",
                event_trigger.CONF_EVENT_TYPE: "ai_media_tracker_room_followed",
            }
        )
        
        @callback
        async def _handle_trigger(variables, context=None):
            event_obj = variables["trigger"]["event"]
            payload_target = event_obj.data.get("target_player", "")
            if payload_target.lower().replace(" ", "_") == player_name_slug:
                await action(variables, context)
                
        return await event_trigger.async_attach_trigger(
            hass, event_config, _handle_trigger, trigger_info
        )

    if trigger_type in ("show_started", "sporting_event_started"):
        event_name = "ai_media_tracker_show_started" if trigger_type == "show_started" else "ai_media_tracker_sporting_event_started"
        event_config = event_trigger.TRIGGER_SCHEMA(
            {
                event_trigger.CONF_PLATFORM: "event",
                event_trigger.CONF_EVENT_TYPE: event_name,
            }
        )
        
        @callback
        async def _handle_trigger(variables, context=None):
            event_obj = variables["trigger"]["event"]
            payload_player = event_obj.data.get("player_name", "")
            if payload_player.lower().replace(" ", "_") == player_name_slug:
                await action(variables, context)
                
        return await event_trigger.async_attach_trigger(
            hass, event_config, _handle_trigger, trigger_info
        )

    return lambda: None
