"""Knowledge-panel ("Google card") builder.

Given a search query, try to assemble a compact infobox:

  1. First, look for a high-confidence reference page already in the index
     (Wikipedia, Britannica, …) — fast and fully local.
  2. If none qualifies, fall back to the live Wikipedia REST/MediaWiki API so
     the panel still works for queries the crawler hasn't covered yet.

Returns ``None`` when nothing is confident enough to show, so the UI simply
omits the panel.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from .db import Index

# Hosts we trust to produce encyclopaedic summaries, best first.
REFERENCE_HOSTS = (
    "wikipedia.org",
    "britannica.com",
    "stanford.edu",   # plato.stanford.edu
    "nationalgeographic.com",
    "sciencedirect.com",
)

_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are",
    "what", "who", "when", "where", "why", "how", "was", "were", "does", "do",
    "vs", "with", "about",
}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class KnowledgeCard:
    title: str
    summary: str
    image: str
    source_name: str
    source_url: str
    facts: list[tuple[str, str]]
    gallery: list[str]  # additional images for a richer panel


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _is_reference(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in REFERENCE_HOSTS)


def _source_name(host: str) -> str:
    if "wikipedia.org" in host:
        return "Wikipedia"
    if "britannica.com" in host:
        return "Encyclopædia Britannica"
    if "stanford.edu" in host:
        return "Stanford Encyclopedia of Philosophy"
    # e.g. "nationalgeographic.com" -> "Nationalgeographic"
    core = host.split(".")[-2] if host.count(".") >= 1 else host
    return core.capitalize()


def _summary(description: str, body_first: str, max_chars: int = 340) -> str:
    text = (description or "").strip() or (body_first or "").strip()
    if not text:
        return ""
    sentences = _SENTENCE_END.split(text)
    out = ""
    for s in sentences:
        if not out:
            out = s
        elif len(out) + len(s) + 1 <= max_chars:
            out = f"{out} {s}"
        else:
            break
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + "…"
    return out.strip()


def build_card(index: Index, query: str) -> Optional[KnowledgeCard]:
    query = query.strip()
    if not query or query == "*" or len(query) < 3:
        return None

    q_tokens = set(_tokens(query))
    if not q_tokens:
        return None

    # Look at the top reference matches for this query.
    candidates = index.search(query, limit=8)
    best = None
    best_overlap = 0.0
    for r in candidates:
        host = r.host or urlsplit(r.url).netloc
        if not _is_reference(host):
            continue
        title_tokens = set(_tokens(r.title))
        if not title_tokens:
            continue
        # How much of the query is reflected in the page title?
        overlap = len(q_tokens & title_tokens) / len(q_tokens)
        # Prefer shorter, more focused titles when overlap ties.
        score = overlap - 0.02 * max(0, len(title_tokens) - len(q_tokens))
        if score > best_overlap:
            best_overlap = score
            best = r

    # A confident in-index reference page wins — it's local and instant.
    if best is not None and best_overlap >= 0.6:
        host = best.host or urlsplit(best.url).netloc
        summary = _summary(best.description, best.snippet.replace("<mark>", "")
                           .replace("</mark>", ""))
        if len(summary) >= 40:
            image = index.first_image_for_page(best.url) or ""
            facts: list[tuple[str, str]] = []
            site = urlsplit(best.url).netloc
            facts.append(("Source", _source_name(host)))
            facts.append(("Website", site))
            title = re.sub(r"\s*[-–—|]\s*(Wikipedia|Britannica).*$",
                           "", best.title).strip()
            return KnowledgeCard(
                title=title or best.title,
                summary=summary,
                image=image,
                source_name=_source_name(host),
                source_url=best.url,
                facts=facts,
                gallery=[],
            )

    # No good local reference — fall back to the live Wikipedia API so the
    # panel still works for queries the crawler hasn't covered yet.
    return _fetch_wikipedia_card(query)


# --------------------------------------------------------------------------- #
# Wikipedia live fallback
# --------------------------------------------------------------------------- #

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_TIMEOUT = 6.0
_WIKI_UA = ("serikasearch/1.0 (knowledge panel; "
            "https://github.com/serikasearch/serikacrawler)")


def _fetch_wikipedia_card(query: str) -> Optional[KnowledgeCard]:
    """Query the English Wikipedia MediaWiki API for the best-matching article
    and build a richer KnowledgeCard from its intro extract, thumbnail,
    additional images, and structured facts.

    Two requests:
      1. generator=search with extracts, pageimages, description, info
      2. images + imageinfo for the top article to build a small gallery.
    Returns None on any failure (network, no match, empty extract).
    """
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "1",
        "prop": "extracts|pageimages|description|info",
        "exintro": "1",
        "explaintext": "1",
        "piprop": "thumbnail",
        "pithumbsize": "400",
        "inprop": "url",
        "redirects": "1",
    }
    url = _WIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _WIKI_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_WIKI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None

    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return None
    page = next(iter(pages.values()))
    title = page.get("title") or ""
    extract = (page.get("extract") or "").strip()
    if not title or len(extract) < 40:
        return None

    thumb = (page.get("thumbnail") or {}).get("source") or ""
    canonical = page.get("fullurl") or (
        "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
    )
    description = (page.get("description") or "").strip()

    summary = _summary(extract, "", max_chars=420)
    if len(summary) < 40:
        return None

    facts: list[tuple[str, str]] = [("Source", "Wikipedia")]
    if description:
        facts.append(("Type", description.capitalize()))
    facts.append(("Website", "en.wikipedia.org"))

    # Try to extract more facts from the first paragraph (dates, places).
    _extract_facts_from_text(extract, facts)

    # Fetch a small gallery of additional images from the article.
    gallery = _fetch_wikipedia_gallery(title)

    return KnowledgeCard(
        title=title,
        summary=summary,
        image=thumb,
        source_name="Wikipedia",
        source_url=canonical,
        facts=facts,
        gallery=gallery[:4],  # keep it small
    )


def _extract_facts_from_text(text: str, facts: list[tuple[str, str]]) -> None:
    """Heuristically extract a few useful facts from the Wikipedia intro."""
    # Look for founding/birth dates.
    year_match = re.search(r"\b(1[5-9]\d{2}|20[0-5]\d)\b", text)
    if year_match:
        year = year_match.group(1)
        # Check context around the year.
        context = text[max(0, year_match.start() - 40):year_match.start()]
        if any(w in context.lower() for w in ("born", "birth", "founded", "established",
                                               "created", "started", "released", "launched")):
            if "born" in context.lower() or "birth" in context.lower():
                facts.append(("Born", year))
            elif "founded" in context.lower() or "established" in context.lower():
                facts.append(("Founded", year))
            elif "released" in context.lower() or "launched" in context.lower():
                facts.append(("Released", year))
            else:
                facts.append(("Started", year))


def _fetch_wikipedia_gallery(title: str) -> list[str]:
    """Fetch up to 4 additional image URLs from a Wikipedia article."""
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "images",
        "imlimit": "8",
    }
    url = _WIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _WIKI_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_WIKI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []

    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return []
    page = next(iter(pages.values()))
    images = page.get("images") or []
    # Filter out icons, SVGs, logos — only keep JPEG/PNG photos.
    gallery: list[str] = []
    for img in images:
        fname = img.get("title", "")  # e.g. "File:Example.jpg"
        if not fname.startswith("File:"):
            continue
        lower = fname.lower()
        if any(x in lower for x in (".svg", ".pdf", ".ogg", ".ogv", ".webm",
                                     "logo", "icon", "commons-logo", "wiki",
                                     "question_book", "disambig", "ambox")):
            continue
        if not any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif")):
            continue
        thumb_url = _fetch_wiki_image_thumb(fname)
        if thumb_url:
            gallery.append(thumb_url)
        if len(gallery) >= 4:
            break
    return gallery


def _fetch_wiki_image_thumb(filename: str) -> str:
    """Get the thumbnail URL for a File: page from the MediaWiki API."""
    params = {
        "action": "query",
        "format": "json",
        "titles": filename,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "300",
    }
    url = _WIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _WIKI_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_WIKI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return ""
    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return ""
    page = next(iter(pages.values()))
    ii = (page.get("imageinfo") or [{}])[0]
    return ii.get("thumburl") or ""
