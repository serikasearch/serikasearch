"""Where to watch a film or show — from JustWatch, free and keyless.

JustWatch aggregates streaming availability across services and countries. Its
GraphQL endpoint needs no key; it does need a country, which defaults to the US
here and can be set with ``... in uk``. Results are grouped by how you'd watch
(subscription, rent, buy, free with ads) so a viewer can tell at a glance
whether something is included in a service they already pay for.

TV networks come from TVmaze as a fallback, so a show with no streaming offers
still shows where it airs. Both are cached.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = ["StreamResult", "parse_stream", "set_cache"]

_TIMEOUT = 7.0
_UA = "Mozilla/5.0 (compatible; SerikaSearch/1.1; +https://serikasearch.com)"
_JW = "https://apis.justwatch.com/graphql"

_cache = None


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


# Country words people type -> JustWatch country codes.
_COUNTRIES = {
    "us": "US", "usa": "US", "america": "US", "united states": "US",
    "uk": "GB", "britain": "GB", "united kingdom": "GB", "england": "GB",
    "canada": "CA", "australia": "AU", "germany": "DE", "france": "FR",
    "netherlands": "NL", "spain": "ES", "italy": "IT", "brazil": "BR",
    "mexico": "MX", "japan": "JP", "india": "IN", "ireland": "IE",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "new zealand": "NZ",
}

_MONETIZATION = {
    "FLATRATE": ("Stream", "Included with a subscription"),
    "FREE": ("Free", "Free to watch"),
    "ADS": ("Free with ads", "Free with adverts"),
    "RENT": ("Rent", "Available to rent"),
    "BUY": ("Buy", "Available to buy"),
}
_MONETIZATION_ORDER = ["FLATRATE", "FREE", "ADS", "RENT", "BUY"]

_SEARCH_QUERY = """
query ($filter: TitleFilter!, $country: Country!, $language: Language!, $first: Int!) {
  searchTitles(country: $country, filter: $filter, first: $first, source: "") {
    edges { node {
      ... on MovieOrShow {
        objectType
        content(country: $country, language: $language) {
          title originalReleaseYear shortDescription
        }
        offers(country: $country, platform: WEB) {
          monetizationType
          standardWebURL
          package { clearName }
        }
      }
    } }
  }
}
"""


@dataclass
class Offer:
    provider: str
    kind: str            # "Stream", "Rent", …
    url: str


@dataclass
class StreamResult:
    title: str
    year: str
    description: str
    country: str
    country_name: str
    groups: dict          # kind -> list[Offer]
    network: str = ""
    official_site: str = ""


_STREAM_RE = re.compile(
    r"^\s*(?:where\s+(?:can\s+i\s+)?(?:to\s+)?(?:watch|stream)|"
    r"(?:how\s+to\s+)?(?:watch|stream)|is\s+.+?\s+on\s+(?:netflix|streaming))\s+"
    r"(.+?)(?:\s+in\s+([a-zA-Z ]{2,20}))?\s*\??$",
    re.I,
)


def _post(query: str, variables: dict):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        _JW, data=payload,
        headers={"User-Agent": _UA, "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _tvmaze_network(title: str) -> tuple[str, str]:
    url = ("https://api.tvmaze.com/singlesearch/shows?q="
           + urllib.parse.quote(title))
    cache_key = f"tvmaze:{title.lower()[:60]}"
    data = None
    if _cache is not None:
        try:
            hit = _cache.get(cache_key)
            if hit:
                data = json.loads(hit)
        except Exception:
            data = None
    if data is None:
        request = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                raw = response.read(64 * 1024).decode("utf-8", "replace")
            data = json.loads(raw)
            if _cache is not None:
                try:
                    self_json = json.dumps(data)
                    _cache.setex(cache_key, 604800, self_json)
                except Exception:
                    pass
        except Exception:
            return ("", "")
    network = ((data.get("network") or data.get("webChannel") or {}) or {}).get(
        "name", "") if isinstance(data, dict) else ""
    site = data.get("officialSite", "") if isinstance(data, dict) else ""
    return (network or "", site or "")


def parse_stream(query: str) -> StreamResult | None:
    """``where to watch inception``, ``where can i stream dune in uk``."""
    match = _STREAM_RE.match(query.strip())
    if not match:
        return None
    title = (match.group(1) or "").strip()
    country_word = (match.group(2) or "").strip().lower()
    if not title or len(title) < 2 or len(title) > 80:
        return None

    country = _COUNTRIES.get(country_word, "US")
    country_name = next((k.title() for k, v in _COUNTRIES.items()
                         if v == country and len(k) > 3), country)

    cache_key = f"jw:{country}:{title.lower()[:60]}"
    node = None
    if _cache is not None:
        try:
            hit = _cache.get(cache_key)
            if hit:
                node = json.loads(hit)
        except Exception:
            node = None

    if node is None:
        try:
            response = _post(_SEARCH_QUERY, {
                "first": 1, "country": country, "language": "en",
                "filter": {"searchQuery": title},
            })
        except Exception:
            return None
        edges = (((response.get("data") or {}).get("searchTitles") or {})
                 .get("edges") or [])
        if not edges:
            return None
        node = edges[0]["node"]
        if _cache is not None:
            try:
                _cache.setex(cache_key, 21600, json.dumps(node))
            except Exception:
                pass

    content = node.get("content") or {}
    title_out = content.get("title") or title
    if not title_out:
        return None

    # Group offers by monetization type, de-duplicating providers.
    groups: dict[str, list[Offer]] = {}
    seen: set[tuple[str, str]] = set()
    for offer in node.get("offers") or []:
        kind_code = offer.get("monetizationType", "")
        provider = (offer.get("package") or {}).get("clearName", "")
        if not provider:
            continue
        key = (kind_code, provider)
        if key in seen:
            continue
        seen.add(key)
        label = _MONETIZATION.get(kind_code, (kind_code.title(), ""))[0]
        groups.setdefault(label, []).append(
            Offer(provider=provider, kind=label,
                  url=offer.get("standardWebURL", "")))

    network, site = ("", "")
    if node.get("objectType") == "SHOW" and not groups:
        network, site = _tvmaze_network(title_out)

    if not groups and not network:
        return None

    return StreamResult(
        title=title_out,
        year=str(content.get("originalReleaseYear") or ""),
        description=(content.get("shortDescription") or "")[:220],
        country=country,
        country_name=country_name if country != "US" else "the US",
        groups=groups,
        network=network,
        official_site=site,
    )


def ordered_groups(result: StreamResult) -> list[tuple[str, list]]:
    """Offer groups in a sensible reading order (subscription first)."""
    label_order = [_MONETIZATION[c][0] for c in _MONETIZATION_ORDER]
    ordered = []
    for label in label_order:
        if label in result.groups:
            ordered.append((label, result.groups[label]))
    for label, offers in result.groups.items():
        if label not in dict(ordered):
            ordered.append((label, offers))
    return ordered
