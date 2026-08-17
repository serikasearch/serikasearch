"""Reference sources behind the knowledge panel and the dictionary answer.

The panel is not tied to one encyclopedia. A query resolves against whichever
source the reader picked — or, by default, the best local match followed by
Wikipedia — and the panel renders a source switcher so the same subject can be
read from several angles:

  ``wikipedia``   English Wikipedia, via the MediaWiki API (summary, image, facts)
  ``simple``      Simple English Wikipedia — plainer language, same API
  ``grokipedia``  Grokipedia, read from the page's Open Graph metadata
  ``wiktionary``  Wiktionary, for word-shaped queries
  ``index``       A reference page serikacrawler already has locally

Dictionary lookups come from the free Wiktionary-derived dictionaryapi.dev,
with links out to Oxford, Merriam-Webster and Cambridge for readers who want a
publisher's own entry — those require paid API keys, so SerikaSearch links to
them rather than reproducing their text.

Every fetch is cached and every failure is soft: a source that is slow, down,
or missing an article simply drops out of the panel.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from .db import Index

__all__ = ["KnowledgeCard", "DictionaryEntry", "build_card", "card_relevant",
           "define", "SOURCES", "SOURCE_LABELS"]

# The knowledge-panel fetch runs inline on the results page, so its timeout is
# the worst case a first-time (uncached) query can add to the response. Keep it
# tight — a panel that is slow to fetch is not worth stalling the whole SERP.
_TIMEOUT = 2.5
_UA = ("serikasearch/1.1 (knowledge panel; +https://serikasearch.com/about)")

# Order matters — this is the order the source switcher renders in.
SOURCES = ("wikipedia", "simple", "grokipedia", "wiktionary")
SOURCE_LABELS = {
    "wikipedia": "Wikipedia",
    "simple": "Simple Wikipedia",
    "grokipedia": "Grokipedia",
    "wiktionary": "Wiktionary",
    "index": "Web",
}

# Hosts we trust to produce encyclopaedic summaries from the local index.
REFERENCE_HOSTS = (
    "wikipedia.org", "britannica.com", "stanford.edu", "grokipedia.com",
    "nationalgeographic.com", "sciencedirect.com", "scholarpedia.org",
)

_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are",
    "what", "who", "when", "where", "why", "how", "was", "were", "does", "do",
    "vs", "with", "about", "define", "definition", "meaning",
}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")

# Shared cache, wired to Redis by the web layer (see serika.web.server).
# Bumped whenever the shape of an upstream request changes, so cached
# responses from an older version of the code are never reused.
_CACHE_VERSION = "v2"

_cache = None


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


def _cached_json(url: str, key: str, ttl: int = 21600):
    if _cache is not None:
        try:
            hit = _cache.get(key)
            if hit:
                return json.loads(hit)
        except Exception:
            pass
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read(1024 * 1024).decode("utf-8", "replace")
        data = json.loads(raw)
    except Exception:
        return None
    if _cache is not None:
        try:
            _cache.setex(key, ttl, raw)
        except Exception:
            pass
    return data


# --------------------------------------------------------------------------- #
# data classes
# --------------------------------------------------------------------------- #

@dataclass
class KnowledgeCard:
    title: str
    summary: str
    image: str
    source_name: str
    source_url: str
    facts: list[tuple[str, str]] = field(default_factory=list)
    gallery: list[str] = field(default_factory=list)
    source_key: str = "wikipedia"
    available: list[str] = field(default_factory=list)


@dataclass
class Sense:
    part_of_speech: str
    definitions: list[tuple[str, str]]     # (definition, example)
    synonyms: list[str] = field(default_factory=list)
    antonyms: list[str] = field(default_factory=list)


@dataclass
class DictionaryEntry:
    word: str
    phonetic: str
    audio: str
    origin: str
    senses: list[Sense] = field(default_factory=list)
    source_name: str = "Wiktionary"
    source_url: str = ""


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _is_reference(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in REFERENCE_HOSTS)


def _source_name(host: str) -> str:
    if "wikipedia.org" in host:
        return "Wikipedia"
    if "britannica.com" in host:
        return "Encyclopædia Britannica"
    if "grokipedia.com" in host:
        return "Grokipedia"
    if "stanford.edu" in host:
        return "Stanford Encyclopedia of Philosophy"
    core = host.split(".")[-2] if host.count(".") >= 1 else host
    return core.capitalize()


def _summarise(text: str, max_chars: int = 420) -> str:
    """Trim to whole sentences so the panel never ends mid-thought."""
    text = (text or "").strip()
    if not text:
        return ""
    out = ""
    for sentence in _SENTENCE_END.split(text):
        if not out:
            out = sentence
        elif len(out) + len(sentence) + 1 <= max_chars:
            out = f"{out} {sentence}"
        else:
            break
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + "…"
    return out.strip()


_HEADING_LINE = re.compile(r"^\s*=+\s*.*?\s*=+\s*$")


def _strip_wiki_headings(text: str) -> str:
    """Flatten a Wiktionary extract into readable prose.

    The plain-text extract is a run of ``== English ==`` / ``=== Noun ===``
    headings interleaved with definitions. Dropping the heading lines and the
    pure-metadata ones leaves the part a reader actually wants.
    """
    keep: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or _HEADING_LINE.match(stripped):
            continue
        # Pronunciation and inflection lines are noise in a summary.
        if stripped.startswith(("IPA", "Audio", "Rhymes", "Hyphenation",
                                "Homophone")):
            continue
        keep.append(stripped)
        if sum(len(k) for k in keep) > 600:
            break
    return " ".join(keep).strip()


def _clean_title(title: str) -> str:
    return re.sub(r"\s*[-–—|]\s*(Wikipedia|Britannica|Grokipedia).*$", "",
                  title).strip()


# --------------------------------------------------------------------------- #
# MediaWiki-backed sources (Wikipedia, Simple Wikipedia, Wiktionary)
# --------------------------------------------------------------------------- #

_WIKI_ENDPOINTS = {
    "wikipedia": ("https://en.wikipedia.org/w/api.php", "Wikipedia",
                  "en.wikipedia.org"),
    "simple": ("https://simple.wikipedia.org/w/api.php", "Simple Wikipedia",
               "simple.wikipedia.org"),
    "wiktionary": ("https://en.wiktionary.org/w/api.php", "Wiktionary",
                   "en.wiktionary.org"),
}


def _mediawiki_card(query: str, source: str) -> Optional[KnowledgeCard]:
    endpoint, label, host = _WIKI_ENDPOINTS[source]

    if source == "wiktionary":
        # A dictionary lookup is an exact-word lookup, so ask for the page by
        # title rather than searching. Two other quirks force this: a
        # Wiktionary entry opens straight into "== English ==" with no intro
        # section for exintro to return, and MediaWiki only fills in a full
        # (non-intro) extract for a single page per request — a search
        # generator would come back with three empty extracts and one real one.
        params = {
            "action": "query", "format": "json",
            "titles": query.strip().lower(),
            "prop": "extracts|description|info",
            "explaintext": "1", "exchars": "1500",
            "inprop": "url", "redirects": "1",
        }
    else:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": query, "gsrlimit": "4",
            "prop": "extracts|pageimages|description|info",
            "exintro": "1", "explaintext": "1",
            "piprop": "thumbnail", "pithumbsize": "480",
            "inprop": "url", "redirects": "1",
        }
    url = endpoint + "?" + urllib.parse.urlencode(params)
    data = _cached_json(url, f"ref:{_CACHE_VERSION}:{source}:{query.lower()[:80]}")
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    if not pages:
        return None

    # Two corrections to the API's own ranking. An article whose title is
    # exactly what was asked for beats anything else — otherwise "ubiquitous"
    # can land on "non-ubiquitous". And the top hit for a common word is often
    # a disambiguation page ("Python may refer to:"), which makes a useless
    # panel, so we walk past those to the first article that describes a thing.
    wanted = query.strip().lower()

    def rank(page: dict) -> tuple[int, int]:
        title = (page.get("title") or "").strip().lower()
        return (0 if title == wanted else 1, page.get("index", 99))

    candidates = sorted(pages.values(), key=rank)
    page = None
    title = extract = summary = ""
    for candidate in candidates:
        description = (candidate.get("description") or "").lower()
        if "same term" in description or "disambiguation" in description:
            continue
        candidate_title = candidate.get("title") or ""
        candidate_extract = (candidate.get("extract") or "").strip()
        if source == "wiktionary":
            candidate_extract = _strip_wiki_headings(candidate_extract)
        if not candidate_title or len(candidate_extract) < 40:
            continue
        candidate_summary = _summarise(candidate_extract)
        if len(candidate_summary) < 40:
            continue
        page, title = candidate, candidate_title
        extract, summary = candidate_extract, candidate_summary
        break

    if page is None:
        return None

    canonical = page.get("fullurl") or (
        f"https://{host}/wiki/"
        + urllib.parse.quote(title.replace(" ", "_"))
    )
    description = (page.get("description") or "").strip()

    facts: list[tuple[str, str]] = [("Source", label)]
    if description:
        facts.append(("Type", description[:80].capitalize()))
    facts.append(("Website", host))
    _facts_from_text(extract, facts)

    gallery = (_wikipedia_gallery(title, endpoint)
               if source in ("wikipedia", "simple") else [])

    return KnowledgeCard(
        title=title,
        summary=summary,
        image=(page.get("thumbnail") or {}).get("source") or "",
        source_name=label,
        source_url=canonical,
        facts=facts,
        gallery=gallery[:4],
        source_key=source,
    )


def _facts_from_text(text: str, facts: list[tuple[str, str]]) -> None:
    """Pull a couple of dates out of the intro paragraph, when they're clear."""
    match = re.search(r"\b(1[5-9]\d{2}|20[0-5]\d)\b", text)
    if not match:
        return
    context = text[max(0, match.start() - 48):match.start()].lower()
    year = match.group(1)
    if "born" in context or "birth" in context:
        facts.append(("Born", year))
    elif "founded" in context or "established" in context:
        facts.append(("Founded", year))
    elif "released" in context or "launched" in context or "published" in context:
        facts.append(("Released", year))
    elif "created" in context or "started" in context or "developed" in context:
        facts.append(("Created", year))


