"""Rich metadata: Open Graph, Twitter cards, JSON-LD, oEmbed.

Two jobs live here.

*At crawl time* :func:`extract_meta` reads the structured metadata a page
publishes about itself — ``og:*``, ``twitter:*``, ``schema.org`` JSON-LD,
microdata-ish fallbacks, canonical URL, publish date, author, feeds. That is
what lets a result card show a real preview image, a byline and a date instead
of a grey rectangle.

*At request time* :func:`unfurl` fetches a single URL on demand and returns the
same shape, plus an oEmbed payload when the host offers one. That path takes a
URL from the caller, so it is guarded against server-side request forgery:
only http(s), no credentials in the URL, no private or link-local addresses,
capped size, capped redirects.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

__all__ = ["extract_meta", "unfurl", "UnfurlError", "oembed_endpoint"]

_UA = "serikasearch/1.1 (+https://serikasearch.com/about; link preview)"
_TIMEOUT = 6.0
_MAX_BYTES = 512 * 1024


class UnfurlError(Exception):
    """Raised when a URL is unsafe to fetch or the fetch failed."""


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
_ATTR_RE = re.compile(
    r"""(\w[\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))"""
)
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# The og/twitter keys worth keeping. Anything else is noise for a search index.
_WANTED_META = {
    "og:title", "og:description", "og:image", "og:image:secure_url",
    "og:image:width", "og:image:height", "og:image:alt", "og:url", "og:type",
    "og:site_name", "og:locale", "og:video", "og:video:url", "og:audio",
    "article:published_time", "article:modified_time", "article:author",
    "article:section", "article:tag",
    "twitter:card", "twitter:site", "twitter:creator", "twitter:title",
    "twitter:description", "twitter:image", "twitter:player",
    "product:price:amount", "product:price:currency", "product:availability",
    "book:author", "book:isbn", "video:duration", "music:duration",
    "author", "description", "keywords", "theme-color", "application-name",
    "apple-mobile-web-app-title", "publish-date", "date",
}


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _ATTR_RE.finditer(tag):
        name = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        out[name] = unescape(value).strip()
    return out


def _first(meta: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = meta.get(key)
        if value:
            return value
    return default


def extract_meta(html: str, base_url: str = "") -> dict[str, Any]:
    """Read every scrap of structured metadata out of a page's markup.

    Returns a flat, JSON-serialisable dict. Absent keys are simply missing —
    callers should use ``.get`` rather than expecting a fixed shape.
    """
    if not html:
        return {}

    # Structured metadata always lives in <head>; scanning the whole body of a
    # 4 MB page for meta tags is pure waste.
    head = html[:200_000]
    raw: dict[str, str] = {}

    for tag in _META_TAG_RE.findall(head):
        attributes = _attrs(tag)
        key = (attributes.get("property") or attributes.get("name")
               or attributes.get("itemprop") or "").lower()
        content = attributes.get("content") or ""
        if not key or not content:
            continue
        if key in _WANTED_META and key not in raw:
            raw[key] = content[:1000]

    links: dict[str, str] = {}
    feeds: list[str] = []
    oembed_url = ""
    for tag in _LINK_TAG_RE.findall(head):
        attributes = _attrs(tag)
        rel = (attributes.get("rel") or "").lower()
        href = attributes.get("href") or ""
        if not href:
            continue
        absolute = urllib.parse.urljoin(base_url, href) if base_url else href
        if "canonical" in rel:
            links.setdefault("canonical", absolute)
        elif "alternate" in rel:
            link_type = (attributes.get("type") or "").lower()
            if "oembed" in link_type:
                oembed_url = oembed_url or absolute
            elif "rss" in link_type or "atom" in link_type:
                feeds.append(absolute)
        elif "manifest" in rel:
            links.setdefault("manifest", absolute)

    meta: dict[str, Any] = {}

    title = _first(raw, "og:title", "twitter:title")
    if not title:
        match = _TITLE_RE.search(head)
        if match:
            title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    if title:
        meta["title"] = title[:300]

    description = _first(raw, "og:description", "twitter:description",
                         "description")
    if description:
        meta["description"] = description[:600]

    image = _first(raw, "og:image:secure_url", "og:image", "twitter:image")
    if image:
        meta["image"] = (urllib.parse.urljoin(base_url, image)
                         if base_url else image)
        if raw.get("og:image:alt"):
            meta["image_alt"] = raw["og:image:alt"][:200]
        for source, target in (("og:image:width", "image_width"),
                               ("og:image:height", "image_height")):
            if raw.get(source, "").isdigit():
                meta[target] = int(raw[source])

    for source, target in (
        ("og:site_name", "site_name"), ("og:type", "type"),
        ("og:locale", "locale"), ("twitter:card", "card"),
        ("twitter:site", "twitter_site"), ("twitter:creator", "twitter_creator"),
        ("article:section", "section"), ("theme-color", "theme_color"),
        ("product:price:amount", "price"),
        ("product:price:currency", "price_currency"),
        ("product:availability", "availability"),
        ("video:duration", "duration"),
    ):
        if raw.get(source):
            meta[target] = raw[source][:200]

    published = _first(raw, "article:published_time", "publish-date", "date")
    if published:
        meta["published"] = published[:40]
    if raw.get("article:modified_time"):
        meta["modified"] = raw["article:modified_time"][:40]

    author = _first(raw, "article:author", "author", "book:author",
                    "twitter:creator")
    if author and not author.startswith("http"):
        meta["author"] = author[:160]

    video = _first(raw, "og:video:url", "og:video", "twitter:player")
    if video:
        meta["video"] = video[:600]

    if raw.get("keywords"):
        tags = [t.strip() for t in raw["keywords"].split(",") if t.strip()]
        if tags:
            meta["keywords"] = tags[:12]

    if links.get("canonical"):
        meta["canonical"] = links["canonical"][:600]
    if feeds:
        meta["feeds"] = feeds[:4]
    if oembed_url:
        meta["oembed"] = oembed_url[:600]

    structured = _extract_jsonld(head if "ld+json" in head else html)
    if structured:
        meta["schema"] = structured
        _merge_schema(meta, structured)

    return meta


def _extract_jsonld(html: str) -> dict[str, Any]:
    """Flatten the most useful schema.org node from a page's JSON-LD blocks."""
    for block in _JSONLD_RE.findall(html)[:6]:
        text = block.strip()
        if not text or len(text) > 200_000:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        for node in _walk_nodes(data):
            node_type = node.get("@type")
            if isinstance(node_type, list):
                node_type = node_type[0] if node_type else ""
            if not isinstance(node_type, str):
                continue
            if node_type in ("Article", "NewsArticle", "BlogPosting", "Recipe",
                             "Product", "VideoObject", "Movie", "Book",
                             "Event", "Organization", "Person", "SoftwareApplication",
                             "HowTo", "FAQPage", "WebSite", "Course"):
                return _slim(node, node_type)
    return {}


