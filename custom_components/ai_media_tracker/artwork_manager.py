import os
import re
import io
import time
import asyncio
import logging
import difflib
import unicodedata
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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
    "college baseball": ("football", "college-baseall"),
    "ncaa baseball": ("football", "college-baseall"),
    "college football": ("football", "college-football"),
    "ncaa football": ("football", "college-football"),
    "nba": ("basketball", "nba"),
    "national basketball association": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "college basketball": ("basketball", "mens-college-basketball"),
    "ncaa basketball": ("basketball", "mens-college-basketball"),
    "ncaa mens basketball": ("basketball", "mens-college-basketball"),
    "mlb": ("baseball", "mlb"),
    "major league baseball": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "national hockey league": ("hockey", "nhl"),
    "ipl": ("cricket", "ipl"),
    "indian premier league": ("cricket", "ipl"),
}

POPULAR_LEAGUES_BY_SPORT = {
    "soccer": [
        ("soccer", "eng.1"), ("soccer", "esp.1"), ("soccer", "usa.1"),
        ("soccer", "uefa.champions"), ("soccer", "mex.1"), ("soccer", "ita.1"),
        ("soccer", "ger.1"), ("soccer", "fra.1"),
        ("soccer", "bra.1"), ("soccer", "fifa.friendly"), ("soccer", "global.1"),
    ],
    "basketball": [("basketball", "nba"), ("basketball", "mens-college-basketball"), ("basketball", "wnba")],
    "football": [("football", "nfl"), ("football", "college-football")],
    "baseball": [("baseball", "mlb")],
    "hockey": [("hockey", "nhl")],
    "cricket": [("cricket", "ipl")],
}

DEFAULT_LEAGUES = [
    ("football", "nfl"), ("basketball", "nba"), ("baseball", "mlb"), ("hockey", "nhl"),
    ("soccer", "eng.1"), ("soccer", "esp.1"), ("soccer", "usa.1")
]

