import os
import re
import io
import time
import asyncio
import logging
import unicodedata
import hashlib
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

LEAGUE_MAP = {
    "premier league": ("soccer", "eng.1"),
    "english premier league": ("soccer", "eng.1"),
    "epl": ("soccer", "eng.1"),
    "la liga": ("soccer", "esp.1"),
    "laliga": ("soccer", "esp.1"),
    "serie a": ("soccer", "ita.1"),
    "bundesliga": ("soccer", "ger.1"),
    "ligue 1": ("soccer", "fra.1"),
    "mls": ("soccer", "usa.1"),
    "major league soccer": ("soccer", "usa.1"),
    "liga mx": ("soccer", "mex.1"),
    "champions league": ("soccer", "uefa.champions"),
    "uefa champions league": ("soccer", "uefa.champions"),
    "nfl": ("football", "nfl"),
    "national football league": ("football", "nfl"),
    "college football": ("football", "college-football"),
    "ncaa football": ("football", "college-football"),
    "nba": ("basketball", "nba"),
    "national basketball association": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "college basketball": ("basketball", "mens-college-basketball"),
    "ncaa basketball": ("basketball", "mens-college-basketball"),
    "ncaa mens basketball": ("basketball", "mens-college-basketball"),
    "college baseball": ("baseball", "college-baseball"),
    "ncaa baseball": ("baseball", "college-baseball"),
    "mlb": ("baseball", "mlb"),
    "major league baseball": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "national hockey league": ("hockey", "nhl"),
    "ipl": ("cricket", "ipl"),
    "indian premier league": ("cricket", "ipl"),
    "atp": ("tennis", "atp"),
    "wta": ("tennis", "wta"),
    "tennis": ("tennis", "atp")
}

POPULAR_LEAGUES_BY_SPORT = {
    "soccer": [("soccer", "eng.1"), ("soccer", "esp.1"), ("soccer", "usa.1"), ("soccer", "uefa.champions"), ("soccer", "mex.1")],
    "basketball": [("basketball", "nba"), ("basketball", "mens-college-basketball")],
    "football": [("football", "nfl"), ("football", "college-football")],
    "baseball": [("baseball", "mlb"), ("baseball", "college-baseball")],
    "hockey": [("hockey", "nhl")],
    "tennis": [("tennis", "atp"), ("tennis", "wta")]
}

DEFAULT_LEAGUES = [
    ("football", "nfl"), ("basketball", "nba"), ("baseball", "mlb"), ("hockey", "nhl"),
    ("tennis", "atp"), ("tennis", "wta")
]


APP_DOMAIN_MAP = {
    "espn": "espn.com",
    "netflix": "netflix.com",
    "hulu": "hulu.com",
    "amazon prime video": "primevideo.com",
    "disney+": "disneyplus.com",
    "directv": "direc.tv",
    "apple tv+": "apple.com",
    "tv": "apple.com",
    "youtube": "youtube.com",
    "youtube tv": "youtube.com",
    "paramount+": "paramount.com",
    "hbo max": "hbo.com",
    "peacock": "peacocktv.com"
}