_GALLERY_SKIP = ("logo", "icon", "commons-logo", "wiki", "question_book",
                 "disambig", "ambox", "edit-", "symbol_", "flag_of_", "map_of")


def _wikipedia_gallery(title: str, endpoint: str) -> list[str]:
    """Up to four photographs from an article, for the panel's thumb strip."""
    params = {
        "action": "query", "format": "json", "titles": title,
        "generator": "images", "gimlimit": "12",
        "prop": "imageinfo", "iiprop": "url", "iiurlwidth": "320",
    }
    url = endpoint + "?" + urllib.parse.urlencode(params)
    data = _cached_json(url, f"gal:{_CACHE_VERSION}:{endpoint[8:20]}:{title.lower()[:80]}",
                        ttl=86400)
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    gallery: list[str] = []
    for page in pages.values():
        name = (page.get("title") or "").lower()
        if not name.startswith("file:"):
            continue
        if any(skip in name for skip in _GALLERY_SKIP):
            continue
        if not name.endswith((".jpg", ".jpeg", ".png")):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        thumb = info.get("thumburl")
        if thumb:
            gallery.append(thumb)
        if len(gallery) >= 4:
            break
    return gallery


# --------------------------------------------------------------------------- #
# Grokipedia
# --------------------------------------------------------------------------- #

