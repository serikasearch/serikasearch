"""!bang shortcuts — jump straight to another site's search.

``!w kyoto`` goes to Wikipedia, ``!gh serika`` to GitHub, ``!yt lofi`` to
YouTube. The convention comes from DuckDuckGo and is worth supporting because
it costs nothing and makes SerikaSearch usable as a browser's only search
engine: the searches it can't answer well still land in one keystroke.

The redirect happens server-side with a 302 and no logging, so the destination
never learns the search came from here beyond the ordinary Referer policy
(which is ``strict-origin-when-cross-origin`` — the query string is not sent).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

__all__ = ["Bang", "BANGS", "find_bang", "resolve_bang", "bang_list"]


@dataclass(frozen=True)
class Bang:
    keys: tuple[str, ...]
    name: str
    url: str          # {q} is replaced with the URL-encoded query
    group: str


BANGS: tuple[Bang, ...] = (
    # reference
    Bang(("w", "wiki", "wikipedia"), "Wikipedia",
         "https://en.wikipedia.org/w/index.php?search={q}", "Reference"),
    Bang(("gk", "grok", "grokipedia"), "Grokipedia",
         "https://grokipedia.com/search?q={q}", "Reference"),
    Bang(("wt", "wiktionary"), "Wiktionary",
         "https://en.wiktionary.org/w/index.php?search={q}", "Reference"),
    Bang(("britannica", "eb"), "Encyclopædia Britannica",
         "https://www.britannica.com/search?query={q}", "Reference"),
    Bang(("oed", "oxford"), "Oxford Learner's Dictionaries",
         "https://www.oxfordlearnersdictionaries.com/search/english/?q={q}",
         "Reference"),
    Bang(("mw", "merriam"), "Merriam-Webster",
         "https://www.merriam-webster.com/dictionary/{q}", "Reference"),
    Bang(("wa", "wolfram"), "Wolfram Alpha",
         "https://www.wolframalpha.com/input?i={q}", "Reference"),
    Bang(("arxiv",), "arXiv",
         "https://arxiv.org/abs/{q}", "Reference"),
    Bang(("scholar", "gs"), "Google Scholar",
         "https://scholar.google.com/scholar?q={q}", "Reference"),

    # code
    Bang(("gh", "github"), "GitHub",
         "https://github.com/search?q={q}", "Code"),
    Bang(("so", "stackoverflow"), "Stack Overflow",
         "https://stackoverflow.com/search?q={q}", "Code"),
    Bang(("npm",), "npm", "https://www.npmjs.com/search?q={q}", "Code"),
    Bang(("pypi", "pip"), "PyPI", "https://pypi.org/search/?q={q}", "Code"),
    Bang(("crates", "rs"), "crates.io",
         "https://crates.io/search?q={q}", "Code"),
    Bang(("mdn",), "MDN Web Docs",
         "https://developer.mozilla.org/en-US/search?q={q}", "Code"),
    Bang(("py", "pydocs"), "Python docs",
         "https://docs.python.org/3/search.html?q={q}", "Code"),
    Bang(("caniuse", "ciu"), "Can I use",
         "https://caniuse.com/?search={q}", "Code"),
    Bang(("hn",), "Hacker News",
         "https://hn.algolia.com/?q={q}", "Code"),

    # media
    Bang(("yt", "youtube"), "YouTube",
         "https://www.youtube.com/results?search_query={q}", "Media"),
    Bang(("imdb",), "IMDb",
         "https://www.imdb.com/find/?q={q}", "Media"),
    Bang(("spotify",), "Spotify",
         "https://open.spotify.com/search/{q}", "Media"),
    Bang(("bandcamp", "bc"), "Bandcamp",
         "https://bandcamp.com/search?q={q}", "Media"),
    Bang(("mal", "myanimelist"), "MyAnimeList",
         "https://myanimelist.net/search/all?q={q}", "Media"),
    Bang(("anilist",), "AniList",
         "https://anilist.co/search/anime?search={q}", "Media"),
    Bang(("vimeo",), "Vimeo",
         "https://vimeo.com/search?q={q}", "Media"),

    # shopping & maps
    Bang(("a", "amazon"), "Amazon",
         "https://www.amazon.com/s?k={q}", "Shopping"),
    Bang(("ebay",), "eBay",
         "https://www.ebay.com/sch/i.html?_nkw={q}", "Shopping"),
    Bang(("osm", "maps"), "OpenStreetMap",
         "https://www.openstreetmap.org/search?query={q}", "Maps"),
    Bang(("gmaps",), "Google Maps",
         "https://www.google.com/maps/search/{q}", "Maps"),

    # social
    Bang(("r", "reddit"), "Reddit",
         "https://www.reddit.com/search/?q={q}", "Social"),
    Bang(("lobsters",), "Lobsters",
         "https://lobste.rs/search?q={q}", "Social"),
    Bang(("mastodon", "fedi"), "Mastodon",
         "https://mastodon.social/search?q={q}", "Social"),

    # other search engines
    Bang(("g", "google"), "Google",
         "https://www.google.com/search?q={q}", "Search engines"),
    Bang(("ddg", "duckduckgo"), "DuckDuckGo",
         "https://duckduckgo.com/?q={q}", "Search engines"),
    Bang(("b", "bing"), "Bing",
         "https://www.bing.com/search?q={q}", "Search engines"),
    Bang(("brave",), "Brave Search",
         "https://search.brave.com/search?q={q}", "Search engines"),
    Bang(("sp", "startpage"), "Startpage",
         "https://www.startpage.com/sp/search?query={q}", "Search engines"),
    Bang(("marginalia",), "Marginalia",
         "https://search.marginalia.nu/search?query={q}", "Search engines"),
    Bang(("wb", "archive"), "Wayback Machine",
         "https://web.archive.org/web/*/{q}", "Search engines"),
)

_LOOKUP: dict[str, Bang] = {}
for _bang in BANGS:
    for _key in _bang.keys:
        _LOOKUP.setdefault(_key, _bang)

# A bang may lead or trail the query: "!w kyoto" and "kyoto !w" both work.
_BANG_RE = re.compile(r"(?:^|\s)!([a-z0-9]{1,16})(?=\s|$)", re.I)


def find_bang(query: str) -> tuple[Bang, str] | None:
    """Return ``(bang, remaining query)`` if the query contains a known bang."""
    if "!" not in query:
        return None
    match = _BANG_RE.search(query)
    if not match:
        return None
    bang = _LOOKUP.get(match.group(1).lower())
    if bang is None:
        return None
    remainder = (query[:match.start()] + " " + query[match.end():]).strip()
    return bang, remainder


def resolve_bang(query: str) -> str:
    """The URL to redirect to, or an empty string when there's no bang.

    A bang with no query redirects to the site's own root rather than running
    an empty search there.
    """
    found = find_bang(query)
    if not found:
        return ""
    bang, remainder = found
    if not remainder:
        parts = urllib.parse.urlsplit(bang.url)
        return f"{parts.scheme}://{parts.netloc}/"
    return bang.url.replace("{q}", urllib.parse.quote(remainder, safe=""))


def bang_list() -> list[tuple[str, list[Bang]]]:
    """Bangs grouped for the /bangs reference page."""
    grouped: dict[str, list[Bang]] = {}
    for bang in BANGS:
        grouped.setdefault(bang.group, []).append(bang)
    return list(grouped.items())