class ArtworkManager:
    def __init__(self, hass, config_entry):
        self.hass = hass
        self.tmdb_key = config_entry.options.get("tmdb_api_key")
        self.logo_dev_key = config_entry.options.get("logo_dev_api_key")
        self.cache_dir = hass.config.path("www", "ai_media_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.local_base_url = "/local/ai_media_cache"
        self.font_path = hass.config.path("custom_components", "ai_media_tracker", "fonts", "arialbd.ttf")
        self._team_cache = {}
        self._url_cache = {}

    def _get_cache_key(self, prefix: str, value: str) -> str:
        return f"{prefix}_{hashlib.md5(str(value).lower().encode()).hexdigest()[:12]}"

    def _normalize_string(self, text: str) -> str:
        text = str(text or "")
        normalized = unicodedata.normalize('NFKD', text)
        cleaned = "".join([c for c in normalized if not unicodedata.combining(c)])
        return cleaned.lower().strip()

    def _clean_team_name(self, name: str) -> str:
        n = self._normalize_string(name)
        n = re.sub(r'\b(clube|club|fc|sc|ac|afc|cf|national team|team)\b', '', n)
        aliases = {"atletico mineiro": "atletico-mg"}
        return aliases.get(n.strip(), n.strip())

    async def async_get_artwork(self, media_title: str, channel_name: str = None, sport: str = None, league: str = None, competitors: list = None, team_overrides: dict = None) -> dict:
        session = async_get_clientsession(self.hass)
        assets = {"poster": None, "logo": None}

        known_channels = {"espn", "espn2", "fox sports", "fox", "abc", "nbc", "cbs", "tnt", "tbs", "hgtv", "food network", "cnn", "msnbc", "fox news", "directv"}
        if not channel_name and media_title.lower().strip() in known_channels:
            channel_name = media_title
            media_title = "Live Broadcast"

        clean_title = (media_title or "").strip()

        if channel_name:
            assets["logo"] = await self._fetch_channel_logo(session, channel_name)

        is_sport = bool(sport or league)
        strong_sports_pattern = re.split(r'\s+vs\.?\s+|\s+@\s+', clean_title, flags=re.IGNORECASE)
        weak_sports_pattern = re.split(r'\s+vs\.?\s+|\s+@\s+|\s+-\s+', clean_title, flags=re.IGNORECASE)
        
        if is_sport or len(strong_sports_pattern) >= 2 or competitors:
            pattern_to_use = weak_sports_pattern if (is_sport or competitors) else strong_sports_pattern
            t1 = competitors[0] if competitors and len(competitors) > 0 else (pattern_to_use[0].strip() if len(pattern_to_use) >= 2 else "Competitor 1")
            t2 = competitors[1] if competitors and len(competitors) > 1 else (pattern_to_use[1].strip() if len(pattern_to_use) >= 2 else "Competitor 2")
            bg_url, sports_data = await self._generate_sports_card(session, t1, t2, sport, league, team_overrides)
            assets["poster"] = bg_url
            assets["sports_data"] = sports_data
        elif self.tmdb_key and len(clean_title) > 3:
            tmdb_url = await self._fetch_tmdb_poster(session, clean_title)
            if tmdb_url: assets["poster"] = tmdb_url

        return assets

    async def _fetch_channel_logo(self, session, name: str) -> str | None:
        if not name: return None
        _LOGGER.debug("Fetching logo for '%s'", name)
        name_lower = name.lower().strip()
        name_lower = re.sub(r'\+', ' plus', name_lower)
        clean = re.sub(r'^the\s?', '', name_lower)
        clean = re.sub(r'[^a-z0-9]', '-', clean)
        _LOGGER.debug("Cleaned logo name for '%s': %s", name, clean)
        #clean = re.sub(r'[^a-z0-9]', '', name_lower)
                
        cache_key = self._get_cache_key("logo", clean)
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]

        # Check for logo in cache
        local_logo_dir = self.hass.config.path("www", "ai_media_cache", "logos")
        current = clean;
        while current != "":
            for ext in [".png", ".jpg", ".jpeg"]:
                local_path = os.path.join(local_logo_dir, f"{clean}-us{ext}")
                if os.path.exists(local_path):
                    _LOGGER.debug("Found logo cache for '%s': %s", name, local_path)
                    url = f"{self.local_base_url}/logos/{clean}-us{ext}"
                    self._url_cache[cache_key] = url
                    return url

            current = re.sub(r'-?[a-z0-9]*$', '', current)

        # Try Logo.Dev for logo
        if(self.logo_dev_key):
            remote_url = f"https://img.logo.dev/name/{name_lower}?token={self.logo_dev_key}&format=png&size=500&retina=true"
            try:
                async with session.get(remote_url, timeout=10.0) as resp:
                    _LOGGER.debug("Fetching remote logo for '%s': %s", name, remote_url)
                    if resp.status == 200:
                        _LOGGER.debug("Remote logo service responded for '%s': %s", name, remote_url)
                        img_bytes = await resp.read()
                        _LOGGER.debug("Read logo bytes for '%s': %d", name, len(img_bytes))
                        disk_cache_path = os.path.join(local_logo_dir, f"{clean}-us.png")
                        _LOGGER.debug("logo cache path for '%s': %s", name, disk_cache_path)
                        _LOGGER.debug("saving logo for '%s': %s", name, disk_cache_path)
                        await self.hass.async_add_executor_job(self._save_as_png, img_bytes, disk_cache_path)
                        _LOGGER.debug("logo saved for '%s': %s", name, disk_cache_path)
                        url = f"{self.local_base_url}/logos/{clean}-us.png"
                        _LOGGER.debug("caching url for '%s': %s", name, url)
                        self._url_cache[cache_key] = url
                        _LOGGER.debug("logo cahced for '%s': %s", name, cache_key)
                        return url
            except Exception: pass

        # fallback to apistemic logo
        app_domain = APP_DOMAIN_MAP.get(name.lower().strip())
        if not app_domain: return None

        remote_url = f"https://logos-api.apistemic.com/domain:{app_domain}"
        try:
            async with session.get(remote_url, headers={"User-Agent": "SectionJ (info@sectionj.net)"}, timeout=10.0) as resp:
                _LOGGER.debug("Fetching remote logo for '%s': %s", name, remote_url)
                if resp.status == 200:
                    _LOGGER.debug("Remote logo service responded for '%s': %s", name, remote_url)
                    img_bytes = await resp.read()
                    _LOGGER.debug("Read logo bytes for '%s': %d", name, len(img_bytes))
                    disk_cache_path = os.path.join(local_logo_dir, f"{clean}-us.png")
                    _LOGGER.debug("logo cache path for '%s': %s", name, disk_cache_path)
                    _LOGGER.debug("saving logo for '%s': %s", name, disk_cache_path)
                    
                    def _process_transparency():
                        try:
                            from PIL import Image
                            import io
                            image = Image.open(io.BytesIO(img_bytes))
                            image = image.convert("RGBA")
                            datas = image.getdata()
                            new_data = []
                            for item in datas:
                                if item[0] > 240 and item[1] > 240 and item[2] > 240:
                                    new_data.append((255, 255, 255, 0))
                                else:
                                    new_data.append(item)
                            image.putdata(new_data)
                            image.save(disk_cache_path, "PNG")
                        except Exception as e:
                            _LOGGER.error("ArtworkManager: Failed to process transparency: %s", e)
                            self._save_as_png(img_bytes, disk_cache_path)
                            
                    await self.hass.async_add_executor_job(_process_transparency)
                    _LOGGER.debug("logo saved for '%s': %s", name, disk_cache_path)
                    url = f"{self.local_base_url}/logos/{clean}-us.png"
                    _LOGGER.debug("caching url for '%s': %s", name, url)
                    self._url_cache[cache_key] = url
                    _LOGGER.debug("logo cahced for '%s': %s", name, cache_key)
                    return url
        except Exception: pass

        return None

    def _read_local_bg_sync(self, path: str) -> bytes | None:
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    _LOGGER.debug("Opened local background: %s", path)
                    return f.read()
            except Exception as e:
                _LOGGER.warning("Failed to read local background: %s", e)
        return None

    async def _generate_sports_card(self, session, t1, t2, sport: str = None, league: str = None, team_overrides: dict = None) -> str:
        is_individual = str(sport).lower() in ["tennis", "golf", "mma", "boxing"]
        leagues_to_search = []
        
        if league:
            clean_league = re.sub(r"[^a-z0-9\s]", "", league.lower().strip())
            mapped = LEAGUE_MAP.get(clean_league)
            if mapped: leagues_to_search.append(mapped)

        if not leagues_to_search and sport:
            mapped_list = POPULAR_LEAGUES_BY_SPORT.get(sport.lower().strip())
            if mapped_list: leagues_to_search.extend(mapped_list)
        if not leagues_to_search: leagues_to_search.extend(DEFAULT_LEAGUES)

        name1 = t1.get("name", str(t1)) if isinstance(t1, dict) else str(t1)
        name2 = t2.get("name", str(t2)) if isinstance(t2, dict) else str(t2)

        if team_overrides:
            data1 = team_overrides.get("t1", {})
            data2 = team_overrides.get("t2", {})
        elif is_individual:
            data1 = await self._search_espn_athlete(session, t1, leagues_to_search, sport)
            data2 = await self._search_espn_athlete(session, t2, leagues_to_search, sport)
        else:
            data1 = await self._search_espn_team(session, name1, leagues_to_search)
            data2 = await self._search_espn_team(session, name2, leagues_to_search)

        sports_data = {
            "sport": sport,
            "is_individual": is_individual,
            "away_team": name1,
            "home_team": name2,
            "away_full_name": name1,
            "home_full_name": name2,
            "away_logo": data1.get("logo", ""),
            "home_logo": data2.get("logo", ""),
            "away_color": f"#{data1.get('color', '2c3e50')}",
            "home_color": f"#{data2.get('color', '34495e')}"
        }

        bg_url = self._get_generic_sport_bg(sport)
        
        if not is_individual:
            t2_clean = re.sub(r'[^a-zA-Z0-9]', '_', name2).lower()
            local_bg = os.path.join(self.cache_dir, "backgrounds", f"{t2_clean}.jpg")
            
            def _check_exists():
                return os.path.exists(local_bg)
            
            if await self.hass.async_add_executor_job(_check_exists):
                bg_url = f"/local/ai_media_cache/backgrounds/{t2_clean}.jpg"
                
        sports_data["background_url"] = bg_url
            
        return bg_url, sports_data

    def _get_generic_sport_bg(self, sport: str) -> str:
        bgs = {
            "tennis": f"{self.local_base_url}/backgrounds/tennis_bg.jpg",
            "baseball": f"{self.local_base_url}/backgrounds/college_baseball_bg.jpg",
            "football": f"{self.local_base_url}/backgrounds/college_football_bg.jpg",
            "basketball": f"{self.local_base_url}/backgrounds/college_basketball_bg.jpg",
            "soccer": f"{self.local_base_url}/backgrounds/soccer_bg.jpg",
            "hockey": f"{self.local_base_url}/backgrounds/hockey_bg.jpg",
            "golf": f"{self.local_base_url}/backgrounds/golf_bg.jpg"
        }
        return bgs.get(str(sport).lower(), f"{self.local_base_url}/backgrounds/generic_bg.jpg")

    async def _search_espn_athlete(self, session, competitor, leagues: list, sport: str = None) -> dict:
        if isinstance(competitor, dict):
            name = competitor.get("name", "")
            target_country = competitor.get("country")
            target_seed = competitor.get("seed")
        else:
            name = str(competitor)
            target_country = None
            target_seed = None
        _LOGGER.debug("Starting Athlete search: '%s'", name)

        import urllib.parse
        target = urllib.parse.quote(name)
        try:
            url = f"https://site.api.espn.com/apis/search/v2?query={target}&limit=15"
            async with session.get(url, timeout=15.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    matches = []
                    for result in data.get("results", []):
                        for content in result.get("contents", []):
                            if content.get("type") == "player":
                                c_sport = content.get("sport", "")
                                c_league = content.get("defaultLeagueSlug", "")
                                _LOGGER.debug("Athlete found: '%s'", content.get("displayName", "Unknown"))
                                
                                is_match = False
                                if sport and c_sport and sport.lower() == c_sport.lower():
                                    is_match = True
                                elif leagues and c_league:
                                    league_strs = [l[1].lower() if isinstance(l, tuple) else l.lower() for l in leagues]
                                    if c_league.lower() in league_strs:
                                        is_match = True
                                elif not sport and not leagues:
                                    is_match = True
                                    
                                if is_match:
                                    if " " not in name.strip():
                                        display_name = content.get("displayName", "Unknown").strip()
                                        last_name = display_name.split(" ")[-1]
                                        if name.strip().lower() != last_name.lower():
                                            is_match = False
                                            
                                if is_match and name.lower() == content.get("displayName", "Unknown").lower():
                                    img = content.get("image", {}).get("default")
                                    if img:
                                        return {"logo": img, "color": "111111"}

                                if is_match:
                                    _LOGGER.debug("Athlete match found: '%s'", content.get("displayName", "Unknown"))
                                    matches.append(content)
                                    
                    if len(matches) == 1:
                        img = matches[0].get("image", {}).get("default")
                        if img:
                            return {"logo": img, "color": "111111"}
                    elif len(matches) > 1:
                        _LOGGER.debug("Multiple athletes found for '%s' in sport '%s', falling back to text", name, sport)
                        #for match in matches:
                        #    _LOGGER.debug("Athlete found: '%s'", match.get("displayName", "Unknown"))
                        return {}
                else:
                    _LOGGER.debug("Athlete search response: '%d'", resp.status)
        except Exception as e:
            _LOGGER.error("Athlete search failed: %s", e)
        return {}

    async def _search_espn_team(self, session, team, leagues) -> dict:
        target = self._clean_team_name(team)
        for s, l in leagues:
            teams = await self._get_cached_teams(session, s, l)
            for m in teams:
                if target in [self._normalize_string(m.get(k)) for k in ["displayName", "name", "location"]]:
                    logos = m.get("logos", [])
                    url = logos[0].get("href", "").split('?')[0] + "?h=500&w=500&format=png" if logos else None
                    return {"logo": url, "color": m.get("color", "2c3e50")}
        return {}

    async def _get_cached_teams(self, session, sport, league) -> list:
        if (sport, league) in self._team_cache: return self._team_cache[(sport, league)]
        try:
            async with session.get(f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams?limit=1000", timeout=10.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    teams = [t["team"] for t in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])]
                    self._team_cache[(sport, league)] = teams
                    return teams
        except Exception: pass
        return []



    async def _fetch_tmdb_poster(self, session, title: str) -> str | None:
        cache_key = self._get_cache_key("tmdb", title)
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]

        filename = f"{cache_key}.jpg"
        local_path = os.path.join(self.cache_dir, filename)
        if os.path.exists(local_path):
            url = f"{self.local_base_url}/{filename}"
            self._url_cache[cache_key] = url
            return url

        try:
            async with session.get(f"https://api.themoviedb.org/3/search/multi?api_key={self.tmdb_key}&query={title}", timeout=10.0) as resp:
                data = await resp.json()
                if data.get("results") and data["results"][0].get("poster_path"):
                    remote_url = f"https://image.tmdb.org/t/p/w1280{data['results'][0]['backdrop_path'] or data['results'][0]['poster_path']}"
                    async with session.get(remote_url) as img_resp:
                        if img_resp.status == 200:
                            img_bytes = await img_resp.read()
                            await self.hass.async_add_executor_job(self._save_bytes, img_bytes, local_path)
                            url = f"{self.local_base_url}/{filename}"
                            self._url_cache[cache_key] = url
                            return url
        except Exception as e:
            _LOGGER.debug("TMDB fetch failed: %s", e)
        return None

    def _save_bytes(self, data: bytes, path: str):
        with open(path, "wb") as f:
            f.write(data)

    def _save_as_png(self, data: bytes, path: str):
        try:
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(data)).convert("RGBA")
            
            width, height = image.size
            if width > 0 and height > 0:
                tl = image.getpixel((0, 0))
                tr = image.getpixel((width - 1, 0))
                bl = image.getpixel((0, height - 1))
                br = image.getpixel((width - 1, height - 1))
                
                # If all 4 corners match exactly, assume it's a solid background
                if tl == tr == bl == br and tl[3] > 0:
                    bg = tl
                    # Convert to transparent if it matches the background color closely
                    data_list = image.getdata()
                    new_data = []
                    for p in data_list:
                        # Tolerance of 5 for slight JPEG/WebP artifacting
                        if abs(p[0] - bg[0]) < 6 and abs(p[1] - bg[1]) < 6 and abs(p[2] - bg[2]) < 6:
                            new_data.append((255, 255, 255, 0))
                        else:
                            new_data.append(p)
                    image.putdata(new_data)
                    
            image.save(path, "PNG")
        except Exception as e:
            _LOGGER.error("Failed to convert image to PNG, falling back to raw save: %s", e)
            self._save_bytes(data, path)