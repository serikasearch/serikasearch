"""Anime airing schedule, from AniList's public GraphQL API.

AniList is keyless and generous with a descriptive client, which is why it's
used here rather than TMDB or a scraper: LiveChart and AniChart publish no open
API, and TMDB/JustWatch need keys. The schedule query asks for the next batch
of episodes to air worldwide; results are cached so a burst of visitors hits
AniList once.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

__all__ = ["AnimeSchedule", "parse_anime", "set_cache"]

_ENDPOINT = "https://graphql.anilist.co"
_TIMEOUT = 6.0
_UA = "serikasearch/1.1 (+https://serikasearch.com/about; anime schedule)"

_cache = None


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


_SCHEDULE_QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 30) {
    airingSchedules(notYetAired: true, sort: TIME) {
      episode
      airingAt
      media {
        title { romaji english }
        format
        episodes
        siteUrl
        coverImage { medium }
        averageScore
        genres
      }
    }
  }
}
"""


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


@dataclass
class AnimeSchedule:
    episodes: list[AiringEpisode]


_ANIME_RE = re.compile(
    r"^\s*(?:(?:what\s+)?anime\s+(?:is\s+)?(?:airing|schedule|calendar|"
    r"coming\s+out|releasing)|airing\s+anime|anime\s+this\s+week|"
    r"upcoming\s+anime|anime\s+releases?)\s*(?:today|this\s+week|now)?\s*\??$",
    re.I,
)


def _post(query: str, variables: dict):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        _ENDPOINT, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _UA},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def parse_anime(query: str) -> AnimeSchedule | None:
    if not _ANIME_RE.match(query.strip()):
        return None

    cache_key = "anime:schedule:v1"
    raw = None
    if _cache is not None:
        try:
            raw = _cache.get(cache_key)
        except Exception:
            raw = None

    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    else:
        try:
            response = _post(_SCHEDULE_QUERY, {"page": 1})
        except Exception:
            return None
        data = ((response.get("data") or {}).get("Page") or {}).get(
            "airingSchedules")
        if not data:
            return None
        if _cache is not None:
            try:
                _cache.setex(cache_key, 1800, json.dumps(data))
            except Exception:
                pass

    if not data:
        return None

    now = time.time()
    episodes: list[AiringEpisode] = []
    seen: set[str] = set()
    for item in data:
        media = item.get("media") or {}
        title = (media.get("title") or {}).get("romaji") or ""
        if not title or title in seen:
            continue
        # Skip long-running staples (One Piece, Sazae-san) that swamp a weekly
        # view. Their total is reported as null, so gate on the airing episode
        # number instead — nothing genuinely "new" is on episode 500.
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
            cover=(media.get("coverImage") or {}).get("medium") or "",
            score=media.get("averageScore") or 0,
            genres=(media.get("genres") or [])[:3],
        ))
        if len(episodes) >= 16:
            break

    return AnimeSchedule(episodes=episodes) if episodes else None
