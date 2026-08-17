"""Anime airing schedule and seasonal guide, from AniList's public GraphQL API.

AniList is keyless and generous with a descriptive client, which is why it's
used here rather than TMDB or a scraper: LiveChart and AniChart publish no open
API, and TMDB/JustWatch need keys.

Three queries are fired:
  * **Airing** — the next batch of episodes to air worldwide, with countdown.
  * **Upcoming** — the next season's new releases (not yet aired, no schedule).
  * **Trending** — what's popular right now.

Results are cached so a burst of visitors hits AniList once.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

__all__ = ["AnimeSchedule", "AiringEpisode", "UpcomingAnime", "parse_anime",
           "set_cache"]

_ENDPOINT = "https://graphql.anilist.co"
_TIMEOUT = 8.0
_UA = "serikasearch/1.1 (+https://serikasearch.com/about; anime schedule)"

_cache = None


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


# --------------------------------------------------------------------------- #
# GraphQL queries
# --------------------------------------------------------------------------- #

_AIRING_QUERY = """
query {
  Page(page: 1, perPage: 50) {
    airingSchedules(notYetAired: true, sort: TIME) {
      episode
      airingAt
      media {
        id
        title { romaji english }
        format
        episodes
        siteUrl
        coverImage { medium large }
        averageScore
        genres
        studios(isMain: true) { nodes { name } }
      }
    }
  }
}
"""

_UPCOMING_QUERY = """
query ($season: MediaSeason, $year: Int) {
  Page(page: 1, perPage: 24) {
    media(season: $season, seasonYear: $year, type: ANIME,
          format_in: [TV, TV_SHORT], sort: POPULARITY_DESC) {
      id
      title { romaji english }
      format
      episodes
      siteUrl
      coverImage { medium large }
      averageScore
      genres
      startDate { year month day }
      studios(isMain: true) { nodes { name } }
    }
  }
}
"""

_TRENDING_QUERY = """
query {
  Page(page: 1, perPage: 12) {
    media(type: ANIME, sort: TRENDING_DESC, format_in: [TV, TV_SHORT]) {
      id
      title { romaji english }
      format
      episodes
      siteUrl
      coverImage { medium large }
      averageScore
      genres
      trending
      studios(isMain: true) { nodes { name } }
    }
  }
}
"""

_SEASONS = ["WINTER", "SPRING", "SUMMER", "FALL"]
_SEASON_NAMES = {
    "WINTER": "Winter", "SPRING": "Spring",
    "SUMMER": "Summer", "FALL": "Fall",
}


def _current_season() -> tuple[str, int]:
    """Return (season, year) for the current anime season."""
    now = time.localtime()
    m, y = now.tm_mon, now.tm_year
    if m in (1, 2, 3): return "WINTER", y
    if m in (4, 5, 6): return "SPRING", y
    if m in (7, 8, 9): return "SUMMER", y
    return "FALL", y


def _next_season() -> tuple[str, int]:
    season, year = _current_season()
    idx = _SEASONS.index(season)
    if idx == 3: return _SEASONS[0], year + 1
    return _SEASONS[idx + 1], year


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class AiringEpisode:
    title: str
    english: str
    episode: int
    airing_at: int
    format: str
    url: str
    cover: str
    score: int
    genres: list[str] = field(default_factory=list)
    studio: str = ""
    total_episodes: int = 0

    @property
    def countdown(self) -> str:
        """Human-readable countdown to airing."""
        delta = self.airing_at - time.time()
        if delta <= 0: return "now"
        days = int(delta // 86400)
        hours = int((delta % 86400) // 3600)
        minutes = int((delta % 3600) // 60)
        if days > 0: return f"{days}d {hours}h"
        if hours > 0: return f"{hours}h {minutes}m"
        return f"{minutes}m"


@dataclass
class UpcomingAnime:
    title: str
    english: str
    format: str
    url: str
    cover: str
    score: int
    genres: list[str] = field(default_factory=list)
    studio: str = ""
    total_episodes: int = 0
    season: str = ""
    year: int = 0


@dataclass
class AnimeSchedule:
    episodes: list[AiringEpisode]
    upcoming: list[UpcomingAnime] = field(default_factory=list)
    trending: list[UpcomingAnime] = field(default_factory=list)
    next_season: str = ""
    next_year: int = 0


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #

_ANIME_RE = re.compile(
    r"^\s*(?:(?:what\s+)?anime\s+(?:is\s+)?(?:airing|schedule|calendar|"
    r"coming\s+out|releasing)|airing\s+anime|anime\s+this\s+week|"
    r"upcoming\s+anime|anime\s+releases?|anime\s+season|"
    r"seasonal\s+anime|new\s+anime|anime\s+trending|"
    r"anime\s+next\s+season)"
    r"\s*(?:today|this\s+week|now)?\s*\??$",
    re.I,
)


def _post(query: str, variables: dict | None = None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        _ENDPOINT, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _UA},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _fetch_airing() -> list:
    """Fetch the airing schedule, cached for 30 minutes."""
    cache_key = "anime:airing:v2"
    if _cache is not None:
        try:
            raw = _cache.get(cache_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    try:
        resp = _post(_AIRING_QUERY)
        data = ((resp.get("data") or {}).get("Page") or {}).get("airingSchedules") or []
    except Exception:
        return []
    if _cache is not None and data:
        try: _cache.setex(cache_key, 1800, json.dumps(data))
        except Exception: pass
    return data


def _fetch_upcoming() -> list:
    """Fetch the next season's upcoming anime, cached for 1 hour."""
    season, year = _next_season()
    cache_key = f"anime:upcoming:{season}:{year}:v2"
    if _cache is not None:
        try:
            raw = _cache.get(cache_key)
            if raw:
                return json.loads(raw), season, year
        except Exception:
            pass
    try:
        resp = _post(_UPCOMING_QUERY, {"season": season, "year": year})
        data = ((resp.get("data") or {}).get("Page") or {}).get("media") or []
    except Exception:
        return [], season, year
    if _cache is not None and data:
        try: _cache.setex(cache_key, 3600, json.dumps(data))
        except Exception: pass
    return data, season, year


