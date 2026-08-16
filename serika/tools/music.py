"""Artist and band data from MusicBrainz — keyless, open, free.

MusicBrainz is a community music encyclopaedia with a public API that asks only
for a descriptive User-Agent and one request per second. That's a good fit for a
knowledge panel: given an artist name it returns the type (person or group),
country, active years, genres, and a discography, plus the artist's own official
and streaming links — which is the honest way to point at tour dates, since no
concert API is free and keyless.

Cover art comes from the Cover Art Archive, also free. Everything is cached so a
popular artist hits MusicBrainz once, not once per visitor.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = ["ArtistCard", "lookup_artist", "set_cache"]

_TIMEOUT = 7.0
# MusicBrainz explicitly requires a real, contactable User-Agent.
_UA = "SerikaSearch/1.1 (https://serikasearch.com/about)"
_MB = "https://musicbrainz.org/ws/2"

# MusicBrainz enforces roughly one request per second per IP. A single panel
# fires up to three calls, so serialise them behind a lock with a minimum gap —
# uncached artist panels take a couple of seconds, cached ones are instant.
_throttle_lock = threading.Lock()
_last_request = [0.0]
_MIN_GAP = 1.1

_cache = None


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


def _get(url: str, cache_key: str, ttl: int = 604800, throttle: bool = True):
    if _cache is not None:
        try:
            hit = _cache.get(cache_key)
            if hit:
                return json.loads(hit)
        except Exception:
            pass

    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})

    # MusicBrainz occasionally answers 503 even inside the rate limit; one
    # retry after a beat clears almost all of those.
    for attempt in range(2):
        if throttle:
            with _throttle_lock:
                wait = _MIN_GAP - (time.monotonic() - _last_request[0])
                if wait > 0:
                    time.sleep(wait)
                _last_request[0] = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                raw = response.read(512 * 1024).decode("utf-8", "replace")
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 503 and attempt == 0:
                time.sleep(1.0)
                continue
            return None
        except Exception:
            return None
        if _cache is not None:
            try:
                _cache.setex(cache_key, ttl, raw)
            except Exception:
                pass
        return data
    return None


@dataclass
class ArtistCard:
    name: str
    sort_name: str
    kind: str                 # "Person", "Group", …
    disambiguation: str
    country: str
    gender: str
    begin: str
    end: str
    ended: bool
    area: str
    genres: list[str] = field(default_factory=list)
    albums: list[dict] = field(default_factory=list)   # {title, year, type}
    links: list[tuple[str, str]] = field(default_factory=list)  # (label, url)
    mbid: str = ""

    @property
    def life_summary(self) -> str:
        """A one-line 'Japanese singer, active since 2002' style descriptor."""
        bits = []
        if self.country:
            bits.append(self.country)
        role = {"Person": "artist", "Group": "band",
                "Character": "virtual artist", "Orchestra": "orchestra",
                "Choir": "choir"}.get(self.kind, "artist")
        bits.append(role)
        return " ".join(bits)


# Which URL relation types to surface, and how to label them.
_LINK_LABELS = {
    "official homepage": "Official site",
    "official site": "Official site",
    "bandcamp": "Bandcamp",
    "soundcloud": "SoundCloud",
    "youtube": "YouTube",
    "spotify": "Spotify",
    "apple music": "Apple Music",
    "streaming": "Streaming",
    "free streaming": "Streaming",
    "purchase for download": "Buy music",
    "social network": "Social",
    "setlistfm": "Setlists (setlist.fm)",
    "songkick": "Tour dates (Songkick)",
    "bandsintown": "Tour dates (Bandsintown)",
    "wikipedia": "Wikipedia",
}

_COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "JP": "Japan", "KR": "South Korea",
    "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France",
    "NL": "Netherlands", "SE": "Sweden", "NO": "Norway", "IT": "Italy",
    "ES": "Spain", "BR": "Brazil", "MX": "Mexico", "IE": "Ireland",
    "NZ": "New Zealand", "BE": "Belgium", "DK": "Denmark", "FI": "Finland",
    "CN": "China", "IN": "India", "RU": "Russia", "PL": "Poland", "AT": "Austria",
    "CH": "Switzerland", "PT": "Portugal", "IS": "Iceland",
}


def _classify_link(url: str, rel_type: str) -> tuple[str, str] | None:
    rel = (rel_type or "").lower()
    host = urllib.parse.urlsplit(url).netloc.lower().replace("www.", "")
    if "setlist.fm" in host:
        return ("Setlists (setlist.fm)", url)
    if "songkick" in host:
        return ("Tour dates (Songkick)", url)
    if "bandsintown" in host:
        return ("Tour dates (Bandsintown)", url)
    if "ticketmaster" in host or "livenation" in host:
        return ("Tickets", url)
    if "bandcamp.com" in host:
        return ("Bandcamp", url)
    if "soundcloud.com" in host:
        return ("SoundCloud", url)
    if "youtube.com" in host or "youtu.be" in host:
        return ("YouTube", url)
    if "spotify.com" in host:
        return ("Spotify", url)
    if "music.apple.com" in host:
        return ("Apple Music", url)
    if "wikipedia.org" in host:
        return None      # Wikipedia is already the panel's main source
    if rel in ("official homepage", "official site"):
        return ("Official site", url)
    label = _LINK_LABELS.get(rel)
    return (label, url) if label else None




def lookup_artist(name: str) -> ArtistCard | None:
    """Resolve an artist by name and build a card, or return None."""
    name = (name or "").strip()
    if not name or len(name) < 2 or len(name) > 80:
        return None

    key = re.sub(r"\s+", " ", name.lower())
    search = _get(
        f"{_MB}/artist/?query={urllib.parse.quote(name)}&fmt=json&limit=5",
        f"mb:search:{key[:60]}",
    )
    artists = (search or {}).get("artists") or []
    if not artists:
        return None

    # Take the best-scored exact-ish match. MusicBrainz sorts by score already,
    # but prefer an exact name hit so "Queen" isn't a tribute band.
    best = artists[0]
    for candidate in artists:
        if candidate.get("name", "").lower() == key and \
                int(candidate.get("score", 0)) >= 80:
            best = candidate
            break
    if int(best.get("score", 0)) < 70:
        return None

    mbid = best.get("id")
    detail = _get(
        f"{_MB}/artist/{mbid}?inc=release-groups+tags+genres+url-rels&fmt=json",
        f"mb:artist:{mbid}",
    )
    if not detail:
        detail = best

    life = detail.get("life-span") or {}
    area = (detail.get("area") or {}).get("name") or ""
    country_code = detail.get("country") or ""
    country = _COUNTRY_NAMES.get(country_code, country_code)

    # Genres: prefer the curated genre list, fall back to tags by vote count.
    genres = [g["name"] for g in
              sorted(detail.get("genres") or [],
                     key=lambda g: -g.get("count", 0))][:6]
    if not genres:
        genres = [t["name"] for t in
                  sorted(detail.get("tags") or [],
                         key=lambda t: -t.get("count", 0))
                  if t.get("count", 0) > 0][:6]

    # Discography: studio albums, newest first, de-duplicated by title.
    albums = []
    seen_titles: set[str] = set()
    release_groups = detail.get("release-groups") or []
    for rg in sorted(release_groups,
                     key=lambda r: r.get("first-release-date") or "",
                     reverse=True):
        if rg.get("primary-type") != "Album":
            continue
        if rg.get("secondary-types"):
            continue      # skip live/compilation/remix albums
        title = rg.get("title", "").strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        year = (rg.get("first-release-date") or "")[:4]
        albums.append({"title": title, "year": year, "id": rg.get("id", "")})
        if len(albums) >= 8:
            break

    links: list[tuple[str, str]] = []
    seen_labels: set[str] = set()
    for relation in detail.get("relations") or []:
        url = (relation.get("url") or {}).get("resource", "")
        if not url:
            continue
        classified = _classify_link(url, relation.get("type", ""))
        if classified and classified[0] not in seen_labels:
            seen_labels.add(classified[0])
            links.append(classified)
    # A stable order: official + tours first, then streaming, then social.
    priority = {"Official site": 0, "Tour dates (Songkick)": 1,
                "Tour dates (Bandsintown)": 1, "Tickets": 1,
                "Setlists (setlist.fm)": 2, "Spotify": 3, "Apple Music": 3,
                "YouTube": 3, "Bandcamp": 3, "SoundCloud": 3, "Social": 4}
    links.sort(key=lambda x: priority.get(x[0], 9))

    return ArtistCard(
        name=detail.get("name") or best.get("name") or name,
        sort_name=detail.get("sort-name") or "",
        kind=detail.get("type") or "",
        disambiguation=detail.get("disambiguation") or "",
        country=country,
        gender=(detail.get("gender") or "").capitalize(),
        begin=life.get("begin") or "",
        end=life.get("end") or "",
        ended=bool(life.get("ended")),
        area=area,
        genres=genres,
        albums=albums,
        links=links[:6],
        mbid=mbid,
    )