class ArtworkManager:
    """Manages the artwork pipeline, team caching, and high-end card generation."""

    def __init__(self, hass, config_entry):
        self.hass = hass
        self.tmdb_key = config_entry.options.get("tmdb_api_key")
        self.cache_dir = hass.config.path("www", "ai_media_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.local_base_url = "/local/ai_media_cache"
        self.font_path = hass.config.path(
            "custom_components", "ai_media_tracker", "fonts", "arialbd.ttf"
        )
        self._team_cache = {}

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

    async def async_get_artwork(self, media_title: str, channel_name: str = None, sport: str = None, league: str = None) -> dict:
        """Resolve artwork assets (poster and logo) independently."""
        session = async_get_clientsession(self.hass)
        
        known_channels = {"espn", "espn2", "fox sports", "fox", "abc", "nbc", "cbs", "tnt", "tbs", "hgtv", "food network", "cnn", "msnbc", "fox news", "directv"}
        if not channel_name and media_title.lower().strip() in known_channels:
            channel_name = media_title
            media_title = "Live Broadcast"
            
        clean_title = (media_title or "").strip()
        assets = {"poster": None, "logo": None}

        # 1. Resolve Channel Logo (Always attempt)
        if channel_name:
            assets["logo"] = await self._fetch_channel_logo(session, channel_name)
            # If we don't have a poster yet, use the logo card as the primary poster
            if not assets["poster"] and assets["logo"]:
                assets["poster"] = await self._generate_channel_card(session, channel_name, assets["logo"], title=clean_title)

        # 2. Resolve Poster Art (Sports or TMDB)
        sports_pattern = re.split(r'\s+vs\.?\s+|\s+@\s+|\s+-\s+', clean_title, flags=re.IGNORECASE)
        if len(sports_pattern) >= 2:
            assets["poster"] = await self._generate_sports_card(session, sports_pattern[0].strip(), sports_pattern[1].strip(), sport, league)
        elif self.tmdb_key and len(clean_title) > 3:
            tmdb_url = await self._fetch_tmdb_poster(session, clean_title)
            if tmdb_url: assets["poster"] = tmdb_url

        # 3. Final Fallback for poster
        if not assets["poster"]:
            assets["poster"] = await self._generate_text_card(clean_title or "No Metadata")
            
        return assets

    async def _fetch_channel_logo(self, session, name: str) -> str | None:
        """Search local cache, then IPTV-org for the channel/app logo."""
        if not name: return None
        name_lower = name.lower().strip()
        clean = re.sub(r'[^a-z0-9]', '', name_lower)
        _LOGGER.debug("Searching for logo for name: '%s', cleaned: '%s'", name, clean)
        
        # Source 0: Local Logos Folder
        local_logo_dir = self.hass.config.path("www", "ai_media_cache", "logos")
        for ext in [".png", ".jpg", ".jpeg"]:
            local_path = os.path.join(local_logo_dir, f"{clean}{ext}")
            if os.path.exists(local_path):
                _LOGGER.info("Logo found locally for '%s' at: %s", name, local_path)
                return f"{self.local_base_url}/logos/{clean}{ext}"

        
        # Source 1: IPTV-org
        variants = [
            f"https://raw.githubusercontent.com/iptv-org/logos/master/channels/png_512/{clean}.png",
            f"https://raw.githubusercontent.com/iptv-org/logos/master/channels/png_100/{clean}.png",
            f"https://raw.githubusercontent.com/iptv-org/logos/master/hosts/tv/{clean}.png"
        ]
        _LOGGER.debug("Starting IPTV-org logo search for '%s' with variants: %s", name, variants)
        for url in variants:
            _LOGGER.debug("Attempting logo URL: %s", url)
            try:
                async with session.head(url, timeout=3.0) as resp:
                    _LOGGER.debug("Logo URL '%s' returned status: %s", url, resp.status)
                    if resp.status == 200: 
                        _LOGGER.info("Logo found via IPTV-org for '%s': %s", name, url)
                        return url
            except Exception as e:
                _LOGGER.debug("IPTV-org attempt failed for '%s': %s", url, e)
            
        _LOGGER.warning("No logo found for channel/app: '%s'. If you have a local logo, place it in %s as %s.png", name, local_logo_dir, clean)
        return None

    async def _generate_sports_card(self, session, t1: str, t2: str, sport: str = None, league: str = None) -> str:
        leagues_to_search = []
        if league:
            clean_league = re.sub(r"[^a-z0-9\s]", "", league.lower().strip())
            mapped = LEAGUE_MAP.get(clean_league)
            if mapped: leagues_to_search.append(mapped)
            else:
                best_match = difflib.get_close_matches(clean_league, LEAGUE_MAP.keys(), n=1, cutoff=0.7)
                if best_match: leagues_to_search.append(LEAGUE_MAP[best_match[0]])

        if not leagues_to_search and sport:
            mapped_list = POPULAR_LEAGUES_BY_SPORT.get(sport.lower().strip())
            if mapped_list: leagues_to_search.extend(mapped_list)
        if not leagues_to_search: leagues_to_search.extend(DEFAULT_LEAGUES)

        data1 = await self._search_espn_team(session, t1, leagues_to_search)
        data2 = await self._search_espn_team(session, t2, leagues_to_search)

        async def _fetch_img(url):
            if not url: return None
            try:
                async with session.get(url, timeout=10.0) as resp:
                    if resp.status == 200: return await resp.read()
            except Exception: pass
            return None

        b1, b2 = await asyncio.gather(_fetch_img(data1.get("logo")), _fetch_img(data2.get("logo")))
        
        filename = f"card_{re.sub(r'[^a-zA-Z0-9]', '_', t1).lower()}_{re.sub(r'[^a-zA-Z0-9]', '_', t2).lower()}.jpg"
        path = os.path.join(self.cache_dir, filename)

        await self.hass.async_add_executor_job(self._compile_canvas, b1, b2, t1, t2, path, 
                                             f"#{data1.get('color', '2c3e50')}", f"#{data2.get('color', '34495e')}")
        return f"{self.local_base_url}/{filename}?t={int(time.time())}"

    async def _generate_channel_card(self, session, channel_name: str, logo_url: str | None, title: str = None) -> str:
        img_bytes = None
        if logo_url:
            try:
                async with session.get(logo_url, timeout=10.0) as resp:
                    if resp.status == 200: img_bytes = await resp.read()
            except Exception: pass
        filename = f"card_{re.sub(r'[^a-z0-9]', '', channel_name.lower())}_{re.sub(r'[^a-z0-9]', '', (title or '').lower())[:15]}.jpg"
        path = os.path.join(self.cache_dir, filename)
        await self.hass.async_add_executor_job(self._compile_channel_canvas, img_bytes, title or channel_name, path)
        return f"{self.local_base_url}/{filename}?t={int(time.time())}"

    async def _generate_text_card(self, text: str) -> str:
        filename = f"text_{re.sub(r'[^a-z0-9]', '', text.lower())[:20]}.jpg"
        path = os.path.join(self.cache_dir, filename)
        if not os.path.exists(path): await self.hass.async_add_executor_job(self._compile_text_canvas, text, path)
        return f"{self.local_base_url}/{filename}"

    def _compile_canvas(self, b1, b2, t1, t2, path, c1, c2):
        width, height = 1920, 1080
        canvas = Image.new("RGB", (width, height), "#1a1a1a")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, width // 2, height], fill=c1)
        draw.rectangle([width // 2, 0, width, height], fill=c2)
        
        def _to_img(b):
            try: return Image.open(io.BytesIO(b)).convert("RGBA")
            except Exception: return None
        logo1, logo2 = _to_img(b1), _to_img(b2)
        font = ImageFont.truetype(self.font_path, 72) if os.path.exists(self.font_path) else ImageFont.load_default()
        
        if logo1:
            logo1.thumbnail((500, 500), Image.Resampling.LANCZOS)
            l1 = self._apply_logo_effects(logo1)
            canvas.paste(l1, (480 - l1.width // 2, 540 - l1.height // 2), l1)
        else:
            txt = t1.upper()
            bbox = draw.textbbox((0, 0), txt, font=font)
            draw.text((480 - (bbox[2]-bbox[0]) // 2, 540 - (bbox[3]-bbox[1]) // 2), txt, font=font, fill="white")
        if logo2:
            logo2.thumbnail((500, 500), Image.Resampling.LANCZOS)
            l2 = self._apply_logo_effects(logo2)
            canvas.paste(l2, (1440 - l2.width // 2, 540 - l2.height // 2), l2)
        else:
            txt = t2.upper()
            bbox = draw.textbbox((0, 0), txt, font=font)
            draw.text((1440 - (bbox[2]-bbox[0]) // 2, 540 - (bbox[3]-bbox[1]) // 2), txt, font=font, fill="white")
        canvas.save(path, "JPEG", quality=95)

    def _compile_channel_canvas(self, logo_bytes, title, save_path):
        width, height = 1920, 1080
        logo = None
        if logo_bytes:
            try: logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            except Exception: pass
        
        dom_color = self._get_dominant_color(logo) if logo else "#1a1a1a"
        canvas = Image.new("RGB", (width, height), dom_color)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 200))
        canvas.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(self.font_path, 100) if os.path.exists(self.font_path) else ImageFont.load_default()
        
        txt = title.upper()
        bbox = draw.textbbox((0, 0), txt, font=font)
        txt_w, txt_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        
        if logo:
            logo.thumbnail((500, 500), Image.Resampling.LANCZOS)
            le = self._apply_logo_effects(logo)
            group_w = le.width + 80 + txt_w
            start_x = (width - group_w) // 2
            canvas.paste(le, (start_x, (height - le.height) // 2), le)
            draw.text((start_x + le.width + 80, (height - txt_h) // 2 - 15), txt, font=font, fill="white")
        else:
            draw.text(((width - txt_w) // 2, (height - txt_h) // 2), txt, font=font, fill="white")
        canvas.save(save_path, "JPEG", quality=90)

    def _compile_text_canvas(self, text, save_path):
        width, height = 1920, 1080
        canvas = Image.new("RGB", (width, height), "#121212")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(self.font_path, 80) if os.path.exists(self.font_path) else ImageFont.load_default()
        txt = text.upper()
        bbox = draw.textbbox((0, 0), txt, font=font)
        draw.text(((width - (bbox[2]-bbox[0])) // 2, (height - (bbox[3]-bbox[1])) // 2), txt, font=font, fill="#555555")
        canvas.save(save_path, "JPEG", quality=85)

    def _get_dominant_color(self, pil_image, default="#222222") -> str:
        small_img = pil_image.resize((32, 32))
        pixels = small_img.getdata()
        counts = {}
        for p in pixels:
            if len(p) == 4 and p[3] < 150: continue
            if (p[0] > 240 and p[1] > 240 and p[2] > 240) or (p[0] < 20 and p[1] < 20 and p[2] < 20): continue
            counts[p[:3]] = counts.get(p[:3], 0) + 1
        return f"#{max(counts, key=counts.get)[0]:02x}{max(counts, key=counts.get)[1]:02x}{max(counts, key=counts.get)[2]:02x}" if counts else default

    def _apply_logo_effects(self, logo):
        if logo.mode != "RGBA": logo = logo.convert("RGBA")
        pad = 150
        base = Image.new("RGBA", (logo.width + pad * 2, logo.height + pad * 2), (0, 0, 0, 0))
        alpha = logo.split()[3]
        shadow = Image.new("L", base.size, 0)
        shadow.paste(alpha, (pad, pad))
        shadow = shadow.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(35))
        base.paste(Image.new("RGBA", base.size, (0, 0, 0, 160)), (0, 0), shadow)
        outline = Image.new("L", base.size, 0)
        outline.paste(alpha, (pad, pad))
        outline = outline.filter(ImageFilter.MaxFilter(13))
        base.paste(Image.new("RGBA", base.size, (255, 255, 255, 255)), (0, 0), outline)
        base.paste(logo, (pad, pad), logo)
        return base

    async def _search_espn_team(self, session, team, leagues) -> dict:
        target = self._clean_team_name(team)
        for s, l in leagues:
            teams = await self._get_cached_teams(session, s, l)
            for m in teams:
                if target in [self._normalize_string(m.get(k)) for k in ["displayName", "name", "location"]]:
                    logos = m.get("logos", [])
                    url = logos[0].get("href", "").split('?')[0] + "?h=500&w=500&format=png" if logos else None
                    return {"logo": url, "color": m.get("color")}
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

    async def _fetch_tmdb_poster(self, session, title) -> str | None:
        try:
            async with session.get(f"https://api.themoviedb.org/3/search/multi?api_key={self.tmdb_key}&query={title}", timeout=10.0) as resp:
                data = await resp.json()
                if data.get("results") and data["results"][0].get("poster_path"):
                    return f"https://image.tmdb.org/t/p/w1280{data['results'][0]['backdrop_path'] or data['results'][0]['poster_path']}"
        except Exception: pass
        return None
