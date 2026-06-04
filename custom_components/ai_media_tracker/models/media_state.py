from dataclasses import dataclass, field
from typing import Optional

@dataclass
class MediaState:
    title: str = "Unknown"
    native_title: Optional[str] = None
    series_title: Optional[str] = None
    state: str = "idle"
    app_name: Optional[str] = None
    channel_platform_name: Optional[str] = None
    content_type: Optional[str] = None
    sport: Optional[str] = None
    league: Optional[str] = None
    teams: list[str] = field(default_factory=list)
    entity_picture: Optional[str] = None
    logo_url: Optional[str] = None
    confidence: float = 0.0
    source: str = "unknown"
    native_resolved: bool = False
    vlm_enriched: bool = False
    artwork_resolved: bool = False
    last_updated: float = 0.0

    # Logic flags
    native_metadata: bool = False
    vlm_analysis: bool = False
    last_kickstart: float = 0.0

    # New deep metadata fields
    scene_analysis: dict = field(default_factory=dict)
    sports_data: dict = field(default_factory=dict)
    show_info: dict = field(default_factory=dict)

    # Headshots for individual sports
    home_headshot: Optional[str] = None
    away_headshot: Optional[str] = None
    venue: Optional[str] = None
    tournament_name: Optional[str] = None

    # Guide data / Network hints
    network_name: Optional[str] = None
    source: str = "unknown"