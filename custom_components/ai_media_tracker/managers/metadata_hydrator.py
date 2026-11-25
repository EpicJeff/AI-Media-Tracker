import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

class MetadataHydrator:
    def __init__(self, hass, tmdb_api_key: str):
        self.hass = hass
        self.api_key = tmdb_api_key

    async def hydrate(self, title: str, media_type: str = "tv") -> dict:
        """Fetches rich metadata from TMDB based on the identified title."""
        if not self.api_key: return {}
        
        import urllib.parse
        import re
        
        # Clean title of trademark/copyright symbols and URL encode
        clean_title = re.sub(r'[\u2122\u00AE\u00A9]', '', title).strip()
        encoded_title = urllib.parse.quote(clean_title)
        
        session = async_get_clientsession(self.hass)
        search_url = f"https://api.themoviedb.org/3/search/multi?api_key={self.api_key}&query={encoded_title}"
        
        try:
            async with session.get(search_url) as resp:
                data = await resp.json()
                results = data.get("results", [])
                if not results: return {}
                
                # Assume the first result is the correct match
                item = results[0]
                item_id = item.get("id")
                m_type = item.get("media_type", "movie")
                
                # Fetch detailed metadata (credits, ratings, runtime)
                details_url = f"https://api.themoviedb.org/3/{m_type}/{item_id}?api_key={self.api_key}&append_to_response=credits,release_dates,content_ratings,videos"
                
                async with session.get(details_url) as det_resp:
                    details = await det_resp.json()
                    
                    # Extract original network as a reliable guide hint
                    networks = details.get("networks", [])
                    network_name = networks[0].get("name") if networks else None

                    # Consolidate into a clean attribute object
                    runtimes = details.get("episode_run_time", [])
                    runtime = details.get("runtime")
                    if not runtime and runtimes:
                        runtime = runtimes[0]
                        
                    content_rating = None
                    if m_type == "movie":
                        releases = details.get("release_dates", {}).get("results", [])
                        us_release = next((r for r in releases if r.get("iso_3166_1") == "US"), None)
                        if us_release and us_release.get("release_dates"):
                            content_rating = us_release["release_dates"][0].get("certification")
                    elif m_type == "tv":
                        ratings = details.get("content_ratings", {}).get("results", [])
                        us_rating = next((r for r in ratings if r.get("iso_3166_1") == "US"), None)
                        if us_rating:
                            content_rating = us_rating.get("rating")

                    import random
                    
                    # Safely extract YouTube Trailer/Teaser
                    videos_obj = details.get("videos") or {}
                    results = videos_obj.get("results", [])
                    
                    # Sort results by size descending (highest resolution first)
                    results.sort(key=lambda x: x.get("size", 0), reverse=True)
                    
                    valid_videos = {"Teaser": [], "Trailer": []}
                    
                    for vid in results:
                        if vid.get("site") == "YouTube" and vid.get("type") in ["Trailer", "Teaser"]:
                            valid_videos[vid.get("type")].append(vid)
                            
                    trailer_youtube_id = None
                    if valid_videos["Teaser"]:
                        # Pick randomly from the top 3 highest resolution teasers
                        top_teasers = valid_videos["Teaser"][:3]
                        trailer_youtube_id = random.choice(top_teasers).get("key")
                        _LOGGER.debug("Selected random high-res Teaser for %s: %s", title, trailer_youtube_id)
                    elif valid_videos["Trailer"]:
                        # Pick randomly from the top 3 highest resolution trailers
                        top_trailers = valid_videos["Trailer"][:3]
                        trailer_youtube_id = random.choice(top_trailers).get("key")
                        _LOGGER.debug("Selected random high-res Trailer for %s: %s", title, trailer_youtube_id)

                    return {
                        "overview": details.get("overview", ""),
                        "rating": details.get("vote_average"),
                        "content_rating": content_rating,
                        "runtime": runtime,
                        "genres": [g["name"] for g in details.get("genres", [])],
                        "release_date": details.get("release_date") or details.get("first_air_date"),
                        "tmdb_url": f"https://www.themoviedb.org/{m_type}/{item_id}",
                        "network_name": network_name,
                        "trailer_youtube_id": trailer_youtube_id
                    }
        except Exception as e:
            _LOGGER.error("MetadataHydrator failed: %s", e)
            return {}