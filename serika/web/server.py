"""SerikaSearch web server — the search front-end.

A stdlib HTTP server that renders the AMOLED, purple-accented UI over the FTS5
index that serikacrawler builds. All markup lives in the ``html/`` tree and is
rendered through the tiny template engine in :mod:`serika.web.templates`; static
assets (CSS/JS) are served from ``static/``. Serves web results, image results,
a knowledge panel, and locally cached favicons — so result pages make no
third-party requests.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote_plus, urlencode

from ..core.db import Index
from ..core.knowledge import build_card
from ..core.query import parse as parse_query
from .templates import HTML_DIR, STATIC_DIR, render

PAGE_SIZE = 10
IMAGE_PAGE_SIZE = 60

# Wordmark: text only, no icon.
WORDMARK = 'serika<em>search</em>'

# Tab favicon — a plain purple "s" on black, matching the wordmark.
FAVICON_SVG = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='14' fill='%23000000'/%3E"
    "%3Ctext x='32' y='46' font-family='Helvetica,Arial,sans-serif' "
    "font-size='42' font-weight='bold' text-anchor='middle' "
    "fill='%23a274ff'%3Es%3C/text%3E%3C/svg%3E"
)


# --------------------------------------------------------------------------- #
# component renderers — each returns a safe HTML string for a |safe slot
# --------------------------------------------------------------------------- #

def _searchbox(value: str = "", tab: str = "web",
               autofocus: bool = False) -> str:
    hidden = ""
    if tab and tab != "web":
        hidden += f'<input type="hidden" name="tab" value="{html.escape(tab, quote=True)}"/>'
    return render("components/searchbox.html", {
        "query": value,
        "autofocus": "autofocus" if autofocus else "",
        "hidden": hidden,
    })


def _tabs(q: str, tab: str, web_n: int, img_n: int, vid_n: int = 0) -> str:
    out = []
    for key, label, count in (("web", "Web", web_n),
                              ("images", "Images", img_n),
                              ("videos", "Videos", vid_n)):
        active = "active" if tab == key else ""
        n = f'<span class="n">{count:,}</span>' if count else ""
        out.append(render("components/tab.html", {
            "active": active,
            "href": _qs(q, key),
            "label": label,
            "count": n,
        }))
    return "".join(out)


def _operator_badges(parsed) -> str:
    """Small removable badges for active search operators (site:, -term, …)."""
    if not parsed.has_operators:
        return ""
    badges = []
    for s in parsed.sites:
        badges.append(
            f'<a class="op-badge" href="/search" title="Remove site filter">'
            f'site:{html.escape(s)} &times;</a>'
        )
    for w in parsed.excludes:
        badges.append(f'<span class="op-badge op-exclude">-{html.escape(w)}</span>')
    for w in parsed.intitle:
        badges.append(f'<span class="op-badge">intitle:{html.escape(w)}</span>')
    for w in parsed.inurl:
        badges.append(f'<span class="op-badge">inurl:{html.escape(w)}</span>')
    return f'<div class="op-badges">{"".join(badges)}</div>'


def _header(q: str, tab: str, parsed, web_n: int, img_n: int,
            vid_n: int = 0) -> str:
    return render("components/header.html", {
        "searchbox": _searchbox(q, tab),
        "op_badges": _operator_badges(parsed),
        "tabs": _tabs(q, tab, web_n, img_n, vid_n),
    })


def _pager(q: str, tab: str, page: int, last: int) -> str:
    if last <= 1:
        return ""
    return render("components/pager.html", {
        "prev_disabled": "disabled" if page <= 1 else "",
        "prev_href": _qs(q, tab, max(1, page - 1)),
        "page": f"{page:,}",
        "last": f"{last:,}",
        "next_disabled": "disabled" if page >= last else "",
        "next_href": _qs(q, tab, min(last, page + 1)),
    })


def _result_card(r, i: int) -> str:
    host = r.host or urlsplit(r.url).netloc
    path = urlsplit(r.url).path or "/"
    if len(path) > 70:
        path = path[:70] + "…"
    return render("components/result.html", {
        "i": i,
        "host_q": quote_plus(host),
        "host": host,
        "path": path,
        "url": r.url,
        "title": (r.title or r.url)[:140],
        "snippet": _safe_snippet(r.snippet),
    })


def _image_card(r, i: int) -> str:
    caption = r.alt or r.page_title or r.host
    # Justified (Google Images-style) rows need each image's aspect ratio to
    # size its box. Fall back to a neutral 3:2 when the source never told us.
    ratio = round(r.width / r.height, 4) if (r.width and r.height) else 1.5
    return render("components/image-card.html", {
        "i": i,
        "ratio": ratio,
        "page_url": r.page_url or r.src,
        "src": r.src,
        "caption": caption[:120],
        "host": r.host,
        "host_q": quote_plus(r.host),
    })


def _video_card(v, i: int) -> str:
    """Render a video result card with thumbnail, title, host, and embed data."""
    host = v.get("host", "")
    platform = v.get("platform", "")
    embed_id = v.get("embed_id", "")
    thumb = v.get("thumbnail", "")
    title = html.escape((v.get("page_title", "") or host)[:140])
    page_url = v.get("page_url", "")

    # Build embed URL for popout.
    if platform == "youtube":
        embed_url = (
            f"https://www.youtube-nocookie.com/embed/{embed_id}"
            f"?rel=0&modestbranding=1&playsinline=1"
        )
        if not thumb:
            thumb = f"https://i.ytimg.com/vi/{embed_id}/hqdefault.jpg"
    elif platform == "vimeo":
        embed_url = f"https://player.vimeo.com/video/{embed_id}?dnt=1"
    else:
        embed_url = ""

    thumb_block = ""
    if thumb:
        thumb_block = (
            f'<img src="{html.escape(thumb, quote=True)}" alt="" '
            f'loading="lazy" decoding="async" '
            f'onerror="this.style.display=\'none\'"/>'
        )

    return render("components/video-card.html", {
        "i": i,
        "host_q": quote_plus(host),
        "host": host,
        "url": page_url or embed_url,
        "title": title,
        "embed_url": embed_url,
        "embed_id": embed_id,
        "platform": platform,
        "thumb_block": thumb_block,
    })


def _knowledge_panel(card) -> str:
    image_block = ""
    if card.image:
        image_block = (
            f'<div class="kpanel-img"><img src="{html.escape(card.image, quote=True)}" '
            f'alt="" loading="lazy" decoding="async" '
            f'onerror="this.parentElement.style.display=\'none\'"/></div>'
        )
    facts = "".join(
        f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>"
        for k, v in card.facts
    )
    # Build gallery block from additional images.
    gallery_block = ""
    if hasattr(card, "gallery") and card.gallery:
        thumbs = "".join(
            f'<img src="{html.escape(g, quote=True)}" alt="" '
            f'loading="lazy" decoding="async" '
            f'onerror="this.style.display=\'none\'"/>'
            for g in card.gallery
        )
        gallery_block = f'<div class="kpanel-gallery">{thumbs}</div>'
    return render("components/knowledge-panel.html", {
        "image_block": image_block,
        "source_name": card.source_name,
        "title": card.title,
        "summary": card.summary,
        "facts": facts,
        "gallery_block": gallery_block,
        "source_url": card.source_url,
    })


def _qs(q: str, tab: str = "web", page: int = 1) -> str:
    params = {"q": q}
    if tab and tab != "web":
        params["tab"] = tab
    if page > 1:
        params["page"] = str(page)
    return "/search?" + urlencode(params)


def _filter_by_size(results, size: str):
    """Filter image results by size/aspect category in-memory."""
    if not size:
        return results
    out = []
    for r in results:
        w, h = r.width, r.height
        if not w or not h:
            continue
        ratio = w / h
        area = w * h
        if size == "large" and area >= 200000:
            out.append(r)
        elif size == "medium" and 40000 <= area < 200000:
            out.append(r)
        elif size == "wide" and ratio >= 1.4:
            out.append(r)
        elif size == "tall" and ratio <= 0.8:
            out.append(r)
    return out


def _image_filter_buttons(base_url: str, active: str) -> str:
    """Build the size filter button row for image search."""
    opts = [("", "All"), ("large", "Large"), ("medium", "Medium"),
            ("wide", "Wide"), ("tall", "Tall")]
    parts = ['<span class="filter-label">Size:</span>']
    for key, label in opts:
        cls = "filter-opt active" if key == active else "filter-opt"
        href = base_url if not key else f"{base_url}&size={key}"
        parts.append(f'<a class="{cls}" href="{href}">{label}</a>')
    return "".join(parts)


def _safe_snippet(snippet: str) -> str:
    """FTS returns snippets with literal <mark> tags around matches; escape
    everything else so we never emit raw crawled HTML."""
    open_ph, close_ph = "\x00O\x00", "\x00C\x00"
    s = snippet.replace("<mark>", open_ph).replace("</mark>", close_ph)
    s = html.escape(s)
    return s.replace(open_ph, "<mark>").replace(close_ph, "</mark>")


def _shell(title: str, body_html: str) -> str:
    return render("layouts/base.html", {
        "title": title,
        "favicon": FAVICON_SVG,
        "body": body_html,
    })


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    index: Index  # injected by serve()
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console clean
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)

        if route == "/":
            self._send_html(self._home())
        elif route == "/search":
            q = (params.get("q", [""])[0]).strip()
            tab = (params.get("tab", ["web"])[0]).strip()
            try:
                page = max(1, int(params.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            if tab == "images":
                size = (params.get("size", [""])[0]).strip()
                self._send_html(self._image_results(q, page, size))
            elif tab == "videos":
                self._send_html(self._video_results(q, page))
            else:
                self._send_html(self._web_results(q, page))
        elif route == "/icon":
            self._send_icon((params.get("h", [""])[0]).strip())
        elif route == "/api/similar":
            self._send_similar(params)
        elif route == "/api/search":
            self._api_search(params)
        elif route == "/api/images":
            self._api_images(params)
        elif route == "/api/videos":
            self._api_videos(params)
        elif route == "/api/stats":
            self._api_stats()
        elif route == "/robots.txt":
            self._send_robots()
        elif route == "/llms.txt":
            self._send_llms()
        elif route == "/healthz":
            self._send_bytes(b"ok", "text/plain")
        elif route.startswith("/static/"):
            self._send_static(route)
        else:
            self._send_html(self._not_found(), status=404)

    def _send_similar(self, params):
        """JSON list of images related to a given one, for the lightbox."""
        src = (params.get("src", [""])[0]).strip()
        page_url = (params.get("page", [""])[0]).strip()
        host = (params.get("host", [""])[0]).strip()
        related = self.index.similar_images(src, page_url, host, limit=14)
        items = [{
            "src": r.src,
            "page": r.page_url or r.src,
            "title": (r.alt or r.page_title or r.host)[:120],
            "host": r.host,
            "ratio": round(r.width / r.height, 4) if (r.width and r.height) else 1.5,
        } for r in related]
        self._send_bytes(
            json.dumps(items).encode("utf-8"),
            "application/json",
            cache="no-cache",
        )

    # ----- JSON API --------------------------------------------------------

    def _api_search(self, params) -> None:
        """GET /api/search?q=...&limit=10&page=1 — JSON web search."""
        q = (params.get("q", [""])[0]).strip()
        try:
            limit = min(50, max(1, int(params.get("limit", ["10"])[0])))
        except ValueError:
            limit = 10
        try:
            page = max(1, int(params.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        if not q:
            self._send_bytes(json.dumps({"error": "missing q"}).encode(),
                             "application/json", status=400)
            return
        parsed = parse_query(q)
        offset = (page - 1) * limit
        total = self.index.count_matches(parsed)
        results = self.index.search(parsed, limit, offset)
        data = {
            "query": q,
            "total": total,
            "page": page,
            "limit": limit,
            "results": [{
                "title": r.title,
                "url": r.url,
                "host": r.host,
                "description": r.description,
                "snippet": r.snippet.replace("<mark>", "").replace("</mark>", ""),
                "score": round(r.score, 4),
            } for r in results],
        }
        self._send_bytes(json.dumps(data).encode(), "application/json",
                         cache="no-cache")

    def _api_images(self, params) -> None:
        """GET /api/images?q=...&limit=20&page=1 — JSON image search."""
        q = (params.get("q", [""])[0]).strip()
        try:
            limit = min(100, max(1, int(params.get("limit", ["20"])[0])))
        except ValueError:
            limit = 20
        try:
            page = max(1, int(params.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        if not q:
            self._send_bytes(json.dumps({"error": "missing q"}).encode(),
                             "application/json", status=400)
            return
        parsed = parse_query(q)
        offset = (page - 1) * limit
        total = self.index.count_image_matches(parsed)
        results = self.index.search_images(parsed, limit, offset)
        data = {
            "query": q,
            "total": total,
            "page": page,
            "limit": limit,
            "results": [{
                "src": r.src,
                "page_url": r.page_url,
                "page_title": r.page_title,
                "host": r.host,
                "alt": r.alt,
                "width": r.width,
                "height": r.height,
                "score": round(r.score, 4),
            } for r in results],
        }
        self._send_bytes(json.dumps(data).encode(), "application/json",
                         cache="no-cache")

    def _api_videos(self, params) -> None:
        """GET /api/videos?q=...&limit=20&page=1 — JSON video search."""
        q = (params.get("q", [""])[0]).strip()
        try:
            limit = min(50, max(1, int(params.get("limit", ["20"])[0])))
        except ValueError:
            limit = 20
        try:
            page = max(1, int(params.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        if not q:
            self._send_bytes(json.dumps({"error": "missing q"}).encode(),
                             "application/json", status=400)
            return
        parsed = parse_query(q)
        offset = (page - 1) * limit
        total = self.index.count_video_matches(parsed)
        results = self.index.search_videos(parsed, limit, offset)
        data = {
            "query": q,
            "total": total,
            "page": page,
            "limit": limit,
            "results": results,
        }
        self._send_bytes(json.dumps(data).encode(), "application/json",
                         cache="no-cache")

    def _api_stats(self) -> None:
        """GET /api/stats — index statistics as JSON."""
        # Flush stats cache so we always return fresh counts.
        if self.index._redis:
            try:
                self.index._redis.delete("stats:doc_count")
            except Exception:
                pass
        data = {
            "pages": self.index.document_count(),
            "images": self.index.image_count(),
            "videos": self.index.video_count(),
            "favicons": self.index.favicon_count(),
            "sites": len(self.index.hosts()),
            "frontier_pending": self.index.frontier_pending(),
            "categories": [
                {"name": c, "count": n} for c, n in self.index.categories()
            ],
        }
        self._send_bytes(json.dumps(data).encode(), "application/json",
                         cache="no-cache")

    # ----- robots.txt / llms.txt -------------------------------------------

    def _send_robots(self) -> None:
        """Serve robots.txt — allow all crawlers, point to sitemap/API."""
        robots = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /icon\n"
            "\n"
            "User-agent: GPTBot\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            "User-agent: ClaudeBot\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            "User-agent: anthropic-ai\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            "User-agent: PerplexityBot\n"
            "Allow: /\n"
            "Disallow: /api/\n"
        )
        self._send_bytes(robots.encode(), "text/plain", cache="public, max-age=3600")

    def _send_llms(self) -> None:
        """Serve llms.txt — LLM-friendly site summary."""
        pages = self.index.document_count()
        images = self.index.image_count()
        videos = self.index.video_count()
        sites = len(self.index.hosts())
        llms = (
            f"# SerikaSearch\n\n"
            f"> Self-hosted search engine with {pages:,} indexed pages, "
            f"{images:,} images, {videos:,} videos, and {sites:,} sites.\n\n"
            f"## Search\n"
            f"- Web search: /search?q=QUERY\n"
            f"- Image search: /search?q=QUERY&tab=images\n"
            f"- Video search: /search?q=QUERY&tab=videos\n\n"
            f"## API (JSON)\n"
            f"- Web: GET /api/search?q=QUERY&limit=10&page=1\n"
            f"- Images: GET /api/images?q=QUERY&limit=20&page=1\n"
            f"- Videos: GET /api/videos?q=QUERY&limit=20&page=1\n"
            f"- Stats: GET /api/stats\n"
            f"- Similar images: GET /api/similar?src=URL&page=URL&host=HOST\n\n"
            f"## Search operators\n"
            f"- site:example.com — restrict to a host\n"
            f"- -term — exclude a term\n"
            f"- intitle:word — title must contain word\n"
            f"- inurl:word — URL must contain word\n"
            f'- "exact phrase" — quoted phrase match\n\n'
            f"## Content\n"
            f"- All content is crawled respectfully following robots.txt\n"
            f"- Pages are indexed with PostgreSQL tsvector/GIN full-text search\n"
            f"- Images include alt text, dimensions, and source page\n"
            f"- Videos are detected from embedded players (YouTube, Vimeo, etc.)\n"
        )
        self._send_bytes(llms.encode(), "text/plain", cache="public, max-age=3600")

    # ----- pages -----------------------------------------------------------

    def _home(self) -> str:
        body = render("home/index.html", {
            "searchbox": _searchbox(autofocus=True),
            "pages": f"{self.index.document_count():,}",
            "images": f"{self.index.image_count():,}",
            "videos": f"{self.index.video_count():,}",
            "sites": f"{len(self.index.hosts()):,}",
        })
        return _shell("SerikaSearch", body)

    def _web_results(self, q: str, page: int) -> str:
        parsed = parse_query(q)
        if parsed.is_empty:
            return self._home()

        offset = (page - 1) * PAGE_SIZE
        total = self.index.count_matches(parsed)
        results = self.index.search(parsed, PAGE_SIZE, offset)
        img_total = self.index.count_image_matches(parsed)
        vid_total = self.index.count_video_matches(parsed)
        header = _header(q, "web", parsed, total, img_total, vid_total)

        if not results:
            empty = (
                '<div class="empty">'
                f'<h2>No results for &ldquo;{html.escape(q)}&rdquo;</h2>'
                '<p>SerikaSearch only knows what serikacrawler has visited so far.</p>'
                '<p>Try fewer or broader words, or index more sites with '
                '<code>python -m serika crawl seeds/categories/top-tech.txt</code>.</p>'
                '</div>'
            )
            body = render("search/index.html", {
                "header": header, "serp_mod": "", "meta": "",
                "results": empty, "pager": "", "knowledge": "",
            })
            return _shell(f"{q} — SerikaSearch", body)

        cards = "".join(_result_card(r, i) for i, r in enumerate(results, 1))
        last = max(1, -(-total // PAGE_SIZE))
        meta = (
            f'{total:,} result{"" if total == 1 else "s"} '
            f'for &ldquo;{html.escape(q)}&rdquo;'
        )

        # Knowledge panel: only for genuine text queries (not site-only browse,
        # not operator-only queries) so it stays relevant.
        panel_html = ""
        serp_mod = ""
        if parsed.fts and not parsed.has_operators:
            card = build_card(self.index, q)
            if card:
                panel_html = _knowledge_panel(card)
                serp_mod = "with-panel"

        body = render("search/index.html", {
            "header": header,
            "serp_mod": serp_mod,
            "meta": meta,
            "results": cards,
            "pager": _pager(q, "web", page, last),
            "knowledge": panel_html,
        })
        return _shell(f"{q} — SerikaSearch", body)

    def _image_results(self, q: str, page: int, size: str = "") -> str:
        parsed = parse_query(q)
        if parsed.is_empty:
            return self._home()

        offset = (page - 1) * IMAGE_PAGE_SIZE
        if parsed.fts:
            total = self.index.count_image_matches(parsed)
            results = self.index.search_images(parsed, IMAGE_PAGE_SIZE, offset)
        else:
            total = self.index.image_count(parsed.sites)
            results = self.index.browse_images(parsed.sites, IMAGE_PAGE_SIZE, offset)

        # Apply size filter in-memory (dimensions are stored but not in FTS).
        if size and results:
            results = _filter_by_size(results, size)
            total = len(results)

        web_total = self.index.count_matches(parsed)
        vid_total = self.index.count_video_matches(parsed)
        header = _header(q, "images", parsed, web_total, total, vid_total)

        # Build filter buttons.
        base = _qs(q, "images", page)
        filters = _image_filter_buttons(base, size)

        if not results:
            empty = (
                '<div class="empty">'
                f'<h2>No images for &ldquo;{html.escape(q)}&rdquo;</h2>'
                '<p>Images are indexed from the alt text of pages serikacrawler '
                'has crawled. Crawl more sites to grow the image index.</p>'
                '</div>'
            )
            body = render("images/index.html", {
                "header": header, "meta": "", "cards": empty, "pager": "",
                "filters": filters,
            })
            return _shell(f"{q} — Images — SerikaSearch", body)

        cards = "".join(_image_card(r, i) for i, r in enumerate(results, 1))
        last = max(1, -(-total // IMAGE_PAGE_SIZE))
        meta = (
            f'{total:,} image{"" if total == 1 else "s"} '
            f'for &ldquo;{html.escape(q)}&rdquo;'
        )
        body = render("images/index.html", {
            "header": header,
            "meta": meta,
            "cards": cards,
            "pager": _pager(q, "images", page, last),
            "filters": filters,
        })
        return _shell(f"{q} — Images — SerikaSearch", body)

    def _video_results(self, q: str, page: int) -> str:
        parsed = parse_query(q)
        if parsed.is_empty:
            return self._home()

        offset = (page - 1) * PAGE_SIZE
        total = self.index.count_video_matches(parsed)
        results = self.index.search_videos(parsed, PAGE_SIZE, offset)
        web_total = self.index.count_matches(parsed)
        img_total = self.index.count_image_matches(parsed)
        header = _header(q, "videos", parsed, web_total, img_total, total)

        if not results:
            empty = (
                '<div class="empty">'
                f'<h2>No videos for &ldquo;{html.escape(q)}&rdquo;</h2>'
                '<p>Videos are indexed from embedded videos discovered on '
                'crawled pages (YouTube, Vimeo, etc.). Crawl more sites with '
                'video content to grow the video index.</p>'
                '</div>'
            )
            body = render("search/index.html", {
                "header": header, "serp_mod": "", "meta": "",
                "results": empty, "pager": "", "knowledge": "",
            })
            return _shell(f"{q} — Videos — SerikaSearch", body)

        cards = "".join(_video_card(v, i) for i, v in enumerate(results, 1))
        last = max(1, -(-total // PAGE_SIZE))
        meta = (
            f'{total:,} video{"" if total == 1 else "s"} '
            f'for &ldquo;{html.escape(q)}&rdquo;'
        )
        body = render("search/index.html", {
            "header": header,
            "serp_mod": "",
            "meta": meta,
            "results": cards,
            "pager": _pager(q, "videos", page, last),
            "knowledge": "",
        })
        return _shell(f"{q} — Videos — SerikaSearch", body)

    def _not_found(self) -> str:
        body = render("error/index.html", {
            "code": "404",
            "message": "page not found",
        })
        return _shell("Not found — SerikaSearch", body)

    # ----- favicons --------------------------------------------------------

    def _send_icon(self, host: str):
        """Serve a locally cached favicon, or a generated letter tile."""
        if host:
            stored = self.index.get_favicon(host)
            if stored:
                data, ctype = stored
                self._send_bytes(data, ctype, cache="public, max-age=604800")
                return
        letter = html.escape((host[:1] or "?").upper())
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 18 18'>"
            "<rect width='18' height='18' rx='4' fill='#1a1330'/>"
            "<text x='9' y='13' font-family='Helvetica,Arial,sans-serif' "
            "font-size='11' font-weight='bold' text-anchor='middle' "
            f"fill='#a274ff'>{letter}</text></svg>"
        )
        self._send_bytes(
            svg.encode("utf-8"), "image/svg+xml", cache="public, max-age=86400"
        )

    # ----- static assets ---------------------------------------------------

    def _send_static(self, route: str):
        """Serve a file from static/, confined to that directory."""
        rel = route[len("/static/"):]
        safe = os.path.normpath(rel).lstrip("/")
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)):
            self._send_bytes(b"forbidden", "text/plain", 403)
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send_bytes(b"not found", "text/plain", 404)
            return
        ctype, _ = mimetypes.guess_type(full)
        if not ctype:
            ctype = "application/octet-stream"
        # CSS/JS: long cache — version-busted via ?v= in the HTML.
        self._send_bytes(data, ctype, cache="public, max-age=31536000, immutable")

    # ----- io helpers ------------------------------------------------------

    def _send_html(self, content: str, status: int = 200):
        self._send_bytes(content.encode("utf-8"), "text/html; charset=utf-8", status)

    def _send_bytes(self, data: bytes, ctype: str, status: int = 200,
                    cache: str = "no-cache"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client navigated away mid-response


def serve(index_path: str = "serika.db", host: str = "127.0.0.1", port: int = 8000):
    index = Index(index_path)
    Handler.index = index
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(
        f"SerikaSearch → http://{host}:{port}\n"
        f"  {index.document_count():,} pages · {index.image_count():,} images · "
        f"{index.favicon_count():,} favicons"
    )
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
        index.close()
