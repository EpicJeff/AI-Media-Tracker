import asyncio
import logging
import re
import difflib
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ESPN_LEAGUE_MAP = {
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "mens-college-basketball": ("basketball", "mens-college-basketball"),
    "nfl": ("football", "nfl"),
    "college-football": ("football", "college-football"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "mls": ("soccer", "usa.1"),
    "premier league": ("soccer", "eng.1"),
    "champions league": ("soccer", "uefa.champions"),
    "atp": ("tennis", "atp"),
    "wta": ("tennis", "wta"),
    "tennis": ("tennis", "atp"),
    "pga tour": ("golf", "pga"),
    "pga": ("golf", "pga"),
    "lpga": ("golf", "lpga"),
    "champions tour": ("golf", "champions")
}

class SportsManager:
    def __init__(self, hass, entry_id):
        self.hass = hass
        self.entry_id = entry_id
        self._active_tasks = {}
        self._tracked_events = {}

    async def _verify_image_url(self, url: str) -> str:
        if not url: return ""
        try:
            session = async_get_clientsession(self.hass)
            async with session.head(url, timeout=2) as response:
                if response.status == 200:
                    return url
        except Exception:
            pass
        return ""

    def start_tracking(self, player_name: str, sport: str, league: str, title: str, competitors: list = None, game_state: dict = None):
        if not sport or not title:
            return

        league_val = league or sport
        league_clean = re.sub(r"[^a-z0-9\-\s]", "", league_val.lower().strip())
        mapped_sport, mapped_league = ESPN_LEAGUE_MAP.get(league_clean, (sport.lower().strip(), league_clean))

        current_event = (mapped_sport, mapped_league, title)
        if self._tracked_events.get(player_name) == current_event:
            self.update_game_state(player_name, game_state or {})
            return

        self.stop_tracking(player_name)
        self._tracked_events[player_name] = current_event

        _LOGGER.info("SportsManager: Starting live sync for %s on %s (%s/%s)", title, player_name, mapped_sport, mapped_league)
        
        task = self.hass.async_create_task(
            self._tracking_loop(player_name, mapped_sport, mapped_league, title, competitors or [], game_state or {})
        )
        self._active_tasks[player_name] = task

    def stop_tracking(self, player_name: str):
        if player_name in self._active_tasks:
            _LOGGER.info("SportsManager: Stopping live sync for %s", player_name)
            self._active_tasks[player_name].cancel()
            self._active_tasks.pop(player_name)
        self._tracked_events.pop(player_name, None)

    def stop_all(self):
        for player_name in list(self._active_tasks.keys()):
            self.stop_tracking(player_name)

    def update_game_state(self, player_name: str, game_state: dict):
        if player_name in self._active_tasks:
            meta_mgr = self.hass.data[DOMAIN][self.entry_id]["metadata_mgr"]
            if meta_mgr.exists(player_name):
                state = meta_mgr.get(player_name)
                if getattr(state, "sports_data", None) is not None:
                    state.sports_data["vlm_game_state"] = game_state
                    if p := self.hass.data[DOMAIN][self.entry_id].get("players", {}).get(player_name):
                        p.async_write_ha_state()

    def clear_sports_data(self, player_name: str):
        meta_mgr = self.hass.data[DOMAIN][self.entry_id]["metadata_mgr"]
        if not meta_mgr.exists(player_name): return
        state = meta_mgr.get(player_name)
        state.sports_data = {}

    async def _tracking_loop(self, player_name: str, sport: str, league: str, title: str, competitors: list, game_state: dict):
        session = async_get_clientsession(self.hass)
        
        leagues_to_try = [league]
        if not league or league.lower() in ["null", "ncaa", "college"]:
            if sport == "baseball":
                leagues_to_try = ["mlb", "college-baseball"]
            elif sport == "football":
                leagues_to_try = ["nfl", "college-football"]
            elif sport == "basketball":
                leagues_to_try = ["nba", "mens-college-basketball", "womens-college-basketball", "wnba"]
            elif sport == "hockey":
                leagues_to_try = ["nhl"]
            elif sport == "soccer":
                leagues_to_try = ["eng.1", "usa.1", "uefa.champions", "esp.1", "ita.1", "ger.1"]
            elif sport == "golf":
                leagues_to_try = ["pga", "lpga", "liv"]
        
        if sport == "tennis" and league not in ["atp", "wta"]:
            leagues_to_try = ["atp", "wta"]

        event_id = None
        comp_id = None
        active_league = league
        base_url = ""

        import datetime
        for l in leagues_to_try:
            _LOGGER.warning("SportsManager: looking for live match matching '%s' '%s'", sport, l)
            base_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{l}/scoreboard"
            
            # 1. Search today
            event_id, comp_id, matched_event = await self._find_event_id(session, base_url, title, competitors, sport in ["tennis", "golf"])
            
            # 2. Search past days if not found
            if not event_id:
                lookback = 14 if sport in ["tennis", "golf"] else 3
                for days_ago in range(1, lookback + 1):
                    _LOGGER.warning("SportsManager: looking for recent match matching '%s' '%s' %d days ago", sport, l, days_ago)
                    past_date = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime("%Y%m%d")
                    past_url = f"{base_url}?dates={past_date}"
                    event_id, comp_id, matched_event = await self._find_event_id(session, past_url, title, competitors, sport in ["tennis", "golf"])
                    if event_id:
                        break
                        
            if event_id:
                active_league = l
                break
        
        if not event_id:
            _LOGGER.warning("SportsManager: Could not find a live or recent match matching '%s'", title)
            await self._build_offline_sports_data(player_name, sport, league, competitors)
            return

        _LOGGER.info("SportsManager: Successfully locked onto ESPN Event ID %s for %s", event_id, title)
        event_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{active_league}/scoreboard/{event_id}"

        try:
            while True:
                data = None
                if sport in ["tennis", "golf"]:
                    data = matched_event
                else:
                    async with session.get(event_url, timeout=10.0) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            
                if data:
                    all_comps = data.get("competitions", [])
                    for group in data.get("groupings", []):
                        all_comps.extend(group.get("competitions", []))
                        
                    status_state = all_comps[0].get("status", {}).get("type", {}).get("state") if all_comps else None
                    if comp_id:
                        for c in all_comps:
                            if c.get("id") == comp_id:
                                status_state = c.get("status", {}).get("type", {}).get("state")
                                break
                    
                    if status_state == "post":
                        _LOGGER.info("SportsManager: Event %s is final. Freezing polling loop.", event_id)
                        await self._process_and_publish_score(player_name, data, sport in ["tennis", "golf"], comp_id, sport, game_state)
                        self._active_tasks.pop(player_name, None)
                        break
                            
                    await self._process_and_publish_score(player_name, data, sport in ["tennis", "golf"], comp_id, sport, game_state)
                    
                if sport in ["tennis", "golf"]:
                    # Re-fetch scoreboard for live updates
                    async with session.get(base_url, timeout=10.0) as resp:
                        if resp.status == 200:
                            sb = await resp.json()
                            for e in sb.get("events", []):
                                if e.get("id") == event_id:
                                    matched_event = e
                                    break
                await asyncio.sleep(15)

        except asyncio.CancelledError:
            self.clear_sports_data(player_name)
            self._trigger_ui_update(player_name)
        except Exception as e:
            _LOGGER.error("SportsManager: Exception in loop for %s: %s", player_name, e)

    async def _find_event_id(self, session, url: str, target_title: str, competitors: list, is_individual: bool) -> str | None:
        try:
            search_tokens = []
            if competitors:
                for c in competitors:
                    c_str = c.get("name", str(c)) if isinstance(c, dict) else str(c)
                    search_tokens.append(c_str.lower().strip())
                    
            if not search_tokens or is_individual:
                title_words = [w for w in re.split(r'\s+vs\.?\s+|\s+@\s+|\s+-\s+', target_title.lower()) if len(w) > 2]
                search_tokens.extend(title_words)
            
            if not search_tokens: return None
            
            async with session.get(url, timeout=20.0) as resp:
                if resp.status != 200: 
                    _LOGGER.warning("SportsManager: find event id response: '%s' '%d' ", url, resp.status)
                    return None
                data = await resp.json()
                
                for event in data.get("events", []):
                    event_name = event.get("name", "").lower()
                    event_short = event.get("shortName", "").lower()
                    _LOGGER.warning("SportsManager: found event : '%s' '%s' ", event_name, event_short)
                    
                    # Fuzzy match: token overlap on event title
                    event_tokens = set(re.findall(r'\b\w+\b', event_name + " " + event_short))
                    search_words = set(re.findall(r'\b\w+\b', " ".join(search_tokens)))
                    common_words = [w for w in event_tokens.intersection(search_words) if len(w) > 3]
                    
                    # Flatten competitions since tennis nests them under groupings
                    all_comps = event.get("competitions", [])
                    for group in event.get("groupings", []):
                        all_comps.extend(group.get("competitions", []))
                        
                    if common_words and not competitors:
                        if is_individual and event.get("sport", {}).get("slug", "tennis") in ["tennis", "wta", "atp"]:
                            pass # For tennis, never blindly lock onto the first match
                        else:
                            comp_id = all_comps[0].get("id") if all_comps else None
                            return event.get("id"), comp_id, event
                        
                    for comp in all_comps:
                        matched_entries = 0
                        for entry in comp.get("competitors", []):
                            names_to_check = []
                            if is_individual:
                                ath = entry.get("athlete", {})
                                names_to_check = [ath.get("displayName", "").lower(), ath.get("shortName", "").lower()]
                            else:
                                team = entry.get("team", {})
                                names_to_check = [team.get("displayName", "").lower(), team.get("shortDisplayName", "").lower(), team.get("abbreviation", "").lower()]
                            
                            matched = False
                            for name_str in names_to_check:
                                if not name_str: continue
                                
                                # Exact substring match
                                if any(tok == name_str or tok in name_str or name_str in tok for tok in search_tokens):
                                    matched_entries += 1
                                    matched = True
                                    break
                                    
                                # Word intersection match
                                comp_words = set(re.findall(r'\b\w+\b', name_str))
                                if [w for w in comp_words.intersection(search_words) if len(w) > 2]:
                                    matched_entries += 1
                                    matched = True
                                    break
                                    
                                # Fuzzy match for minor typos (e.g. 1-2 characters off)
                                fuzzy_matched = False
                                for tok in search_tokens:
                                    if len(tok) > 3 and len(name_str) > 3:
                                        if difflib.SequenceMatcher(None, tok, name_str).ratio() >= 0.75:
                                            matched_entries += 1
                                            matched = True
                                            fuzzy_matched = True
                                            break
                                if fuzzy_matched:
                                    break
                                    
                            if matched: continue
                        
                        required_matches = min(len(search_tokens), 2)
                        if matched_entries >= required_matches:
                            return event.get("id"), comp.get("id"), event
                                
        except Exception as e:
            _LOGGER.error("SportsManager: Failed finding event ID: %s", e)
        return None, None, None

    async def _build_offline_sports_data(self, player_name: str, sport: str, league: str, competitors: list):
        if not competitors: return
        
        session = async_get_clientsession(self.hass)
        artwork_mgr = self.hass.data[DOMAIN][self.entry_id]["artwork_mgr"]
        
        is_individual = sport in ["tennis", "golf", "mma", "boxing", "wrestling"]
        leagues = [league] if league else []
        if not leagues and sport == "tennis": leagues = ["atp", "wta"]
        
        t1 = competitors[0]
        t2 = competitors[1] if len(competitors) > 1 else ""
        
        name1 = t1.get("name", str(t1)).strip() if isinstance(t1, dict) else str(t1).strip()
        name2 = t2.get("name", str(t2)).strip() if isinstance(t2, dict) else str(t2).strip()
        
        if is_individual:
            data1 = await artwork_mgr._search_espn_athlete(session, t1, leagues, sport)
            data2 = await artwork_mgr._search_espn_athlete(session, t2, leagues, sport) if t2 else {}
        else:
            if len(competitors) < 2: return
            data1 = await artwork_mgr._search_espn_team(session, name1, leagues)
            data2 = await artwork_mgr._search_espn_team(session, name2, leagues)
            
        sports_data = {
            "clock": None,
            "period": 0,
            "period_name": "",
            "status_detail": "Classic Match / Replay",
            "is_active": False,
            "is_individual": is_individual,
            "sport": sport,
            "away_team": name1,
            "away_score": "-",
            "away_logo": data1.get("logo", ""),
            "home_team": name2,
            "home_score": "-",
            "home_logo": data2.get("logo", "")
        }
        
        if not is_individual:
            sports_data["away_color"] = f"#{data1.get('color', '111111')}"
            sports_data["home_color"] = f"#{data2.get('color', '111111')}"
            
        # Verify logos
        away_logo, home_logo = await asyncio.gather(
            self._verify_image_url(sports_data["away_logo"]),
            self._verify_image_url(sports_data["home_logo"])
        )
        sports_data["away_logo"] = away_logo
        sports_data["home_logo"] = home_logo
            
        meta_mgr = self.hass.data[DOMAIN][self.entry_id]["metadata_mgr"]
        if meta_mgr.exists(player_name):
            meta_mgr.merge(player_name, {"sports_data": sports_data})
            self._trigger_ui_update(player_name)

    async def _process_and_publish_score(self, player_name: str, event_data: dict, is_individual: bool, comp_id: str = None, sport: str = "", game_state: dict = None):
        try:
            all_comps = event_data.get("competitions", [])
            for group in event_data.get("groupings", []):
                all_comps.extend(group.get("competitions", []))
                
            comp = all_comps[0] if all_comps else {}
            if comp_id:
                for c in all_comps:
                    if c.get("id") == comp_id:
                        comp = c
                        break
                        
            status = comp.get("status", {})
            competitors = comp.get("competitors", [])

            sport_slug = event_data.get("sports", [{}])[0].get("slug", sport)
            if not sport_slug: sport_slug = sport
            
            period = status.get("period", 0)
            period_str = f"Period {period}"
            if sport_slug == "baseball": period_str = f"Inning {period}"
            elif sport_slug in ["football", "basketball"]: period_str = f"Quarter {period}"
            elif sport_slug == "soccer": period_str = f"Half {period}"
            elif sport_slug == "hockey": period_str = f"Period {period}"

            sports_data = {
                "clock": status.get("displayClock", "0:00") if not is_individual else None,
                "period": period,
                "period_name": period_str,
                "status_detail": status.get("type", {}).get("shortDetail", ""),
                "is_active": status.get("type", {}).get("state") == "in",
                "is_individual": is_individual,
                "sport": sport_slug
            }
            
            if sport_slug == "baseball":
                situation = comp.get("situation", {})
                if situation:
                    sports_data["balls"] = situation.get("balls", 0)
                    sports_data["strikes"] = situation.get("strikes", 0)
                    sports_data["outs"] = situation.get("outs", 0)
                    sports_data["on_first"] = situation.get("onFirst", False)
                    sports_data["on_second"] = situation.get("onSecond", False)
                    sports_data["on_third"] = situation.get("onThird", False)
                    
                    batter = situation.get("batter", {})
                    if batter:
                        sports_data["batter_name"] = batter.get("athlete", {}).get("shortName", "")
                        sports_data["batter_summary"] = batter.get("summary", "")
                        
                    pitcher = situation.get("pitcher", {})
                    if pitcher:
                        sports_data["pitcher_name"] = pitcher.get("athlete", {}).get("shortName", "")
                        sports_data["pitcher_summary"] = pitcher.get("summary", "")
                        
                short_det = status.get("type", {}).get("shortDetail", "")
                if "Top" in short_det:
                    sports_data["inning_half"] = "top"
                elif "Bot" in short_det:
                    sports_data["inning_half"] = "bottom"
                else:
                    sports_data["inning_half"] = "mid"

            leaderboard = []
            
            for i, team_data in enumerate(competitors):
                home_away = team_data.get("homeAway")
                if not home_away and not is_individual:
                    home_away = "away" if i == 0 else "home"
                    if i > 1: continue

                prefix = f"{home_away}_" if home_away else ("away_" if i == 0 else "home_")
                
                if is_individual:
                    if i > 4: continue # Only take top 5 for individual leaderboard
                    
                    athlete = team_data.get("athlete", {})
                    athlete_id = team_data.get("id", athlete.get("id", ""))
                    
                    # Construct ESPN headshot URL for golf/tennis
                    headshot = athlete.get("headshot")
                    logo_url = ""
                    if isinstance(headshot, dict):
                        logo_url = headshot.get("href", "")
                    elif isinstance(headshot, str):
                        logo_url = headshot
                    elif athlete_id:
                        sport_path = sport_slug # 'golf' or 'tennis', don't append league
                        logo_url = f"https://a.espncdn.com/i/headshots/{sport_path}/players/full/{athlete_id}.png"

                    # Calculate "thru" hole from linescores
                    thru = "-"
                    linescores_arr = team_data.get("linescores", [])
                    if linescores_arr:
                        current_round = linescores_arr[-1]
                        
                        # If the last round has no linescores (e.g. upcoming round), check the previous round
                        if not ("linescores" in current_round) and len(linescores_arr) > 1:
                            current_round = linescores_arr[-2]
                            
                        if isinstance(current_round, dict) and "linescores" in current_round:
                            num_holes = len(current_round["linescores"])
                            if num_holes == 18:
                                thru = "F" # Finished round
                            else:
                                thru = str(num_holes)
                        elif isinstance(current_round, dict) and "period" in current_round:
                            # Not started yet
                            thru = "-"

                    if thru == "-" and "status" in team_data:
                        period = team_data["status"].get("period", 0)
                        if period > 0: thru = str(period)
                        
                    athlete_data = {
                        "rank": i + 1,
                        "name": athlete.get("shortName", athlete.get("displayName", "")),
                        "full_name": athlete.get("displayName", athlete.get("shortName", "")),
                        "score": team_data.get("score", "0"),
                        "logo": logo_url,
                        "flag": athlete.get("flag", {}).get("href", ""),
                        "seed": team_data.get("seed", ""),
                        "thru": thru
                    }
                    leaderboard.append(athlete_data)
                    
                    # Keep legacy keys for top 2 for backward compatibility
                    if i <= 1:
                        prefix_legacy = "away_" if i == 0 else "home_"
                        sports_data[f"{prefix_legacy}team"] = athlete_data["name"]
                        sports_data[f"{prefix_legacy}full_name"] = athlete_data["full_name"]
                        sports_data[f"{prefix_legacy}score"] = athlete_data["score"]
                        sports_data[f"{prefix_legacy}logo"] = athlete_data["logo"]
                        sports_data[f"{prefix_legacy}flag"] = athlete_data["flag"]
                        sports_data[f"{prefix_legacy}seed"] = athlete_data["seed"]
                        sports_data[f"{prefix_legacy}linescores"] = [str(int(ls.get("value"))) if isinstance(ls.get("value"), float) and ls.get("value").is_integer() else str(ls.get("value", "")) for ls in team_data.get("linescores", [])]
                        
                else:
                    if i > 1: continue
                    team = team_data.get("team", {})
                    sports_data[f"{prefix}team"] = team.get("abbreviation", team.get("name", ""))
                    sports_data[f"{prefix}full_name"] = team.get("displayName", team.get("name", team.get("abbreviation", "")))
                    sports_data[f"{prefix}score"] = team_data.get("score", "0")
                    sports_data[f"{prefix}color"] = f"#{team.get('color', '111111')}"
                    sports_data[f"{prefix}logo"] = team.get("logo", "")
                    sports_data[f"{prefix}linescores"] = [str(int(ls.get("value"))) if isinstance(ls.get("value"), float) and ls.get("value").is_integer() else str(ls.get("value", "")) for ls in team_data.get("linescores", [])]
                    sports_data[f"{prefix}hits"] = team_data.get("hits", "")
                    sports_data[f"{prefix}errors"] = team_data.get("errors", "")

            if is_individual:
                sports_data["leaderboard"] = leaderboard

            # Verify all logo/flag URLs concurrently
            keys_to_check = ["home_logo", "away_logo", "home_flag", "away_flag"]
            urls_to_verify = []
            keys_to_update = []
            for key in keys_to_check:
                if key in sports_data and sports_data[key]:
                    urls_to_verify.append(self._verify_image_url(sports_data[key]))
                    keys_to_update.append(key)
            
            if urls_to_verify:
                verified_urls = await asyncio.gather(*urls_to_verify)
                for key, verified_url in zip(keys_to_update, verified_urls):
                    if not verified_url:
                        sports_data[key] = ""

            if game_state:
                sports_data["vlm_game_state"] = game_state
                
                if sport_slug == "tennis":
                    if not sports_data.get("away_linescores") and not sports_data.get("home_linescores"):
                        tennis_sets = game_state.get("tennis_sets", [])
                        if tennis_sets:
                            away_lines = []
                            home_lines = []
                            away_sets_won = 0
                            home_sets_won = 0
                            for s in tennis_sets:
                                a = s.get("competitor_a")
                                b = s.get("competitor_b")
                                a = int(a) if a is not None else 0
                                b = int(b) if b is not None else 0
                                away_lines.append(str(a))
                                home_lines.append(str(b))
                                if (a >= 6 and a - b >= 2) or (a == 7 and b == 6): away_sets_won += 1
                                elif (b >= 6 and b - a >= 2) or (b == 7 and a == 6): home_sets_won += 1
                            
                            sports_data["away_linescores"] = away_lines
                            sports_data["home_linescores"] = home_lines
                            sports_data["away_score"] = str(away_sets_won)
                            sports_data["home_score"] = str(home_sets_won)
                            
                    if sports_data.get("away_score", "0") == "0" and sports_data.get("home_score", "0") == "0":
                        away_ls = sports_data.get("away_linescores", [])
                        home_ls = sports_data.get("home_linescores", [])
                        if away_ls and len(away_ls) == len(home_ls):
                            away_sets_won = 0
                            home_sets_won = 0
                            for a_str, b_str in zip(away_ls, home_ls):
                                try:
                                    a = int(float(a_str)) if a_str else 0
                                    b = int(float(b_str)) if b_str else 0
                                    if (a >= 6 and a - b >= 2) or (a == 7 and b == 6): away_sets_won += 1
                                    elif (b >= 6 and b - a >= 2) or (b == 7 and a == 6): home_sets_won += 1
                                except ValueError:
                                    pass
                            sports_data["away_score"] = str(away_sets_won)
                            sports_data["home_score"] = str(home_sets_won)

            meta_mgr = self.hass.data[DOMAIN][self.entry_id]["metadata_mgr"]
            if not meta_mgr.exists(player_name): return

            existing_meta = meta_mgr.get(player_name)
            if existing_meta and getattr(existing_meta, "sports_data", None) == sports_data:
                return

            meta_mgr.merge(player_name, {"sports_data": sports_data})
            
            # Fire event on score change
            self.hass.bus.async_fire("ai_media_tracker_score_changed", {
                "player_name": player_name,
                "title": existing_meta.title if existing_meta else "",
                "network_name": existing_meta.network_name if existing_meta else "",
                "sports_data": sports_data
            })
            
            extra = getattr(existing_meta, "extra_attributes", {}) if existing_meta else {}
            artwork_mgr = self.hass.data[DOMAIN][self.entry_id]["artwork_mgr"]
            
            if is_individual:
                bg_url = artwork_mgr._get_generic_sport_bg(sport_slug)
            else:
                import re, os
                t2 = sports_data.get("home_full_name", sports_data.get("home_team", ""))
                t2_clean = re.sub(r"[^a-z0-9]+", "_", t2.lower()).strip("_")
                local_bg = os.path.join(artwork_mgr.cache_dir, "backgrounds", f"{t2_clean}.jpg")
                
                # Check if local bg exists without blocking
                def _check_exists():
                    return os.path.exists(local_bg)
                
                if await self.hass.async_add_executor_job(_check_exists):
                    bg_url = f"/local/ai_media_cache/backgrounds/{t2_clean}.jpg"
                else:
                    bg_url = artwork_mgr._get_generic_sport_bg(sport_slug)
            
            sports_data["background_url"] = bg_url
            
            if existing_meta:
                meta_mgr.merge(player_name, {"entity_picture": bg_url, "extra_attributes": extra})

            self._trigger_ui_update(player_name)

        except Exception as e:
            _LOGGER.error("SportsManager: Failed to parse scoreboard data: %s", e)

    def _trigger_ui_update(self, player_name: str):
        player_obj = self.hass.data[DOMAIN][self.entry_id].get("players", {}).get(player_name)
        if player_obj:
            player_obj.async_write_ha_state()

    async def _run_historical_polling_loop(self, player_name: str, sport: str, league: str, event_id: str, scoreboard_data: dict, game_state: dict, comp_id: str = None):
        session = async_get_clientsession(self.hass)
        summary_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={event_id}"
        
        try:
            _LOGGER.info("SportsManager: Entering Time-Shifted Historical loop for %s", event_id)
            
            summary_data = {}
            if sport not in ["tennis", "wta", "atp", "golf"]:
                async with session.get(summary_url, timeout=10.0) as resp:
                    if resp.status == 200:
                        summary_data = await resp.json()
            
            plays = summary_data.get("plays", [])
            
            if not plays:
                _LOGGER.warning("SportsManager: No historical plays found for %s. Applying VLM game_state directly.", event_id)
                all_comps = scoreboard_data.get("competitions", [])
                for group in scoreboard_data.get("groupings", []):
                    all_comps.extend(group.get("competitions", []))
                    
                comp = all_comps[0] if all_comps else {}
                if comp_id:
                    for c in all_comps:
                        if c.get("id") == comp_id:
                            comp = c
                            break
                
                if game_state.get("period"):
                    comp.setdefault("status", {})["period"] = game_state["period"]
                    
                competitors = comp.get("competitors", [])
                if game_state.get("home_score") or game_state.get("away_score"):
                    for team in competitors:
                        if team.get("homeAway") == "home": team["score"] = str(game_state.get("home_score", team.get("score", "0")))
                        elif team.get("homeAway") == "away": team["score"] = str(game_state.get("away_score", team.get("score", "0")))
                elif sport in ["tennis", "wta", "atp"] and game_state.get("tennis_sets"):
                    tennis_sets = game_state["tennis_sets"]
                    if len(competitors) >= 2:
                        away_linescores = []
                        home_linescores = []
                        for s in tennis_sets:
                            if "competitor_a" in s and "competitor_b" in s:
                                away_linescores.append({"value": str(s["competitor_a"])})
                                home_linescores.append({"value": str(s["competitor_b"])})
                                
                        for team in competitors:
                            if team.get("homeAway") == "away":
                                team["linescores"] = away_linescores
                                if away_linescores:
                                    team["score"] = away_linescores[-1]["value"]
                            elif team.get("homeAway") == "home":
                                team["linescores"] = home_linescores
                                if home_linescores:
                                    team["score"] = home_linescores[-1]["value"]
                                
                if "status" not in comp: comp["status"] = {}
                if "type" not in comp["status"]: comp["status"]["type"] = {}
                comp["status"]["type"]["state"] = "in"
                comp["status"]["type"]["detail"] = "Time-Shifted Broadcast"
                comp["status"]["type"]["shortDetail"] = "Time-Shifted Broadcast"
                
                await self._process_and_publish_score(player_name, scoreboard_data, sport in ["tennis", "golf"], comp_id, sport)
                return
            
            current_play_index = 0
            if plays:
                current_play_index = 0

            # Mock 5-second tick loop
            while True:
                if current_play_index < len(plays):
                    play = plays[current_play_index]
                    
                    # Update scoreboard_data with historical play data
                    comp = scoreboard_data.get("competitions", [{}])[0]
                    if comp_id:
                        for c in scoreboard_data.get("competitions", []):
                            if c.get("id") == comp_id:
                                comp = c
                                break
                    
                    for team in comp.get("competitors", []):
                        if team["homeAway"] == "away":
                            team["score"] = str(play.get("awayScore", team.get("score")))
                        else:
                            team["score"] = str(play.get("homeScore", team.get("score")))
                            
                    comp["status"]["displayClock"] = play.get("clock", {}).get("displayValue", "")
                    comp["status"]["period"] = play.get("period", {}).get("number", 0)
                    
                    # Mark as time-shifted broadcast
                    comp["status"]["type"]["detail"] = "Time-Shifted Broadcast"
                    comp["status"]["type"]["state"] = "in" # force it to look active in UI
                    
                    await self._process_and_publish_score(player_name, scoreboard_data, sport in ["tennis", "golf"], comp_id, sport)
                    
                    current_play_index += 1
                
                await asyncio.sleep(5)
                
        except asyncio.CancelledError:
            self.clear_sports_data(player_name)
            self._trigger_ui_update(player_name)
        except Exception as e:
            _LOGGER.error("SportsManager: Historical loop error: %s", e)