_OG_RE = {
    "title": re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+'
                        r'content=["\']([^"\']+)', re.I),
    "description": re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+'
                              r'content=["\']([^"\']+)', re.I),
    "image": re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+'
                        r'content=["\']([^"\']+)', re.I),
}


def _grokipedia_card(query: str) -> Optional[KnowledgeCard]:
    """Grokipedia has no public API, so we read the article page's Open Graph
    tags — which it renders server-side — and link out for the full text.

    Article slugs follow the Wikipedia convention (``Underscore_Case``), so we
    try the query as typed and then a title-cased variant before giving up.
    """
    base = query.strip().strip("?")
    if not base or len(base) > 80:
        return None

    candidates = []
    for variant in (base, base.title(), base.capitalize()):
        slug = urllib.parse.quote(variant.replace(" ", "_"), safe="_()'-,.")
        if slug not in candidates:
            candidates.append(slug)

    for slug in candidates:
        url = f"https://grokipedia.com/page/{slug}"
        # The cache key keeps the slug's original case: the candidates differ
        # only by capitalisation, so lower-casing here would make a miss on
        # "albert_einstein" mask the hit on "Albert_Einstein".
        html = _fetch_text(url, f"grok:{_CACHE_VERSION}:{slug[:80]}")
        if not html:
            continue
        title = _og(html, "title")
        description = _og(html, "description")
        if not title or len(description) < 40:
            continue
        image = _og(html, "image")
        if image.endswith(("icon-512x512.png", "favicon.ico")):
            image = ""      # the site-wide icon isn't an article image
        return KnowledgeCard(
            title=_clean_title(title),
            summary=_summarise(description),
            image=image,
            source_name="Grokipedia",
            source_url=url,
            facts=[("Source", "Grokipedia"), ("Website", "grokipedia.com")],
            source_key="grokipedia",
        )
    return None


