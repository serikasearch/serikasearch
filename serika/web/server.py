"""SerikaSearch web server — routing, APIs, and static delivery.

A stdlib :class:`ThreadingHTTPServer` in front of the PostgreSQL index. All
markup lives in ``html/`` and is filled in by :mod:`serika.web.render`; this
module is only concerned with dispatch, request validation, headers, and
turning stored rows into the shapes the renderer wants.

Two things here are load-bearing for safety. Every response carries a strict
Content-Security-Policy that forbids inline script, which is why no template
contains an ``onerror`` attribute. And every write path — there is exactly one,
the removal-request form — validates and length-caps its input before it
reaches SQL.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote_plus, urlencode

from ..core import bangs, suggest as suggest_mod, unfurl as unfurl_mod
from ..core.db import Index, FRESHNESS_WINDOWS
from ..core import reference
from ..core.query import parse as parse_query
from .. import tools as tools_mod
from ..tools import live as live_tools
from . import pages as content_pages
from . import render as R
from .templates import HTML_DIR, STATIC_DIR, render

PAGE_SIZE = 10
IMAGE_PAGE_SIZE = 60
VIDEO_PAGE_SIZE = 24
MAX_QUERY_LEN = 400

# Tools whose page hosts a live client widget instead of answering a query.
_INTERACTIVE_TOOL_SLUGS = frozenset({
    "stopwatch", "metronome", "noise", "periodic-table", "font-preview",
    "color-picker", "scale-of-universe", "luggage", "recipe",
    "meeting-planner",
})

# Interactive tool slug -> the widget key its page should render.
_TOOL_WIDGET = {"scale-of-universe": "universe", "luggage": "luggage",
                "recipe": "recipe", "meeting-planner": "meeting"}

# Music-intent keywords: when a Wikipedia panel's description contains one of
# these, the page also fetches a MusicBrainz artist card.
_MUSIC_WORDS = re.compile(
    r"\b(singer|songwriter|rapper|musician|band|duo|group|producer|"
    r"vocalist|guitarist|drummer|dj|composer|virtual youtuber|vtuber|"
    r"idol|girl group|boy band|record producer)\b", re.I)
# Explicit music queries: "<name> discography/albums/tour/songs/concerts".
_ARTIST_INTENT = re.compile(
    r"^(.+?)\s+(discography|albums?|songs?|tour|tours|concerts?|"
    r"setlist|setlists|tickets?)\s*$", re.I)

# Example searches under the home-page search box: one per kind of answer, so
# the range of the thing is visible without reading documentation.
HOME_HINTS = [
    ("1 + 1", "1+1"),
    ("5 km to miles", "5 km to miles"),
    ("weather in tokyo", "weather in tokyo"),
    ("100 usd to eur", "100 usd to eur"),
    ("define serendipity", "define serendipity"),
    ("#a274ff", "#a274ff"),
    ("days until christmas", "days until christmas"),
    ("time in tokyo", "time in tokyo"),
]

# Frames are only ever loaded from the two video providers we embed.
CSP = (
    "default-src 'self'; "
    "img-src 'self' https: data: blob:; "
    "media-src 'self' https:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-src https://www.youtube-nocookie.com https://www.youtube.com "
    "https://player.vimeo.com; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


# --------------------------------------------------------------------------- #
# rate limiting
# --------------------------------------------------------------------------- #

class RateLimiter:
    """A small fixed-window limiter for the JSON API and the one POST route.

    In-process and per-worker by design: it exists to stop a single client
    hammering the index, not to be a distributed quota system. Behind several
    instances, put a real limiter in the proxy.
    """

    def __init__(self, limit: int = 60, window: int = 60):
        self.limit = limit
        self.window = window
        self._hits: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            if len(self._hits) > 20000:          # bound the memory, crudely
                cutoff = now - self.window
                self._hits = {k: v for k, v in self._hits.items()
                              if v[1] > cutoff}
            count, started = self._hits.get(key, (0, now))
            if now - started > self.window:
                count, started = 0, now
            count += 1
            self._hits[key] = (count, started)
            return count <= self.limit


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)


def _clean_host(raw: str) -> str:
    """Normalise whatever a site owner typed into a bare hostname."""
    value = (raw or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split("?")[0].split("#")[0]
    value = value.split("@")[-1].split(":")[0]
    if value.startswith("www."):
        value = value[4:]
    return value if _HOST_RE.match(value) else ""


def _int_param(params, name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(params.get(name, [str(default)])[0])))
    except (ValueError, TypeError):
        return default


def _str_param(params, name: str, default: str = "", limit: int = 200) -> str:
    return (params.get(name, [default])[0] or "").strip()[:limit]


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    index: Index                      # injected by serve()
    api_limiter: RateLimiter
    form_limiter: RateLimiter
    protocol_version = "HTTP/1.1"
    server_version = "SerikaSearch"
    sys_version = ""

    def log_message(self, *args):
        """No access log. Search terms live in the query string, and writing
        them to disk would contradict the privacy policy."""

    # ----- routing ---------------------------------------------------------

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            self._route(route, params)
        except BrokenPipeError:
            pass
        except Exception:
            self._send_html(self._error_page("500", "something went wrong"),
                            status=500)
        finally:
            # Every request runs on its own thread, so the database connection
            # it borrowed has to go back before that thread ends.
            self.index.release()

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        parsed = urlsplit(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route != "/how-to-opt-out":
            self._send_html(self._error_page("404", "page not found"), 404)
            return
        try:
            self._post_optout()
        except Exception:
            self._send_html(self._error_page("500", "something went wrong"), 500)
        finally:
            self.index.release()

    def _route(self, route: str, params: dict) -> None:
        # --- search -------------------------------------------------------
        if route == "/":
            self._send_html(self._home())
        elif route == "/search":
            self._search(params)

        # --- tools --------------------------------------------------------
        elif route == "/tools":
            self._tools_index()
        elif route.startswith("/tools/"):
            self._tool_page(route[len("/tools/"):], params)

        # --- info & legal -------------------------------------------------
        elif route == "/bangs":
            self._bangs_page()
        elif route == "/settings":
            self._simple_page("pages/settings.html", "Settings")
        elif route == "/advanced":
            self._simple_page("pages/advanced.html", "Advanced search")
        elif route == "/stats":
            self._stats_page()
        elif route == "/how-to-opt-out":
            self._optout_page()
        elif content_pages.page_by_slug(route.lstrip("/")):
            self._doc_page(content_pages.page_by_slug(route.lstrip("/")))
        elif route == "/api":
            self._redirect("/api-docs")

        # --- JSON API -----------------------------------------------------
        elif route.startswith("/api/"):
            self._api(route, params)

        # --- machine-readable ---------------------------------------------
        elif route == "/icon":
            self._send_icon(_str_param(params, "h", limit=255))
        elif route == "/robots.txt":
            self._send_robots()
        elif route == "/llms.txt":
            self._send_llms()
        elif route == "/opensearch.xml":
            self._send_opensearch()
        elif route == "/sitemap.xml":
            self._send_sitemap()
        elif route == "/manifest.webmanifest":
            self._send_manifest()
        elif route == "/healthz":
            self._send_bytes(b"ok", "text/plain")
        elif route.startswith("/static/"):
            self._send_static(route)
        else:
            self._send_html(self._error_page("404", "page not found"), 404)

    # ----- request context -------------------------------------------------

    def _client_ip(self) -> str:
        """The visitor's address.

        ``X-Forwarded-For`` is trusted because this is designed to sit behind a
        reverse proxy (Coolify, nginx, a CDN). If you expose the server
        directly, a client can forge that header — it is only used for rate
        limiting and for echoing back in the "my ip" answer, never for access
        control, so the worst case is a bypassed rate limit.
        """
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first[:64]
        return (self.client_address[0] if self.client_address else "")[:64]

    def _new_tab(self) -> bool:
        """Result links open in a new tab by default; the settings page can
        turn that off, and stores the choice as a cookie-free localStorage flag
        that JS applies after load."""
        return True

    # ======================================================================= #
    # search
    # ======================================================================= #

    def _search(self, params: dict) -> None:
        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        tab = _str_param(params, "tab", "web", 12)
        page = _int_param(params, "page", 1, 1, 500)

        if not q:
            self._send_html(self._home())
            return

        # !bang shortcuts jump straight out, before any work is done.
        destination = bangs.resolve_bang(q)
        if destination:
            self._redirect(destination, permanent=False)
            return

        if tab == "images":
            self._send_html(self._image_results(
                q, page, _str_param(params, "size", limit=12)))
        elif tab == "videos":
            self._send_html(self._video_results(q, page))
        else:
            self._send_html(self._web_results(
                q, page,
                freshness=_str_param(params, "when", limit=12),
                source=_str_param(params, "src", limit=20),
            ))

    def _counts(self, parsed, freshness: str = "") -> dict:
        return {
            "web": self.index.count_matches(parsed, freshness=freshness),
            "images": self.index.count_image_matches(parsed),
            "videos": self.index.count_video_matches(parsed),
        }

    def _instant_answer(self, q: str) -> str:
        """The answer strip above results — a dictionary entry, or a tool."""
        word = reference.parse_define(q)
        if word:
            entry = reference.define(word)
            if entry:
                return R.dictionary_card(entry)

        answer = tools_mod.resolve(q, {
            "client_ip": self._client_ip(),
            "user_agent": self.headers.get("User-Agent", ""),
        })
        return R.answer_card(answer)

    def _artist_card(self, q: str, parsed, knowledge_card) -> str:
        """A MusicBrainz artist card when the query is about a musician.

        Triggered explicitly ("<name> discography/tour/…") or implicitly when
        the knowledge panel's summary describes a musician. Kept off the path
        for ordinary queries so most searches never pay for the lookup.
        """
        from ..tools import music

        name = ""
        intent = _ARTIST_INTENT.match(q.strip())
        if intent:
            name = intent.group(1).strip()
        elif knowledge_card is not None:
            summary = (getattr(knowledge_card, "summary", "") or "")[:400]
            facts = " ".join(v for _, v in getattr(knowledge_card, "facts", []))
            if _MUSIC_WORDS.search(summary + " " + facts):
                name = knowledge_card.title
        if not name or len(name) < 2:
            return ""

        try:
            card = music.lookup_artist(name)
        except Exception:
            return ""
        if card is None:
            return ""
        return R.artist_card(card)

    def _web_results(self, q: str, page: int, freshness: str = "",
                     source: str = "") -> str:
        parsed = parse_query(q)
        if parsed.is_empty:
            return self._home()

        if freshness not in FRESHNESS_WINDOWS:
            freshness = ""

        offset = (page - 1) * PAGE_SIZE
        total = self.index.count_matches(parsed, freshness=freshness)
        results = self.index.search(parsed, PAGE_SIZE, offset,
                                    freshness=freshness)
        counts = {
            "web": total,
            "images": self.index.count_image_matches(parsed),
            "videos": self.index.count_video_matches(parsed),
        }
        header = R.header(q, "web", parsed, counts)
        answer_html = self._instant_answer(q)
        filters = R.freshness_filters(q, "web", freshness)

        # An explicit "<name> discography/tour/…" often has no web results (the
        # keyword narrows the index), so resolve the artist card before the
        # empty-results check — otherwise it would be lost.
        if page == 1 and _ARTIST_INTENT.match(q.strip()):
            answer_html = self._artist_card(q, parsed, None) + answer_html

        if not results:
            return self._empty_web(q, header, answer_html, filters, freshness)

        metas = self.index.page_meta([r.url for r in results])
        cards = R.result_cards(results, metas, new_tab=self._new_tab())
        last = max(1, -(-total // PAGE_SIZE))

        meta_line = (f'{total:,} result{"" if total == 1 else "s"} '
                     f'for &ldquo;{html.escape(q)}&rdquo;')

        # The knowledge panel is for genuine subject queries — an operator-only
        # or site-scoped query is browsing, not asking about a thing.
        panel_html = ""
        serp_mod = ""
        if parsed.fts and not parsed.has_operators and page == 1:
            card = reference.build_card(self.index, q, source)
            if card:
                panel_html = R.knowledge_panel(card, q)
                serp_mod = "with-panel"
            # Enrich musicians with a MusicBrainz card (discography, links).
            # The explicit-intent case is already handled above the results
            # check, so here we only do the implicit (panel-says-musician) path.
            if not _ARTIST_INTENT.match(q.strip()):
                artist_html = self._artist_card(q, parsed, card)
                if artist_html:
                    answer_html = artist_html + answer_html

        related = ""
        if page == 1 and parsed.fts:
            related = R.related_block(
                suggest_mod.related_searches(q, [r.title for r in results])
            )

        body = render("search/index.html", {
            "header": header,
            "h1": f"Search results for {q}",
            "serp_mod": serp_mod,
            "meta": meta_line,
            "filters": filters,
            "answer": answer_html,
            "did_you_mean": "",
            "results": cards,
            "pager": R.pager(q, "web", page, last, when=freshness),
            "related": related,
            "knowledge": panel_html,
        })
        return R.shell(f"{q} — SerikaSearch", body,
                       description=f"Search results for {q}.")

    def _empty_web(self, q: str, header: str, answer_html: str,
                   filters: str, freshness: str) -> str:
        """No hits. Offer a correction and some ways forward rather than a
        dead end — an empty result set is usually a spelling or scope problem."""
        correction = ""
        try:
            correction = suggest_mod.did_you_mean(q, self.index.vocabulary())
        except Exception:
            correction = ""

        suggestion_html = ""
        if correction:
            suggestion_html = (
                f'<p style="margin-bottom:var(--sp-4)">Did you mean '
                f'<a href="/search?q={quote_plus(correction)}">'
                f"<strong>{html.escape(correction)}</strong></a>?</p>"
            )

        extra = ""
        if freshness:
            extra = ('<p>The “indexed within” filter is active — '
                     f'<a href="/search?q={quote_plus(q)}">search all time</a> '
                     "instead.</p>")

        empty = (
            '<div class="empty">'
            f'<h2>No results for &ldquo;{html.escape(q)}&rdquo;</h2>'
            f"{suggestion_html}{extra}"
            "<p>SerikaSearch has its own index, which is smaller than the big "
            "engines&rsquo;. If a page hasn&rsquo;t been crawled yet, it "
            "isn&rsquo;t here.</p>"
            "<p>Try fewer or broader words, drop any operators, or jump "
            "somewhere else with a "
            f'<a href="/bangs">!bang</a> — e.g. '
            f'<code>!w {html.escape(q[:40])}</code>.</p>'
            f'<div class="empty-suggestions">'
            f'<a class="chip" href="/search?q={quote_plus(q)}&amp;tab=images">'
            f"Try images</a>"
            f'<a class="chip" href="/help">Search help</a>'
            f'<a class="chip" href="/tools">Tools</a></div>'
            "</div>"
        )
        body = render("search/index.html", {
            "header": header, "h1": f"Search results for {q}",
            "serp_mod": "", "meta": "", "filters": filters,
            "answer": answer_html, "did_you_mean": "", "results": empty,
            "pager": "", "related": "", "knowledge": "",
        })
        return R.shell(f"{q} — SerikaSearch", body)

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
            results = self.index.browse_images(parsed.sites, IMAGE_PAGE_SIZE,
                                               offset)

        if size and results:
            results = _filter_by_size(results, size)

        counts = {
            "web": self.index.count_matches(parsed),
            "images": total,
            "videos": self.index.count_video_matches(parsed),
        }
        header = R.header(q, "images", parsed, counts)
        filters = R.image_filters(q, size, page)

        if not results:
            empty = (
                '<div class="empty">'
                f'<h2>No images for &ldquo;{html.escape(q)}&rdquo;</h2>'
                "<p>Images are indexed from the pages that display them, using "
                "the alt text those pages provide. A subject with no crawled "
                "pages has no images here.</p>"
                f'<div class="empty-suggestions">'
                f'<a class="chip" href="/search?q={quote_plus(q)}">Try web '
                f"results</a></div></div>"
            )
            body = render("images/index.html", {
                "header": header, "h1": f"Image results for {q}",
                "meta": "", "cards": empty, "pager": "",
                "filters": filters, "query": q, "page": page, "size": size,
            })
            return R.shell(f"{q} — Images — SerikaSearch", body)

        cards = R.image_cards(results, 1, new_tab=self._new_tab())
        last = max(1, -(-total // IMAGE_PAGE_SIZE))
        meta_line = (f'{total:,} image{"" if total == 1 else "s"} '
                     f'for &ldquo;{html.escape(q)}&rdquo;')

        body = render("images/index.html", {
            "header": header,
            "h1": f"Image results for {q}",
            "meta": meta_line,
            "cards": cards,
            "pager": R.pager(q, "images", page, last, size=size),
            "filters": filters,
            "query": q,
            "page": page,
            "size": size,
        })
        return R.shell(f"{q} — Images — SerikaSearch", body,
                       description=f"Image results for {q}.")

    def _video_results(self, q: str, page: int) -> str:
        parsed = parse_query(q)
        if parsed.is_empty:
            return self._home()

        offset = (page - 1) * VIDEO_PAGE_SIZE
        total = self.index.count_video_matches(parsed)
        results = self.index.search_videos(parsed, VIDEO_PAGE_SIZE, offset)
        counts = {
            "web": self.index.count_matches(parsed),
            "images": self.index.count_image_matches(parsed),
            "videos": total,
        }
        header = R.header(q, "videos", parsed, counts)

        if not results:
            empty = (
                '<div class="empty">'
                f'<h2>No videos for &ldquo;{html.escape(q)}&rdquo;</h2>'
                "<p>Videos are found as embeds on crawled pages — YouTube, "
                "Vimeo and the like. Nothing crawled so far embeds a video "
                "about this.</p></div>"
            )
            body = render("search/videos.html", {
                "header": header, "h1": f"Video results for {q}",
                "meta": "", "results": empty, "pager": "",
            })
            return R.shell(f"{q} — Videos — SerikaSearch", body)

        last = max(1, -(-total // VIDEO_PAGE_SIZE))
        meta_line = (f'{total:,} video{"" if total == 1 else "s"} '
                     f'for &ldquo;{html.escape(q)}&rdquo;')
        body = render("search/videos.html", {
            "header": header,
            "h1": f"Video results for {q}",
            "meta": meta_line,
            "results": R.video_cards(results),
            "pager": R.pager(q, "videos", page, last),
        })
        return R.shell(f"{q} — Videos — SerikaSearch", body)

    # ======================================================================= #
    # pages
    # ======================================================================= #

    def _home(self) -> str:
        body = render("home/index.html", {
            "searchbox": R.searchbox(autofocus=True,
                                     placeholder="Search the web, or ask a question"),
            "hints": R.hint_chips(HOME_HINTS[:5]),
            "tools": R.home_tool_strip(12),
            "pages": f"{self.index.document_count():,}",
            "images": f"{self.index.image_count():,}",
            "videos": f"{self.index.video_count():,}",
            "sites": f"{len(self.index.hosts()):,}",
        })
        return R.shell("SerikaSearch — independent search", body)

    def _doc_page(self, page) -> str | None:
        body = render("pages/index.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "kicker": page.kicker,
            "title": page.title,
            "summary": page.summary,
            "updated": page.updated,
            "toc": R.doc_toc(page),
            "sections": R.doc_sections(page),
        })
        self._send_html(R.shell(f"{page.title} — SerikaSearch", body,
                                description=page.summary[:200]))

    def _simple_page(self, template: str, title: str) -> None:
        body = render(template, {
            "header": R.header("", "", None, {}, show_tabs=False),
        })
        self._send_html(R.shell(f"{title} — SerikaSearch", body))

    def _tools_index(self) -> None:
        body = render("tools/index.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "blurb": ("Twenty-odd calculators and converters that answer "
                      "straight in the search box. Most run locally with no "
                      "network call at all — type the example on any card into "
                      "the search bar and the answer appears above the "
                      "results."),
            "groups": R.tool_group_blocks(),
        })
        self._send_html(R.shell("Tools — SerikaSearch", body,
                                description="Calculators, converters and "
                                            "generators built into search."))

    def _tool_page(self, slug: str, params: dict) -> None:
        tool = tools_mod.tool_by_slug(slug[:40])
        if tool is None:
            self._send_html(self._error_page("404", "no such tool"), 404)
            return

        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        answer_html = ""
        # Interactive widgets (stopwatch, metronome, periodic table, …) are live
        # components, not query answers — render them on the bare tool page.
        if slug in _INTERACTIVE_TOOL_SLUGS and not q:
            bpm = _str_param(params, "bpm", limit=4)
            answer_html = R.interactive_widget(_TOOL_WIDGET.get(slug, slug), bpm)
        elif slug == "artist" and q:
            # The artist card lives in the web layer, not the resolver.
            from ..tools import music
            try:
                card = music.lookup_artist(_ARTIST_INTENT.sub(r"\1", q.strip()))
            except Exception:
                card = None
            answer_html = (R.artist_card(card) if card else
                           '<div class="notice bad">No artist found for '
                           f"“{html.escape(q)}”.</div>")
        elif q:
            answer_html = self._instant_answer(q)
            if not answer_html:
                answer_html = (
                    '<div class="notice bad">Couldn&rsquo;t read that as '
                    f"a {html.escape(tool.name.lower())} input. Try one of the "
                    "examples above.</div>"
                )

        examples = _TOOL_EXAMPLES.get(slug, [tool.example])
        example_chips = "".join(
            f'<a class="chip" href="/tools/{html.escape(slug)}'
            f'?q={quote_plus(example)}">{html.escape(example)}</a>'
            for example in examples
        )
        others = [t for t in tools_mod.TOOLS if t.slug != slug][:6]

        body = render("tools/detail.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "name": tool.name,
            "blurb": tool.blurb,
            "slug": slug,
            "query": q,
            "example": tool.example,
            "examples": example_chips,
            "answer": answer_html,
            "others": R.tool_cards(others),
        })
        self._send_html(R.shell(f"{tool.name} — SerikaSearch", body,
                                description=tool.blurb))

    def _bangs_page(self) -> None:
        body = render("pages/bangs.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "groups": R.bang_groups(),
        })
        self._send_html(R.shell("!bang shortcuts — SerikaSearch", body,
                                description="Jump straight to another site's "
                                            "search with a bang."))

    def _stats_page(self) -> None:
        pages_n = self.index.document_count()
        hosts = self.index.hosts()
        stats = [
            ("Pages", f"{pages_n:,}", "web"),
            ("Images", f"{self.index.image_count():,}", "image"),
            ("Videos", f"{self.index.video_count():,}", "video"),
            ("Sites", f"{len(hosts):,}", "spark"),
            ("Favicons cached", f"{self.index.favicon_count():,}", "id"),
            ("Rich metadata", f"{self.index.page_meta_count():,}", "code"),
            ("Queued to crawl", f"{self.index.frontier_pending():,}", "arrow"),
        ]
        categories_html = ""
        categories = self.index.categories()
        if categories:
            rows = "".join(
                f'<a class="bang-item" href="/search?q={quote_plus(name)}">'
                f'<span class="bang-name">{html.escape(name)}</span>'
                f'<span class="bang-alt">{count:,}</span></a>'
                for name, count in categories[:24]
            )
            categories_html = (
                '<section class="tool-group"><h2>Categories</h2>'
                f'<div class="bang-grid">{rows}</div></section>'
            )

        body = render("pages/stats.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "cards": R.stat_cards(stats),
            "hosts": R.host_rows(hosts[:30]),
            "categories": categories_html,
        })
        self._send_html(R.shell("Index statistics — SerikaSearch", body))

    # ----- opt-out ---------------------------------------------------------

    def _optout_page(self, notice: str = "", host_value: str = "",
                     email_value: str = "", status: int = 200) -> None:
        page = content_pages.page_by_slug("how-to-opt-out")
        body = render("pages/optout.html", {
            "header": R.header("", "", None, {}, show_tabs=False),
            "kicker": page.kicker,
            "title": page.title,
            "summary": page.summary,
            "updated": page.updated,
            "toc": R.doc_toc(page) + '<li><a href="#removal-form">Removal '
                                     "request form</a></li>",
            "sections": R.doc_sections(page),
            "notice": notice,
            "host_value": host_value,
            "email_value": email_value,
        })
        self._send_html(R.shell("How to opt out — SerikaSearch", body,
                                description=page.summary[:200]), status)

    def _post_optout(self) -> None:
        if not self.form_limiter.allow(self._client_ip()):
            self._optout_page(
                '<div class="notice bad">Too many requests from this address. '
                "Please wait a few minutes and try again.</div>",
                status=429,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self._optout_page(
                '<div class="notice bad">That request was empty or too '
                "large.</div>", status=400)
            return

        raw = self.rfile.read(length).decode("utf-8", "replace")
        fields = parse_qs(raw, keep_blank_values=True)

        host = _clean_host(_str_param(fields, "host", limit=255))
        email = _str_param(fields, "email", limit=255)
        scope = _str_param(fields, "scope", "all", limit=40)
        note = _str_param(fields, "note", limit=2000)

        if not host:
            self._optout_page(
                '<div class="notice bad">That doesn&rsquo;t look like a '
                "domain. Enter it as <code>example.com</code>, without "
                "<code>https://</code> or a path.</div>",
                host_value=_str_param(fields, "host", limit=120),
                email_value=email, status=400)
            return
        if not _EMAIL_RE.match(email):
            self._optout_page(
                '<div class="notice bad">A working contact email is needed so '
                "the request can be verified.</div>",
                host_value=host, email_value="", status=400)
            return
        if scope not in ("all", "pages", "images", "crawl"):
            scope = "all"

        try:
            request_id = self.index.add_optout_request(host, email, scope, note)
        except Exception:
            self._optout_page(
                '<div class="notice bad">The request couldn&rsquo;t be saved. '
                "Please try again, or email us directly.</div>",
                host_value=host, email_value=email, status=500)
            return

        self._optout_page(
            '<div class="notice good"><strong>Request received.</strong> '
            f"Reference <code>#{request_id}</code> for "
            f"<code>{html.escape(host)}</code>. A person will verify that you "
            "control the domain and then action it — adding the robots.txt "
            "directive above is the quickest way to prove that, and it stops "
            "re-crawling immediately. You&rsquo;ll get an email when it is "
            "done.</div>"
        )

    def _error_page(self, code: str, message: str) -> str:
        body = render("error/index.html", {
            "code": code,
            "message": message,
            "searchbox": R.searchbox(),
        })
        return R.shell(f"{code} — SerikaSearch", body)

    # ======================================================================= #
    # JSON API
    # ======================================================================= #

    def _api(self, route: str, params: dict) -> None:
        if not self.api_limiter.allow(self._client_ip()):
            self._send_json({"error": "rate limited",
                             "detail": "Too many requests. Please slow down "
                                       "and cache responses."}, status=429)
            return

        if route == "/api/search":
            self._api_search(params)
        elif route == "/api/images":
            self._api_images(params)
        elif route == "/api/videos":
            self._api_videos(params)
        elif route == "/api/suggest":
            self._api_suggest(params)
        elif route == "/api/answer":
            self._api_answer(params)
        elif route == "/api/define":
            self._api_define(params)
        elif route == "/api/unfurl":
            self._api_unfurl(params)
        elif route == "/api/similar":
            self._api_similar(params)
        elif route == "/api/stats":
            self._api_stats()
        else:
            self._send_json({"error": "unknown endpoint"}, status=404)

    def _api_search(self, params) -> None:
        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        limit = _int_param(params, "limit", 10, 1, 50)
        page = _int_param(params, "page", 1, 1, 500)
        freshness = _str_param(params, "when", limit=12)
        if freshness not in FRESHNESS_WINDOWS:
            freshness = ""
        if not q:
            self._send_json({"error": "missing q"}, status=400)
            return

        parsed = parse_query(q)
        offset = (page - 1) * limit
        total = self.index.count_matches(parsed, freshness=freshness)
        results = self.index.search(parsed, limit, offset, freshness=freshness)
        metas = self.index.page_meta([r.url for r in results])

        answer = tools_mod.resolve(q, {"client_ip": ""})
        self._send_json({
            "query": q,
            "total": total,
            "page": page,
            "limit": limit,
            "answer": _answer_json(answer),
            "results": [{
                "title": r.title,
                "url": r.url,
                "host": r.host,
                "description": r.description,
                "snippet": r.snippet.replace("<mark>", "").replace("</mark>", ""),
                "score": round(r.score, 4),
                "meta": metas.get(r.url, {}),
            } for r in results],
        })

    def _api_images(self, params) -> None:
        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        limit = _int_param(params, "limit", 20, 1, 100)
        page = _int_param(params, "page", 1, 1, 500)
        size = _str_param(params, "size", limit=12)
        if not q:
            self._send_json({"error": "missing q"}, status=400)
            return

        parsed = parse_query(q)
        offset = (page - 1) * limit
        total = self.index.count_image_matches(parsed)
        results = self.index.search_images(parsed, limit, offset)
        if size:
            results = _filter_by_size(results, size)
        self._send_json({
            "query": q, "total": total, "page": page, "limit": limit,
            "results": [{
                "src": r.src, "page_url": r.page_url, "page_title": r.page_title,
                "host": r.host, "alt": r.alt, "width": r.width,
                "height": r.height, "score": round(r.score, 4),
            } for r in results],
        })

    def _api_videos(self, params) -> None:
        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        limit = _int_param(params, "limit", 20, 1, 50)
        page = _int_param(params, "page", 1, 1, 500)
        if not q:
            self._send_json({"error": "missing q"}, status=400)
            return
        parsed = parse_query(q)
        self._send_json({
            "query": q,
            "total": self.index.count_video_matches(parsed),
            "page": page, "limit": limit,
            "results": self.index.search_videos(parsed, limit,
                                                (page - 1) * limit),
        })

    def _api_suggest(self, params) -> None:
        q = _str_param(params, "q", limit=80)
        if len(q) < 2:
            self._send_json({"query": q, "suggestions": []})
            return
        # A bang being typed completes to bangs, not to page titles.
        if q.startswith("!"):
            prefix = q[1:].lower()
            matches = []
            for bang in bangs.BANGS:
                for key in bang.keys:
                    if key.startswith(prefix):
                        matches.append(f"!{key} ")
                        break
                if len(matches) >= 8:
                    break
            self._send_json({"query": q, "suggestions": matches,
                             "kind": "bang"})
            return
        self._send_json({
            "query": q,
            "suggestions": self.index.suggest(q, 8),
            "kind": "title",
        })

    def _api_answer(self, params) -> None:
        q = _str_param(params, "q", limit=MAX_QUERY_LEN)
        if not q:
            self._send_json({"error": "missing q"}, status=400)
            return
        answer = tools_mod.resolve(q, {"client_ip": self._client_ip(),
                                       "user_agent": ""})
        self._send_json({"query": q, "answer": _answer_json(answer)})

    def _api_define(self, params) -> None:
        word = _str_param(params, "w", limit=40) or _str_param(params, "q",
                                                               limit=40)
        if not word:
            self._send_json({"error": "missing w"}, status=400)
            return
        entry = reference.define(word)
        if entry is None:
            self._send_json({"error": "not found", "word": word}, status=404)
            return
        self._send_json({
            "word": entry.word,
            "phonetic": entry.phonetic,
            "audio": entry.audio,
            "origin": entry.origin,
            "source": entry.source_name,
            "source_url": entry.source_url,
            "senses": [{
                "part_of_speech": sense.part_of_speech,
                "definitions": [{"definition": d, "example": x}
                                for d, x in sense.definitions],
                "synonyms": sense.synonyms,
                "antonyms": sense.antonyms,
            } for sense in entry.senses],
        })

    def _api_unfurl(self, params) -> None:
        url = _str_param(params, "url", limit=2000)
        if not url:
            self._send_json({"error": "missing url"}, status=400)
            return
        try:
            data = unfurl_mod.unfurl(url)
        except unfurl_mod.UnfurlError as error:
            self._send_json({"error": str(error), "url": url}, status=400)
            return
        self._send_json(data)

    def _api_similar(self, params) -> None:
        src = _str_param(params, "src", limit=2000)
        page_url = _str_param(params, "page", limit=2000)
        host = _str_param(params, "host", limit=255)
        related = self.index.similar_images(src, page_url, host, limit=14)
        self._send_json([{
            "src": r.src,
            "page": r.page_url or r.src,
            "title": (r.alt or r.page_title or r.host)[:120],
            "host": r.host,
            "width": r.width,
            "height": r.height,
            "ratio": round(r.width / r.height, 4) if (r.width and r.height) else 1.5,
        } for r in related])

    def _api_stats(self) -> None:
        if self.index._redis:
            try:
                self.index._redis.delete("stats:doc_count")
            except Exception:
                pass
        self._send_json({
            "pages": self.index.document_count(),
            "images": self.index.image_count(),
            "videos": self.index.video_count(),
            "favicons": self.index.favicon_count(),
            "rich_metadata": self.index.page_meta_count(),
            "sites": len(self.index.hosts()),
            "frontier_pending": self.index.frontier_pending(),
            "categories": [{"name": c, "count": n}
                           for c, n in self.index.categories()],
        })

    # ======================================================================= #
    # machine-readable resources
    # ======================================================================= #

    def _origin(self) -> str:
        host = self.headers.get("Host", "localhost:8000")
        proto = self.headers.get("X-Forwarded-Proto", "")
        if proto not in ("http", "https"):
            proto = "https" if not host.startswith("localhost") else "http"
        return f"{proto}://{host}"

    def _send_robots(self) -> None:
        robots = (
            "# SerikaSearch — see /how-to-opt-out for how to keep your own\n"
            "# site out of our index.\n\n"
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /icon\n"
            "Disallow: /search\n"
            "Disallow: /tools/\n\n"
            f"Sitemap: {self._origin()}/sitemap.xml\n"
        )
        self._send_bytes(robots.encode(), "text/plain; charset=utf-8",
                         cache="public, max-age=3600")

    def _send_llms(self) -> None:
        pages_n = self.index.document_count()
        llms = (
            f"# SerikaSearch\n\n"
            f"> An independent search engine with its own crawler and index: "
            f"{pages_n:,} pages, {self.index.image_count():,} images, "
            f"{self.index.video_count():,} videos, "
            f"{len(self.index.hosts()):,} sites. No ads, no tracking, no "
            f"generated answers.\n\n"
            f"## Search\n"
            f"- Web: /search?q=QUERY\n"
            f"- Images: /search?q=QUERY&tab=images\n"
            f"- Videos: /search?q=QUERY&tab=videos\n\n"
            f"## JSON API (no key required, rate limited)\n"
            f"- /api/search?q=QUERY&limit=10&page=1&when=week\n"
            f"- /api/images?q=QUERY&limit=20&page=1\n"
            f"- /api/videos?q=QUERY&limit=20&page=1\n"
            f"- /api/suggest?q=PREFIX\n"
            f"- /api/answer?q=QUERY — instant answer only\n"
            f"- /api/define?w=WORD\n"
            f"- /api/unfurl?url=URL — Open Graph / oEmbed metadata\n"
            f"- /api/similar?src=URL&page=URL&host=HOST\n"
            f"- /api/stats\n\n"
            f"## Search operators\n"
            f"- site:example.com — restrict to a host\n"
            f"- -term — exclude a term\n"
            f"- intitle:word — the title must contain the word\n"
            f"- inurl:word — the URL must contain the word\n"
            f'- "exact phrase" — quoted phrase match\n'
            f"- !bang — redirect to another site's search (see /bangs)\n\n"
            f"## Instant answers\n"
            f"Calculator, unit and currency conversion, world clock, date "
            f"arithmetic, dictionary, colour conversion, hashes, encodings, "
            f"QR codes, password and UUID generation. Full list: /tools\n\n"
            f"## Policies\n"
            f"- Privacy: /privacy\n"
            f"- Terms: /terms\n"
            f"- Opt out of the crawler: /how-to-opt-out\n"
            f"- Copyright: /dmca\n\n"
            f"## Crawling\n"
            f"- Our crawler is serikacrawler and it obeys robots.txt.\n"
            f"- Pages are indexed with PostgreSQL tsvector full-text search.\n"
            f"- Images are referenced, never copied or re-hosted.\n"
        )
        self._send_bytes(llms.encode(), "text/plain; charset=utf-8",
                         cache="public, max-age=3600")

    def _send_opensearch(self) -> None:
        origin = html.escape(self._origin(), quote=True)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/"'
            ' xmlns:moz="http://www.mozilla.org/2006/browser/search/">\n'
            "  <ShortName>SerikaSearch</ShortName>\n"
            "  <Description>Independent search with its own crawler and index."
            "</Description>\n"
            "  <InputEncoding>UTF-8</InputEncoding>\n"
            f'  <Image width="64" height="64" type="image/svg+xml">'
            f"{origin}/static/icon.svg</Image>\n"
            f'  <Url type="text/html" method="get" template="{origin}/search?q='
            '{searchTerms}"/>\n'
            f'  <Url type="application/json" method="get" template="{origin}'
            '/api/suggest?q={searchTerms}"/>\n'
            f'  <moz:SearchForm>{origin}/</moz:SearchForm>\n'
            "</OpenSearchDescription>\n"
        )
        self._send_bytes(xml.encode(), "application/opensearchdescription+xml",
                         cache="public, max-age=86400")

    def _send_sitemap(self) -> None:
        origin = self._origin()
        routes = ["/", "/tools", "/bangs", "/advanced", "/stats", "/settings"]
        routes += [f"/{slug}" for slug in content_pages.LEGAL_NAV]
        routes += [f"/tools/{tool.slug}" for tool in tools_mod.TOOLS]
        entries = "".join(
            f"  <url><loc>{html.escape(origin + route)}</loc>"
            f"<changefreq>weekly</changefreq></url>\n"
            for route in routes
        )
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               f"{entries}</urlset>\n")
        self._send_bytes(xml.encode(), "application/xml",
                         cache="public, max-age=86400")

    def _send_manifest(self) -> None:
        manifest = {
            "name": "SerikaSearch",
            "short_name": "Serika",
            "description": "Independent search with its own crawler and index.",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#000000",
            "theme_color": "#000000",
            "icons": [{
                "src": "/static/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }],
            "categories": ["utilities", "productivity"],
        }
        self._send_bytes(json.dumps(manifest).encode(),
                         "application/manifest+json",
                         cache="public, max-age=86400")

    # ----- favicons --------------------------------------------------------

    def _send_icon(self, host: str) -> None:
        """A locally cached favicon, or a generated letter tile.

        Serving these ourselves is why result pages make no third-party
        requests for icons.
        """
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
        self._send_bytes(svg.encode("utf-8"), "image/svg+xml",
                         cache="public, max-age=86400")

    # ----- static ----------------------------------------------------------

    def _send_static(self, route: str) -> None:
        relative = route[len("/static/"):]
        safe = os.path.normpath(relative).lstrip("/")
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)):
            self._send_bytes(b"forbidden", "text/plain", 403)
            return
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError:
            self._send_bytes(b"not found", "text/plain", 404)
            return
        ctype, _ = mimetypes.guess_type(full)
        self._send_bytes(data, ctype or "application/octet-stream",
                         cache="public, max-age=31536000, immutable")

    # ----- io --------------------------------------------------------------

    def _redirect(self, location: str, permanent: bool = False) -> None:
        try:
            self.send_response(301 if permanent else 302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Referrer-Policy",
                             "strict-origin-when-cross-origin")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_html(self, content: str, status: int = 200) -> None:
        self._send_bytes(content.encode("utf-8"),
                         "text/html; charset=utf-8", status)

    def _send_json(self, data, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8", status,
            cache="no-store", cors=True,
        )

    def _send_bytes(self, data: bytes, ctype: str, status: int = 200,
                    cache: str = "no-cache", cors: bool = False) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("Referrer-Policy",
                             "strict-origin-when-cross-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Permissions-Policy",
                             "geolocation=(), microphone=(), camera=(), "
                             "interest-cohort=(), browsing-topics=()")
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass    # the client navigated away mid-response


# --------------------------------------------------------------------------- #
# module helpers
# --------------------------------------------------------------------------- #

def _filter_by_size(results, size: str):
    """Filter image results by size or aspect category.

    Dimensions aren't part of the full-text index, so this runs in memory over
    the current page of results rather than in SQL.
    """
    if not size:
        return results
    out = []
    for r in results:
        width, height = r.width, r.height
        if not width or not height:
            continue
        ratio = width / height
        area = width * height
        if size == "large" and area >= 200_000:
            out.append(r)
        elif size == "medium" and 40_000 <= area < 200_000:
            out.append(r)
        elif size == "wide" and ratio >= 1.4:
            out.append(r)
        elif size == "tall" and ratio <= 0.8:
            out.append(r)
    return out


def _answer_json(answer) -> dict | None:
    """Serialise an instant answer for the API, minus the rendered SVG."""
    if answer is None:
        return None
    data = {k: v for k, v in (answer.data or {}).items() if k != "svg"}
    return {
        "kind": answer.kind,
        "title": answer.title,
        "subtitle": answer.subtitle,
        "detail": answer.detail,
        "rows": [{"label": k, "value": v} for k, v in answer.rows],
        "items": answer.items,
        "source": answer.source,
        "source_url": answer.source_url,
        "tool": answer.tool,
        "data": data,
    }


_TOOL_EXAMPLES = {
    "calculator": ["1+1", "sqrt(144)", "2^10", "15% of 80", "5!"],
    "unit-converter": ["5 km to miles", "180 f in c", "2 cups to ml",
                       "1 GiB in MB", "60 mph to kmh"],
    "percentage": ["20% of 80", "20% off 250", "12 is what percent of 60",
                   "from 40 to 60 percent change"],
    "base-converter": ["255 in binary", "0xff to decimal", "1011 to hex"],
    "roman-numerals": ["MCMXCIV", "1994 in roman", "MMXXVI"],
    "currency": ["100 usd to eur", "£20 in dollars", "usd to jpy"],
    "weather": ["weather in tokyo", "berlin forecast", "weather in new york"],
    "world-clock": ["time in tokyo", "time in new york", "time in london"],
    "date-calculator": ["days until christmas", "days between 2020-01-01 and "
                        "2026-01-01", "what day was 1990-05-12", "age 1990-05-12"],
    "timestamp": ["unix timestamp", "1700000000 to date"],
    "color-picker": ["#a274ff", "rgb(162,116,255)", "rebeccapurple", "teal"],
    "qr": ["qr code for https://serikasearch.com", "qr code for hello world"],
    "encoder": ["base64 encode hello", "url decode a%20b", "rot13 secret",
                "html encode <b>hi</b>"],
    "hash": ["sha256 hello", "md5 password", "crc32 serika"],
    "password": ["generate password", "24 character password", "passphrase"],
    "uuid": ["uuid"],
    "random": ["flip a coin", "roll 2d6", "random number between 1 and 100"],
    "word-count": ["word count: the quick brown fox jumps over the lazy dog"],
    "lorem": ["lorem ipsum", "5 paragraphs of lorem"],
    "dictionary": ["define serendipity", "define ephemeral",
                   "what does ubiquitous mean"],
    "bmi": ["bmi 180cm 75kg", "bmi 5'10 160lb", "bmi 1.8m 90kg"],
    "split": ["split bill 120 by 3 at 18%", "split bill 84.50 between 4",
              "tip on 50 at 20%"],
    "sun": ["sunrise in tokyo", "sunset in london", "berlin sunrise"],
    "anagram": ["anagram listen", "unscramble aelpp", "words from tables"],
    "morse": ["morse code hello", "morse SOS", "morse .... . .-.. .-.. ---"],
    "stopwatch": ["stopwatch", "timer", "countdown"],
    "metronome": ["metronome", "metronome 120 bpm"],
    "noise": ["white noise", "pink noise", "brown noise", "rain sound",
              "ocean sound"],
    "periodic-table": ["periodic table"],
    "font-preview": ["font preview", "type tester"],
    "translate": ["thank you in japanese", "how do you say water in italian",
                  "translate good morning to french", "how much in french"],
    "scale-of-universe": ["scale of universe"],
    "anime": ["anime schedule", "airing anime", "upcoming anime"],
    "luggage": ["carry on size ryanair", "cabin bag emirates",
                "hand luggage british airways"],
    "stream": ["where to watch inception", "where can i stream dune in uk",
               "where to watch breaking bad"],
    "recipe": ["recipe converter"],
    "meeting-planner": ["meeting planner", "meeting planner tokyo london new york"],
    "artist": ["taylor swift discography", "ado", "drake albums",
               "joost klein"],
}


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def serve(index_path: str = "serika.db", host: str = "127.0.0.1",
          port: int = 8000) -> None:
    index = Index(index_path)

    # Share the Redis connection with the modules that cache upstream lookups,
    # so a popular weather or Wikipedia query hits the network once.
    live_tools.set_cache(index._redis)
    reference.set_cache(index._redis)
    from ..tools import anime as anime_tool, stream as stream_tool, \
        music as music_tool
    anime_tool.set_cache(index._redis)
    stream_tool.set_cache(index._redis)
    music_tool.set_cache(index._redis)

    Handler.index = index
    Handler.api_limiter = RateLimiter(limit=120, window=60)
    Handler.form_limiter = RateLimiter(limit=5, window=600)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(
        f"SerikaSearch → http://{host}:{port}\n"
        f"  {index.document_count():,} pages · {index.image_count():,} images · "
        f"{index.video_count():,} videos · {index.favicon_count():,} favicons"
    )
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
        index.close()
