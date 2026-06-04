import logging
import asyncio
from datetime import datetime, timezone
import difflib
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

class GuideManager:
    """Manages television EPG listings, enabling show-to-channel timezone cross-referencing."""

    def __init__(self, hass):
        self.hass = hass
        self._schedule_cache = []
        self._last_fetch = None

    async def async_refresh_schedule(self):
        """Fetch the current TV schedule to run local matches against."""
        now = datetime.now(timezone.utc)
        if self._last_fetch and (now - self._last_fetch).total_seconds() < 14400:
            return # Cache is valid for 4 hours

        session = async_get_clientsession(self.hass)
        try:
            # Querying the TVMaze primary daily US schedule
            url = "https://api.tvmaze.com/schedule?country=US"
            async with session.get(url, timeout=8.0) as resp:
                if resp.status == 200:
                    self._schedule_cache = await resp.json()
                    self._last_fetch = now
                    _LOGGER.info("GuideManager: Successfully loaded %d live TV listings.", len(self._schedule_cache))
        except Exception as ex:
            _LOGGER.error("GuideManager: Schedule download failed: %s", ex)

    async def find_channel_by_title(self, media_title: str) -> str | None:
        """Cross-references a movie/show title with ongoing TV broadcasts."""
        if not media_title or len(media_title) < 3:
            return None

        await self.async_refresh_schedule()
        if not self._schedule_cache:
            return None

        now = datetime.now(timezone.utc)
        target_normalized = media_title.lower().strip()
        best_match = None
        highest_ratio = 0.8  # Match quality threshold

        for item in self._schedule_cache:
            show_name = item.get("show", {}).get("name", "")
            if not show_name:
                continue

            # Compare airtimes
            airstamp_str = item.get("airstamp")
            runtime = item.get("runtime") or 30
            
            if not airstamp_str:
                continue
                
            try:
                # Parse ISO date structures safely
                airstamp = datetime.fromisoformat(airstamp_str.replace("Z", "+00:00"))
                air_end = airstamp + asyncio.subprocess.sys.modules['datetime'].timedelta(minutes=runtime)
                
                # Check if this show is currently broadcasting
                if airstamp <= now <= air_end:
                    match_ratio = difflib.SequenceMatcher(None, target_normalized, show_name.lower().strip()).ratio()
                    if match_ratio > highest_ratio:
                        highest_ratio = match_ratio
                        # Grab network or fall back to web stream channels
                        best_match = item.get("show", {}).get("network", {}).get("name") or \
                                     item.get("show", {}).get("webChannel", {}).get("name")
            except Exception:
                continue

        if best_match:
            _LOGGER.info("GuideManager: Match found! '%s' matched currently airing show on %s", media_title, best_match)
        return best_match