def _og(html: str, key: str) -> str:
    match = _OG_RE[key].search(html)
    if not match:
        return ""
    import html as _htmlmod
    return _htmlmod.unescape(match.group(1)).strip()


def _fetch_text(url: str, cache_key: str, ttl: int = 43200) -> str:
    """Fetch an HTML page's <head>, cached. Only the first 128 KB is read —
    Open Graph tags are always near the top and articles can be megabytes."""
    if _cache is not None:
        try:
            hit = _cache.get(cache_key)
            if hit is not None:
                return hit
        except Exception:
            pass
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "text/html"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            text = response.read(128 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        text = ""       # 404 is a normal answer here: no such article
    except Exception:
        return ""
    if _cache is not None:
        try:
            _cache.setex(cache_key, ttl if text else 3600, text[:16384])
        except Exception:
            pass
    return text


# --------------------------------------------------------------------------- #
# local index source
# --------------------------------------------------------------------------- #

def _index_card(index: Index, query: str,
                results=None) -> Optional[KnowledgeCard]:
    """A reference page the crawler already holds — instant, no network.

    ``results`` lets the caller hand in the page's already-fetched search
    results so we skip a second (expensive) ranking query for the same words.
    """
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return None

    if results is None:
        results = index.search(query, limit=8)
    best, best_score = None, 0.0
    for result in results:
        host = result.host or urlsplit(result.url).netloc
        if not _is_reference(host):
            continue
        title_tokens = set(_tokens(result.title))
        if not title_tokens:
            continue
        overlap = len(query_tokens & title_tokens) / len(query_tokens)
        score = overlap - 0.02 * max(0, len(title_tokens) - len(query_tokens))
        if score > best_score:
            best, best_score = result, score

    if best is None or best_score < 0.6:
        return None

    host = best.host or urlsplit(best.url).netloc
    plain = best.snippet.replace("<mark>", "").replace("</mark>", "")
    summary = _summarise(best.description or plain)
    if len(summary) < 40:
        return None

    return KnowledgeCard(
        title=_clean_title(best.title) or best.title,
        summary=summary,
        image=index.first_image_for_page(best.url) or "",
        source_name=_source_name(host),
        source_url=best.url,
        facts=[("Source", _source_name(host)), ("Website", host)],
        source_key="index",
    )


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def build_card(index: Index, query: str,
               source: str = "", results=None) -> Optional[KnowledgeCard]:
    """Build the knowledge panel for ``query``.

    ``source`` pins a specific encyclopedia (the reader clicked a tab). With no
    source, a confident local reference page wins — it costs no network round
    trip — and Wikipedia is the fallback.
    """
    query = query.strip()
    if not query or query == "*" or len(query) < 3:
        return None

    card: Optional[KnowledgeCard] = None
    if source == "grokipedia":
        card = _grokipedia_card(query)
    elif source in _WIKI_ENDPOINTS:
        card = _mediawiki_card(query, source)
    elif source == "index":
        card = _index_card(index, query, results)
    else:
        card = _index_card(index, query, results) or _mediawiki_card(query, "wikipedia")

    if card is None and source:
        # The reader asked for a source that has no article — fall back so the
        # panel still shows something, with the switcher intact.
        card = _index_card(index, query, results) or _mediawiki_card(query, "wikipedia")
    if card is None:
        return None

    card.available = list(SOURCES)
    return card


def card_relevant(query: str, card) -> bool:
    """Whether a knowledge panel is genuinely about the query.

    The article lookup matches on loose token overlap, which is what you want
    for *finding* an article but not for *deciding to show a panel*: "best
    laptop 2026" shouldn't surface the "Laptop" article, nor "how to boil an
    egg" the "Boiled egg" one. The panel earns its place only when the query
    essentially is the article's subject — the title (minus any parenthetical
    disambiguation) and the query differ by at most one incidental word.
    """
    if card is None:
        return False
    title_main = (getattr(card, "title", "") or "").split("(")[0]
    q = set(_tokens(query))
    t = set(_tokens(title_main))
    if not q or not t:
        return False
    if q == t:
        return True
    if len(q & t) < min(len(q), len(t)):   # smaller side not fully contained
        return False
    return len(q - t) <= 1 and len(t - q) <= 1


# --------------------------------------------------------------------------- #
# dictionary
# --------------------------------------------------------------------------- #

_DEFINE_RE = re.compile(
    r"^\s*(?:define|definition\s+of|meaning\s+of|what\s+does\s+)"
    r"\s*([a-zA-Z][a-zA-Z'\- ]{1,40}?)"
    r"(?:\s+mean)?\s*\??$",
    re.I,
)
_DEFINE_SUFFIX_RE = re.compile(
    r"^\s*([a-zA-Z][a-zA-Z'\-]{1,30})\s+(?:definition|meaning|defined)\s*\??$",
    re.I,
)

# Publisher dictionaries with no free API — linked, never scraped.
DICTIONARY_LINKS = (
    ("Oxford Learner's Dictionaries",
     "https://www.oxfordlearnersdictionaries.com/definition/english/{word}"),
    ("Merriam-Webster", "https://www.merriam-webster.com/dictionary/{word}"),
    ("Cambridge Dictionary",
     "https://dictionary.cambridge.org/dictionary/english/{word}"),
    ("Wiktionary", "https://en.wiktionary.org/wiki/{word}"),
)


def parse_define(query: str) -> str:
    """Return the word to look up, or an empty string when this isn't a
    definition query."""
    match = _DEFINE_RE.match(query) or _DEFINE_SUFFIX_RE.match(query)
    if not match:
        return ""
    word = match.group(1).strip().strip("\"'")
    if not word or len(word) > 40:
        return ""
    return word


def define(word: str) -> Optional[DictionaryEntry]:
    """Look a word up in the free Wiktionary-derived dictionary API."""
    word = word.strip().lower()
    if not word or not re.match(r"^[a-z][a-z'\- ]{0,40}$", word):
        return None

    url = ("https://api.dictionaryapi.dev/api/v2/entries/en/"
           + urllib.parse.quote(word))
    data = _cached_json(url, f"dict:{_CACHE_VERSION}:{word}", ttl=86400 * 7)
    if not isinstance(data, list) or not data:
        return None

    first = data[0]
    phonetic = first.get("phonetic") or ""
    audio = ""
    for item in first.get("phonetics") or []:
        if not phonetic and item.get("text"):
            phonetic = item["text"]
        if not audio and item.get("audio"):
            audio = item["audio"]

    senses: list[Sense] = []
    for entry in data:
        for meaning in entry.get("meanings") or []:
            definitions = []
            for definition in (meaning.get("definitions") or [])[:4]:
                text = (definition.get("definition") or "").strip()
                if text:
                    definitions.append(
                        (text, (definition.get("example") or "").strip())
                    )
            if not definitions:
                continue
            senses.append(Sense(
                part_of_speech=meaning.get("partOfSpeech") or "",
                definitions=definitions,
                synonyms=list(dict.fromkeys(meaning.get("synonyms") or []))[:8],
                antonyms=list(dict.fromkeys(meaning.get("antonyms") or []))[:6],
            ))
        if len(senses) >= 4:
            break

    if not senses:
        return None

    return DictionaryEntry(
        word=first.get("word") or word,
        phonetic=phonetic,
        audio=audio,
        origin=(first.get("origin") or "").strip(),
        senses=senses[:4],
        source_name="Wiktionary",
        source_url=f"https://en.wiktionary.org/wiki/{urllib.parse.quote(word)}",
    )
