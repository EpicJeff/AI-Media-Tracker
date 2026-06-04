import logging
import asyncio
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

class GuideManager:
    """Manages live TV guide cross-referencing to improve channel identification."""

    def __init__(self, hass, api_key: str = None):
        self.hass = hass
        self.api_key = api_key
        self._cache = {} # {title: [channels]}

    async def get_candidate_channels(self, title: str) -> list[str]:
        """Fetch a list of channels currently airing a show with the given title."""
        _LOGGER.debug("Checking guide candidates for '%s'", title)
        if not title or len(title) < 3 or not self.api_key:
            return []

        if title in self._cache:
            return self._cache[title]

        session = async_get_clientsession(self.hass)
        candidates = []

        try:
            import datetime
            import urllib.parse
            
            # Step 1: Search for the program by title to get a tmsId
            safe_title = urllib.parse.quote(title)
            search_url = f"http://data.tmsapi.com/v1.1/programs/search?q={safe_title}&api_key={self.api_key}"
            async with session.get(search_url, timeout=10.0) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    tms_ids = [res.get("tmsId") for res in (results.get("hits", []) if isinstance(results, dict) else results)[:3] if res.get("tmsId")]
                else:
                    tms_ids = []

            # Step 2: Fetch airings for the identified programs
            if tms_ids:
                _LOGGER.debug("Guide candidate found for '%s'", title)
                # Look back 4 hours to catch shows that are currently airing but started earlier
                start_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=4)).strftime("%Y-%m-%dT%H:%MZ")
                
                for tms_id in tms_ids:
                    airings_url = f"http://data.tmsapi.com/v1.1/programs/{tms_id}/airings?startDateTime={start_time}&api_key={self.api_key}"
                    async with session.get(airings_url, timeout=10.0) as resp:
                        if resp.status == 200:
                            airings = await resp.json()
                            if isinstance(airings, list):
                                for airing in airings:
                                    station = airing.get("station", {})
                                    name = station.get("name") or station.get("callSign")
                                    if name:
                                        candidates.append(name)

            # Clean up and deduplicate
            candidates = list(set([c for c in candidates if c]))
            self._cache[title] = candidates
            _LOGGER.debug("Guide candidates for '%s': %s", title, candidates)
            return candidates

        except Exception as e:
            _LOGGER.error("Guide lookup failed for '%s': %s", title, e)
            return []