def _fetch_trending() -> list:
    """Fetch trending anime, cached for 15 minutes."""
    cache_key = "anime:trending:v2"
    if _cache is not None:
        try:
            raw = _cache.get(cache_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    try:
        resp = _post(_TRENDING_QUERY)
        data = ((resp.get("data") or {}).get("Page") or {}).get("media") or []
    except Exception:
        return []
    if _cache is not None and data:
        try: _cache.setex(cache_key, 900, json.dumps(data))
        except Exception: pass
    return data


def _studio_name(media: dict) -> str:
    studios = media.get("studios") or {}
    nodes = studios.get("nodes") or []
    if nodes and isinstance(nodes, list):
        return nodes[0].get("name") or ""
    return ""


def parse_anime(query: str) -> AnimeSchedule | None:
    """``anime schedule``, ``upcoming anime``, ``anime trending`` → rich data."""
    if not _ANIME_RE.match(query.strip()):
        return None

    now = time.time()
    q_lower = query.strip().lower()

    # Determine which sections to fetch based on the query intent.
    want_airing = "trend" not in q_lower and "next season" not in q_lower
    want_upcoming = "trend" not in q_lower or "next season" in q_lower
    want_trending = "trend" in q_lower or "season" in q_lower or q_lower in (
        "anime", "anime schedule", "airing anime")

    # Default: show all three sections.
    want_airing = True
    want_upcoming = True
    want_trending = True

    episodes: list[AiringEpisode] = []
    if want_airing:
        raw_airing = _fetch_airing()
        seen: set[str] = set()
        for item in raw_airing:
            media = item.get("media") or {}
            title = (media.get("title") or {}).get("romaji") or ""
            if not title or title in seen:
                continue
            if item.get("episode", 0) > 300:
                continue
            if item.get("airingAt", 0) < now:
                continue
            seen.add(title)
            episodes.append(AiringEpisode(
                title=title,
                english=(media.get("title") or {}).get("english") or "",
                episode=item.get("episode", 0),
                airing_at=item.get("airingAt", 0),
                format=media.get("format") or "",
                url=media.get("siteUrl") or "",
                cover=((media.get("coverImage") or {}).get("large")
                       or (media.get("coverImage") or {}).get("medium") or ""),
                score=media.get("averageScore") or 0,
                genres=(media.get("genres") or [])[:3],
                studio=_studio_name(media),
                total_episodes=media.get("episodes") or 0,
            ))
            if len(episodes) >= 20:
                break

    upcoming: list[UpcomingAnime] = []
    next_season, next_year = "", 0
    if want_upcoming:
        raw_upcoming, next_season, next_year = _fetch_upcoming()
        for media in raw_upcoming:
            title = (media.get("title") or {}).get("romaji") or ""
            if not title:
                continue
            upcoming.append(UpcomingAnime(
                title=title,
                english=(media.get("title") or {}).get("english") or "",
                format=media.get("format") or "",
                url=media.get("siteUrl") or "",
                cover=((media.get("coverImage") or {}).get("large")
                       or (media.get("coverImage") or {}).get("medium") or ""),
                score=media.get("averageScore") or 0,
                genres=(media.get("genres") or [])[:3],
                studio=_studio_name(media),
                total_episodes=media.get("episodes") or 0,
                season=next_season,
                year=next_year,
            ))
            if len(upcoming) >= 12:
                break

    trending: list[UpcomingAnime] = []
    if want_trending:
        raw_trending = _fetch_trending()
        for media in raw_trending:
            title = (media.get("title") or {}).get("romaji") or ""
            if not title:
                continue
            trending.append(UpcomingAnime(
                title=title,
                english=(media.get("title") or {}).get("english") or "",
                format=media.get("format") or "",
                url=media.get("siteUrl") or "",
                cover=((media.get("coverImage") or {}).get("large")
                       or (media.get("coverImage") or {}).get("medium") or ""),
                score=media.get("averageScore") or 0,
                genres=(media.get("genres") or [])[:3],
                studio=_studio_name(media),
                total_episodes=media.get("episodes") or 0,
            ))
            if len(trending) >= 8:
                break

    if not episodes and not upcoming and not trending:
        return None

    return AnimeSchedule(
        episodes=episodes, upcoming=upcoming, trending=trending,
        next_season=_SEASON_NAMES.get(next_season, ""),
        next_year=next_year,
    )