def _walk_nodes(data: Any, depth: int = 0):
    if depth > 4:
        return
    if isinstance(data, list):
        for item in data[:20]:
            yield from _walk_nodes(item, depth + 1)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                yield from _walk_nodes(data[key], depth + 1)


def _text_of(value: Any) -> str:
    """schema.org values are strings, objects, or lists of either."""
    if isinstance(value, str):
        return value.strip()[:300]
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@id") or "").strip()[:300]
    if isinstance(value, list) and value:
        return _text_of(value[0])
    return ""


def _slim(node: dict, node_type: str) -> dict[str, Any]:
    """Keep the handful of schema fields a result card can actually show."""
    out: dict[str, Any] = {"type": node_type}
    for key, target in (
        ("name", "name"), ("headline", "name"), ("description", "description"),
        ("datePublished", "published"), ("dateModified", "modified"),
        ("author", "author"), ("publisher", "publisher"),
        ("recipeYield", "yield"), ("totalTime", "duration"),
        ("cookTime", "cook_time"), ("prepTime", "prep_time"),
        ("brand", "brand"), ("sku", "sku"), ("isbn", "isbn"),
        ("startDate", "start_date"), ("location", "location"),
        ("applicationCategory", "category"),
    ):
        if key in node and target not in out:
            text = _text_of(node[key])
            if text:
                out[target] = text

    rating = node.get("aggregateRating")
    if isinstance(rating, dict):
        value = rating.get("ratingValue")
        count = rating.get("reviewCount") or rating.get("ratingCount")
        if value is not None:
            try:
                out["rating"] = round(float(value), 2)
            except (TypeError, ValueError):
                pass
        if count is not None:
            try:
                out["rating_count"] = int(float(count))
            except (TypeError, ValueError):
                pass

    offers = node.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        if offers.get("price") is not None:
            out["price"] = str(offers["price"])[:24]
        if offers.get("priceCurrency"):
            out["price_currency"] = str(offers["priceCurrency"])[:8]
        if offers.get("availability"):
            out["availability"] = str(offers["availability"]).rsplit("/", 1)[-1]

    return out


def _merge_schema(meta: dict, schema: dict) -> None:
    """Promote schema values into the flat keys the UI reads."""
    for source, target in (("published", "published"), ("author", "author"),
                           ("rating", "rating"), ("rating_count", "rating_count"),
                           ("price", "price"),
                           ("price_currency", "price_currency"),
                           ("duration", "duration")):
        if source in schema and target not in meta:
            meta[target] = schema[source]
    if "type" in schema and "type" not in meta:
        meta["type"] = schema["type"]


# --------------------------------------------------------------------------- #
# oEmbed
# --------------------------------------------------------------------------- #

# Providers worth knowing without discovery, so an embed works even when the
# page doesn't advertise a rel="alternate" oEmbed link.
_OEMBED_PROVIDERS = (
    (re.compile(r"(?:^|\.)youtube\.com|(?:^|\.)youtu\.be"),
     "https://www.youtube.com/oembed?format=json&url="),
    (re.compile(r"(?:^|\.)vimeo\.com"),
     "https://vimeo.com/api/oembed.json?url="),
    (re.compile(r"(?:^|\.)soundcloud\.com"),
     "https://soundcloud.com/oembed?format=json&url="),
    (re.compile(r"(?:^|\.)flickr\.com|(?:^|\.)flic\.kr"),
     "https://www.flickr.com/services/oembed?format=json&url="),
    (re.compile(r"(?:^|\.)reddit\.com"),
     "https://www.reddit.com/oembed?url="),
    (re.compile(r"(?:^|\.)tiktok\.com"),
     "https://www.tiktok.com/oembed?url="),
    (re.compile(r"(?:^|\.)spotify\.com"),
     "https://open.spotify.com/oembed?url="),
    (re.compile(r"(?:^|\.)dailymotion\.com"),
     "https://www.dailymotion.com/services/oembed?format=json&url="),
    (re.compile(r"(?:^|\.)twitch\.tv"),
     "https://api.twitch.tv/v5/oembed?url="),
    (re.compile(r"(?:^|\.)mastodon\.|(?:^|\.)mstdn\."),
     ""),   # Mastodon instances advertise their own endpoint; discover it
)


def oembed_endpoint(url: str) -> str:
    """The oEmbed endpoint for a known provider, or an empty string."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for pattern, endpoint in _OEMBED_PROVIDERS:
        if pattern.search(host) and endpoint:
            return endpoint + urllib.parse.quote(url, safe="")
    return ""


# --------------------------------------------------------------------------- #
# safe fetching
# --------------------------------------------------------------------------- #

_BLOCKED_PORTS = {22, 23, 25, 445, 3306, 5432, 6379, 9200, 11211, 27017}


def _assert_public(url: str) -> str:
    """Reject anything that could be used to probe the internal network."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnfurlError("only http and https URLs can be previewed")
    if parts.username or parts.password:
        raise UnfurlError("URLs with credentials are not fetched")
    host = parts.hostname or ""
    if not host:
        raise UnfurlError("no host in URL")
    if parts.port and parts.port in _BLOCKED_PORTS:
        raise UnfurlError("that port is not allowed")

    try:
        infos = socket.getaddrinfo(host, parts.port or
                                   (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnfurlError("host could not be resolved") from None

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_reserved
                or address.is_link_local or address.is_multicast
                or address.is_unspecified):
            raise UnfurlError("that address is not publicly routable")
    return url


def _fetch(url: str, accept: str) -> tuple[str, str]:
    """Fetch a URL with SSRF guards, following at most three redirects."""
    seen = set()
    current = url
    for _ in range(4):
        if current in seen:
            raise UnfurlError("redirect loop")
        seen.add(current)
        _assert_public(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": _UA, "Accept": accept,
                     "Accept-Language": "en;q=0.9,*;q=0.5"},
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            response = opener.open(request, timeout=_TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 303, 307, 308):
                location = error.headers.get("Location")
                if not location:
                    raise UnfurlError(f"HTTP {error.code}") from None
                current = urllib.parse.urljoin(current, location)
                continue
            raise UnfurlError(f"HTTP {error.code}") from None
        except Exception as error:
            raise UnfurlError(str(error) or type(error).__name__) from None

        with response:
            raw = response.read(_MAX_BYTES)
            charset = "utf-8"
            content_type = response.headers.get("Content-Type", "")
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            try:
                return raw.decode(charset, errors="replace"), response.geturl()
            except LookupError:
                return raw.decode("utf-8", errors="replace"), response.geturl()
    raise UnfurlError("too many redirects")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects to the caller so each hop is re-validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unfurl(url: str, want_oembed: bool = True) -> dict[str, Any]:
    """Fetch a URL and return its rich preview metadata.

    Raises :class:`UnfurlError` for unsafe URLs and network failures; callers
    should turn that into a 4xx rather than a 500.
    """
    url = (url or "").strip()
    if not url:
        raise UnfurlError("no URL given")
    if "://" in url and not url.startswith(("http://", "https://")):
        raise UnfurlError("only http and https URLs can be previewed")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if len(url) > 2000:
        raise UnfurlError("URL too long")

    html, final_url = _fetch(url, "text/html,application/xhtml+xml")
    meta = extract_meta(html, final_url)
    meta["url"] = final_url
    meta["host"] = urllib.parse.urlsplit(final_url).netloc

    if want_oembed:
        endpoint = meta.get("oembed") or oembed_endpoint(final_url)
        if endpoint:
            try:
                payload, _ = _fetch(endpoint, "application/json")
                data = json.loads(payload)
                if isinstance(data, dict):
                    meta["oembed_data"] = {
                        key: data[key]
                        for key in ("type", "title", "author_name",
                                    "author_url", "provider_name", "html",
                                    "thumbnail_url", "width", "height",
                                    "duration")
                        if key in data
                    }
            except (UnfurlError, ValueError):
                pass    # an embed is a bonus, never a requirement

    return meta
