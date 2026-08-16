"""HTML rendering for SerikaSearch.

Structure lives in ``html/`` templates; this module fills the ``|safe`` slots
in them. Anything repeated — a list of result cards, a table of facts, a row of
tabs — is built here in Python, because the template engine deliberately has no
loops (see :mod:`serika.web.templates`).

The rule throughout: every value that came from a crawled page, a user query,
or a third-party API goes through :func:`html.escape` before it reaches a
``|safe`` slot. The only raw HTML emitted is markup written in this file or in
:mod:`serika.web.pages`.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, urlencode, urlsplit

from ..core import bangs as bang_module
from ..core.reference import SOURCE_LABELS, SOURCES, DICTIONARY_LINKS
from ..tools import TOOLS, tool_groups
from .templates import render

__all__ = ["shell", "header", "searchbox", "result_cards", "image_cards",
           "video_cards", "knowledge_panel", "answer_card", "dictionary_card",
           "pager", "related_block", "did_you_mean_block", "doc_sections",
           "doc_toc", "tool_cards", "bang_groups", "stat_cards", "icon"]

ASSET_VERSION = "13"

WORDMARK = "serika<em>search</em>"

FAVICON_SVG = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='14' fill='%23000000'/%3E"
    "%3Ctext x='32' y='46' font-family='Helvetica,Arial,sans-serif' "
    "font-size='42' font-weight='bold' text-anchor='middle' "
    "fill='%23a274ff'%3Es%3C/text%3E%3C/svg%3E"
)


def e(value) -> str:
    """Escape for HTML text and attribute contexts."""
    return html.escape("" if value is None else str(value), quote=True)


# --------------------------------------------------------------------------- #
# icons — one place, so stroke weights stay consistent
# --------------------------------------------------------------------------- #

_ICON_PATHS = {
    "web": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="m21 16-5-5L5 20"/>',
    "video": '<rect x="2" y="5" width="14" height="14" rx="2"/><path d="m22 8-6 4 6 4z"/>',
    "news": '<path d="M4 5h13v14H4z"/><path d="M17 9h3v8a2 2 0 0 1-2 2h-1z"/><path d="M7 9h7M7 12h7M7 15h4"/>',
    "calc": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 12h.01M12 12h.01M16 12h.01M8 16h.01M12 16h.01M16 16h.01"/>',
    "convert": '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="7.5" cy="7.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/>',
    "binary": '<path d="M6 4h3v7H6zM6 13h3v7H6zM15 4h3v7h-3zM15 13h3v7h-3z"/>',
    "roman": '<path d="M4 6h6M7 6v12M4 18h6M14 6l3 12 3-12"/>',
    "currency": '<circle cx="12" cy="12" r="9"/><path d="M15 9a3 3 0 0 0-3-2c-1.7 0-3 1-3 2.3 0 3 6 1.7 6 4.7 0 1.4-1.3 2.5-3 2.5a3 3 0 0 1-3-2M12 5v14"/>',
    "weather": '<path d="M17.5 19a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 19z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "color": '<circle cx="12" cy="12" r="9"/><circle cx="9" cy="9.5" r="1.3" fill="currentColor"/><circle cx="15" cy="9.5" r="1.3" fill="currentColor"/><circle cx="8.5" cy="14.5" r="1.3" fill="currentColor"/>',
    "qr": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM19 19h2v2h-2zM14 19h2M19 14h2"/>',
    "code": '<path d="m9 8-5 4 5 4M15 8l5 4-5 4"/>',
    "hash": '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
    "key": '<circle cx="8" cy="14" r="4"/><path d="m11 11 9-9M17 5l2 2M14 8l2 2"/>',
    "id": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M5.5 17a4 4 0 0 1 7 0M15 10h4M15 14h3"/>',
    "dice": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.4" fill="currentColor"/><circle cx="16" cy="16" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/>',
    "text": '<path d="M4 6h16M4 12h16M4 18h10"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.2-4.2"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "external": '<path d="M7 17 17 7M9 7h8v8"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "cloud": '<path d="M17.5 19a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 19z"/>',
    "cloud-sun": '<path d="M8 6V4M4.5 7.5 3 6M12 10a4 4 0 0 0-8 0"/><path d="M17.5 20a3.5 3.5 0 0 0 0-7 5 5 0 0 0-9.5 1.5A3 3 0 0 0 8.5 20z"/>',
    "rain": '<path d="M17.5 16a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 16z"/><path d="M8 19l-1 2M12 19l-1 2M16 19l-1 2"/>',
    "drizzle": '<path d="M17.5 16a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 16z"/><path d="M9 19v1M13 19v1"/>',
    "snow": '<path d="M17.5 15a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 15z"/><path d="M9 19h.01M12 21h.01M15 19h.01"/>',
    "sleet": '<path d="M17.5 15a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 15z"/><path d="M9 19h.01M13 19l-1 2"/>',
    "storm": '<path d="M17.5 15a4 4 0 0 0 0-8 6 6 0 0 0-11.4 1.8A3.5 3.5 0 0 0 6.5 15z"/><path d="m12 13-2 4h3l-2 4"/>',
    "fog": '<path d="M4 10h16M6 14h12M8 18h8M5 6h14"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
    "shuffle": '<path d="M3 7h4l10 10h4M3 17h4l3-3M15 7h6M18 4l3 3-3 3M18 14l3 3-3 3"/>',
    "heart": '<path d="M12 20s-7-4.5-9.5-9A4.8 4.8 0 0 1 12 6a4.8 4.8 0 0 1 9.5 5c-2.5 4.5-9.5 9-9.5 9z"/>',
    "receipt": '<path d="M5 3v18l2-1.4L9 21l2-1.4L13 21l2-1.4L17 21l2-1.4V3l-2 1.4L15 3l-2 1.4L11 3 9 4.4 7 3z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "tiles": '<rect x="3" y="4" width="7" height="7" rx="1"/><rect x="14" y="4" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "dots": '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><path d="M9 12h5" stroke-width="2.4"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
    "metronome": '<path d="M9 3h6l3 18H6z"/><path d="M6 15h12"/><path d="m12 15 4-8"/>',
    "waves": '<path d="M2 8c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2M2 14c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2"/>',
    "atom": '<circle cx="12" cy="12" r="1.6" fill="currentColor"/><ellipse cx="12" cy="12" rx="9" ry="4"/><ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(120 12 12)"/>',
    "type": '<path d="M4 7V5h16v2M9 19h6M12 5v14"/>',
    "play": '<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
    "sunrise": '<path d="M12 3v5M5.6 10.6 4.2 9.2M18.4 10.6l1.4-1.4M2 18h20M6 18a6 6 0 0 1 12 0M8 6l4-3 4 3"/>',
    "sunset": '<path d="M12 8V3M5.6 10.6 4.2 9.2M18.4 10.6l1.4-1.4M2 18h20M6 18a6 6 0 0 1 12 0M8 5l4 3 4-3"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/>',
    "tv": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m7 3 5 4 5-4"/>',
    "luggage": '<rect x="6" y="7" width="12" height="14" rx="2"/><path d="M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3M10 11v6M14 11v6"/>',
}


def icon(name: str, size: int = 16, stroke: float = 1.9) -> str:
    """A stroked 24×24 icon, inlined so there are no icon-font requests."""
    path = _ICON_PATHS.get(name)
    if not path:
        return ""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


# --------------------------------------------------------------------------- #
# shell, header, search box
# --------------------------------------------------------------------------- #

def shell(title: str, body_html: str, description: str = "",
          head_extra: str = "") -> str:
    return render("layouts/base.html", {
        "title": title,
        "description": description or
                       "Independent search with its own crawler and index.",
        "favicon": FAVICON_SVG,
        "body": body_html,
        "head_extra": head_extra,
        "v": ASSET_VERSION,
    })


def searchbox(value: str = "", tab: str = "web", autofocus: bool = False,
              placeholder: str = "Search the web") -> str:
    hidden = ""
    if tab and tab != "web":
        hidden = f'<input type="hidden" name="tab" value="{e(tab)}"/>'
    return render("components/searchbox.html", {
        "query": value,
        "placeholder": placeholder,
        "autofocus": "autofocus" if autofocus else "",
        "hidden": hidden,
    })


_TAB_SPEC = (
    ("web", "Web", "web"),
    ("images", "Images", "image"),
    ("videos", "Videos", "video"),
)


def tabs(q: str, tab: str, counts: dict) -> str:
    out = []
    for key, label, icon_name in _TAB_SPEC:
        count = counts.get(key, 0)
        number = f'<span class="n">{count:,}</span>' if count else ""
        out.append(render("components/tab.html", {
            "active": "active" if tab == key else "",
            "href": search_url(q, key),
            "label": label,
            "key": key,
            "icon": icon(icon_name, 15),
            "count": number,
        }))
    return "".join(out)


def header(q: str, tab: str, parsed=None, counts: dict | None = None,
           show_tabs: bool = True) -> str:
    return render("components/header.html", {
        "searchbox": searchbox(q, tab),
        "op_badges": operator_badges(parsed) if parsed is not None else "",
        "tabs": tabs(q, tab, counts or {}) if show_tabs else "",
    })


def operator_badges(parsed) -> str:
    if parsed is None or not getattr(parsed, "has_operators", False):
        return ""
    badges = []
    for site in parsed.sites:
        badges.append(f'<span class="op-badge">site:{e(site)}</span>')
    for word in parsed.excludes:
        badges.append(f'<span class="op-badge op-exclude">-{e(word)}</span>')
    for word in parsed.intitle:
        badges.append(f'<span class="op-badge">intitle:{e(word)}</span>')
    for word in parsed.inurl:
        badges.append(f'<span class="op-badge">inurl:{e(word)}</span>')
    return f'<div class="op-badges">{"".join(badges)}</div>'


def search_url(q: str, tab: str = "web", page: int = 1, **extra) -> str:
    params = {"q": q}
    if tab and tab != "web":
        params["tab"] = tab
    if page > 1:
        params["page"] = str(page)
    for key, value in extra.items():
        if value:
            params[key] = value
    return "/search?" + urlencode(params)


# --------------------------------------------------------------------------- #
# result cards
# --------------------------------------------------------------------------- #

def _display_url(url: str, limit: int = 68) -> str:
    """A breadcrumb-ish path, the way search engines show it."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path:
        return ""
    segments = [s for s in path.split("/") if s]
    text = " › ".join(segments)
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text


def _safe_snippet(snippet: str) -> str:
    """Snippets arrive with literal <mark> tags around matches. Escape
    everything else so crawled markup can never reach the page."""
    open_ph, close_ph = "\x00O\x00", "\x00C\x00"
    text = snippet.replace("<mark>", open_ph).replace("</mark>", close_ph)
    text = html.escape(text)
    return text.replace(open_ph, "<mark>").replace(close_ph, "</mark>")


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pretty_date(value: str) -> str:
    match = _ISO_DATE.match(value or "")
    if not match:
        return ""
    year, month, day = match.groups()
    index = int(month) - 1
    if not 0 <= index < 12:
        return ""
    return f"{int(day)} {_MONTHS[index]} {year}"


def _rich_row(meta: dict) -> str:
    """The metadata line under a snippet: date, author, rating, price."""
    if not meta:
        return ""
    bits: list[str] = []

    date = _pretty_date(meta.get("published", ""))
    if date:
        bits.append(f'<span class="rich-item">{icon("calendar", 12)}{e(date)}</span>')

    author = (meta.get("author") or "").strip()
    if author and len(author) < 60:
        bits.append(f'<span class="rich-item">by {e(author)}</span>')

    rating = meta.get("rating")
    if isinstance(rating, (int, float)) and 0 < rating <= 5:
        filled = round(rating)
        stars = "★" * filled + "☆" * (5 - filled)
        count = meta.get("rating_count") or 0
        suffix = f" ({count:,})" if count else ""
        bits.append(
            f'<span class="rich-item rich-rating">{stars} {rating:g}{e(suffix)}</span>'
        )

    price = (meta.get("price") or "").strip()
    if price:
        currency = (meta.get("price_currency") or "").strip()
        bits.append(
            f'<span class="rich-item rich-price">{e(price)} {e(currency)}</span>'
        )

    duration = (meta.get("duration") or "").strip()
    if duration and len(duration) < 20:
        bits.append(f'<span class="rich-item">{icon("clock", 12)}{e(duration)}</span>')

    page_type = (meta.get("type") or "").strip().lower()
    if page_type and page_type not in ("website", "article", "webpage"):
        bits.append(f'<span class="rich-badge">{e(page_type)}</span>')

    if not bits:
        return ""
    joined = '<span class="rich-sep">·</span>'.join(bits)
    return f'<div class="result-rich">{joined}</div>'


def _sections_row(meta: dict, url: str) -> str:
    """Jump links built from the page's own <h2>/<h3> headings."""
    headings = [h for h in (meta.get("headings") or []) if 3 < len(h) < 60]
    if len(headings) < 3:
        return ""
    links = []
    for heading in headings[:4]:
        anchor = re.sub(r"[^\w\s-]", "", heading).strip().lower()
        anchor = re.sub(r"[\s_]+", "-", anchor)
        target = f"{url}#{anchor}" if anchor else url
        links.append(
            f'<a class="section-link" href="{e(target)}" '
            f'rel="noopener noreferrer">{e(heading)}</a>'
        )
    return f'<div class="result-sections">{"".join(links)}</div>'


def result_cards(results, metas: dict | None = None,
                 new_tab: bool = True) -> str:
    metas = metas or {}
    target = ' target="_blank" rel="noopener noreferrer"' if new_tab else ""
    out = []
    for i, r in enumerate(results, 1):
        host = r.host or urlsplit(r.url).netloc
        meta = metas.get(r.url, {})

        thumb = ""
        modifier = ""
        image = (meta.get("image") or "").strip()
        # A share image is only worth a thumbnail if it's a real picture, not a
        # 1200×630 logo card — those add noise without adding information.
        if image.startswith("http") and (meta.get("image_width") or 400) >= 200:
            alt = meta.get("image_alt") or ""
            thumb = (
                f'<div class="result-thumb"><img src="{e(image)}" alt="{e(alt)}" '
                f'loading="lazy" decoding="async" referrerpolicy="no-referrer" '
                f'data-fallback="hide-parent"/></div>'
            )
            modifier = "has-thumb"

        site_name = (meta.get("site_name") or "").strip() or host

        out.append(render("components/result.html", {
            "i": i,
            "mod": modifier,
            "host_q": quote_plus(host),
            "site_name": site_name[:60],
            "display_url": _display_url(r.url),
            "url": r.url,
            "target": target,
            "title": (r.title or r.url)[:160],
            "snippet": _safe_snippet(r.snippet),
            "rich": _rich_row(meta),
            "sections": _sections_row(meta, r.url),
            "thumb": thumb,
        }))
    return "".join(out)


def image_cards(results, start: int = 1, new_tab: bool = True) -> str:
    target = ' target="_blank" rel="noopener noreferrer"' if new_tab else ""
    out = []
    for i, r in enumerate(results, start):
        caption = r.alt or r.page_title or r.host
        ratio = round(r.width / r.height, 4) if (r.width and r.height) else 1.5
        out.append(render("components/image-card.html", {
            "i": i,
            "ratio": ratio,
            "page_url": r.page_url or r.src,
            "src": r.src,
            "target": target,
            "caption": caption[:140],
            "host": r.host,
            "host_q": quote_plus(r.host or ""),
            "width": r.width or 0,
            "height": r.height or 0,
        }))
    return "".join(out)


def video_cards(videos) -> str:
    out = []
    for i, v in enumerate(videos, 1):
        host = v.get("host", "")
        platform = v.get("platform", "")
        embed_id = v.get("embed_id", "")
        thumb = v.get("thumbnail", "")

        if platform == "youtube":
            embed_url = (f"https://www.youtube-nocookie.com/embed/{embed_id}"
                         f"?rel=0&modestbranding=1&playsinline=1")
            thumb = thumb or f"https://i.ytimg.com/vi/{embed_id}/hqdefault.jpg"
        elif platform == "vimeo":
            embed_url = f"https://player.vimeo.com/video/{embed_id}?dnt=1"
        else:
            embed_url = ""

        thumb_block = ""
        if thumb:
            thumb_block = (
                f'<img src="{e(thumb)}" alt="" loading="lazy" decoding="async" '
                f'referrerpolicy="no-referrer" data-fallback="hide"/>'
            )

        out.append(render("components/video-card.html", {
            "i": i,
            "host_q": quote_plus(host),
            "host": host,
            "url": v.get("page_url", "") or embed_url,
            "title": (v.get("page_title", "") or host)[:160],
            "embed_url": embed_url,
            "embed_id": embed_id,
            "platform": platform,
            "thumb_block": thumb_block,
        }))
    return "".join(out)


# --------------------------------------------------------------------------- #
# knowledge panel
# --------------------------------------------------------------------------- #

def knowledge_panel(card, query: str) -> str:
    image_block = ""
    if card.image:
        image_block = (
            f'<div class="kpanel-img"><img src="{e(card.image)}" alt="" '
            f'loading="lazy" decoding="async" referrerpolicy="no-referrer" '
            f'data-fallback="hide-parent"/></div>'
        )

    facts = "".join(
        f"<dt>{e(key)}</dt><dd>{e(value)}</dd>" for key, value in card.facts
    )

    gallery_block = ""
    if card.gallery:
        thumbs = "".join(
            f'<img src="{e(src)}" alt="" loading="lazy" decoding="async" '
            f'referrerpolicy="no-referrer" data-fallback="hide"/>'
            for src in card.gallery
        )
        gallery_block = f'<div class="kpanel-gallery">{thumbs}</div>'

    # Source switcher — the same subject, read from a different encyclopedia.
    tabs_html = ""
    if card.available:
        links = []
        for key in SOURCES:
            active = "active" if key == card.source_key else ""
            href = search_url(query, "web", 1, src=key)
            links.append(f'<a class="ksource {active}" href="{e(href)}">'
                         f"{e(SOURCE_LABELS.get(key, key))}</a>")
        tabs_html = f'<div class="kpanel-sources">{"".join(links)}</div>'

    return render("components/knowledge-panel.html", {
        "sources": tabs_html,
        "image_block": image_block,
        "source_name": card.source_name,
        "title": card.title,
        "summary": card.summary,
        "facts": facts,
        "gallery_block": gallery_block,
        "source_url": card.source_url,
    })


# --------------------------------------------------------------------------- #
# instant answers
# --------------------------------------------------------------------------- #

_ANSWER_KICKERS = {
    "calc": ("Calculator", "calc"),
    "convert": ("Unit conversion", "convert"),
    "currency": ("Currency", "currency"),
    "weather": ("Weather", "weather"),
    "time": ("World clock", "clock"),
    "date": ("Date calculator", "calendar"),
    "timestamp": ("Unix timestamp", "clock"),
    "base": ("Number bases", "binary"),
    "roman": ("Roman numerals", "roman"),
    "color": ("Colour", "color"),
    "text": ("Text conversion", "code"),
    "hash": ("Checksum", "hash"),
    "qr": ("QR code", "qr"),
    "coin": ("Coin flip", "dice"),
    "dice": ("Dice", "dice"),
    "random": ("Random number", "shuffle"),
    "password": ("Password generator", "key"),
    "uuid": ("UUID", "id"),
    "lorem": ("Lorem ipsum", "text"),
    "wordcount": ("Word count", "text"),
    "ip": ("Your connection", "web"),
    "define": ("Dictionary", "book"),
    "bmi": ("BMI calculator", "heart"),
    "split": ("Bill splitter", "receipt"),
    "sun": ("Sunrise & sunset", "sunrise"),
    "anagram": ("Anagram solver", "tiles"),
    "morse": ("Morse code", "dots"),
    "translate": ("Phrasebook", "globe"),
    "luggage": ("Carry-on checker", "luggage"),
    "anime": ("Anime schedule", "tv"),
    "stream": ("Where to watch", "tv"),
    "artist": ("Artist", "tv"),
    "interactive": ("Tool", "spark"),
}


def _copy_button(value: str, label: str = "Copy") -> str:
    if not value:
        return ""
    return (f'<button type="button" class="btn btn-ghost btn-sm copy-btn" '
            f'data-copy="{e(value)}">{icon("copy", 14)} {e(label)}</button>')


def _answer_rows(rows) -> str:
    if not rows:
        return ""
    cells = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows)
    return f'<dl class="answer-rows">{cells}</dl>'


def _answer_items(items) -> str:
    if not items:
        return ""
    blocks = "".join(f'<div class="answer-item">{e(item)}</div>' for item in items)
    return f'<div class="answer-items">{blocks}</div>'


def _weather_body(answer) -> str:
    data = answer.data
    days = ""
    for day in data.get("days", [])[1:6]:
        days += (
            f'<div class="weather-day">'
            f'<div class="wd-name">{e(day["weekday"])}</div>'
            f'<div class="wd-icon">{icon(day["icon"], 20)}</div>'
            f'<div class="wd-temp"><b>{day["high"]:g}°</b> '
            f'<span>{day["low"]:g}°</span></div>'
            + (f'<div class="wd-rain">{day["rain"]}%</div>' if day["rain"] else "")
            + "</div>"
        )
    days_block = f'<div class="weather-days">{days}</div>' if days else ""
    return (
        f'<div class="weather-now">'
        f'<span class="weather-icon">{icon(data.get("icon", "cloud"), 44, 1.5)}</span>'
        f'<span class="weather-temp">{e(answer.title)}</span>'
        f'<span class="answer-detail" style="margin:0">{e(answer.detail)}<br>'
        f'{e(data.get("fahrenheit", ""))}°F</span>'
        f"</div>{days_block}"
    )


def _color_body(answer) -> str:
    data = answer.data
    hex_code = data.get("hex", "#000000")
    text_color = "#ffffff" if data.get("on_dark") else "#000000"
    shades = "".join(
        f'<button type="button" class="color-shade copy-btn" '
        f'style="background:{e(shade)}" data-copy="{e(shade)}" '
        f'title="{e(shade)}" aria-label="Copy {e(shade)}"></button>'
        for shade in data.get("shades", [])
    )
    return (
        f'<div class="color-preview" style="background:{e(hex_code)};'
        f'color:{text_color}">{e(hex_code.upper())}</div>'
        + (f'<div class="color-shades">{shades}</div>' if shades else "")
    )


def _qr_body(answer) -> str:
    # The SVG is generated by our own encoder in serika.tools.qr — it contains
    # no external references and no user text, only <rect> and <path>.
    svg = answer.data.get("svg", "")
    payload = answer.data.get("payload", "")
    return (
        f'<div class="qr-wrap"><div class="qr-code">{svg}</div>'
        f'<div class="qr-meta"><div class="answer-sub">{e(payload)}</div>'
        f'<p class="answer-detail">Error-correction level M. Scan it with any '
        f'camera app, or download the SVG for print.</p></div></div>'
    )


def _bmi_body(answer) -> str:
    data = answer.data
    if data.get("empty"):
        return (f'<div class="answer-value" style="font-size:var(--fs-xl)">'
                f'{e(answer.title)}</div>'
                f'<div class="answer-detail">{e(answer.subtitle)}</div>')
    tone = data.get("tone", "good")
    position = data.get("position", 50)
    # A WHO-banded scale with a marker at the computed BMI.
    scale = (
        '<div class="bmi-scale">'
        '<div class="bmi-track">'
        '<span class="bmi-band under"></span><span class="bmi-band healthy"></span>'
        '<span class="bmi-band over"></span><span class="bmi-band obese"></span>'
        f'<span class="bmi-marker tone-{e(tone)}" style="left:{position:.1f}%"></span>'
        '</div>'
        '<div class="bmi-ticks"><span>15</span><span>18.5</span>'
        '<span>25</span><span>30</span><span>40</span></div>'
        '</div>'
    )
    return (
        f'<div class="answer-value tone-{e(tone)}">{e(answer.title)}</div>'
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>{scale}'
        f'<div class="answer-detail">{e(answer.detail)}</div>'
        + _answer_rows(answer.rows)
    )


def _sun_body(answer) -> str:
    data = answer.data
    return (
        f'<div class="sun-arc">'
        f'<div class="sun-arc-line"></div>'
        f'<span class="sun-endpoint left">{icon("sunrise", 20)}'
        f'<b>{e(data.get("sunrise", ""))}</b></span>'
        f'<span class="sun-endpoint right">{icon("sunset", 20)}'
        f'<b>{e(data.get("sunset", ""))}</b></span>'
        f'</div>'
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>'
        f'<div class="answer-detail">{e(answer.detail)}</div>'
    )


def _anagram_body(answer) -> str:
    data = answer.data
    if data.get("empty"):
        return (f'<div class="answer-value" style="font-size:var(--fs-xl)">'
                f'{e(answer.title)}</div>'
                f'<div class="answer-detail">{e(answer.subtitle)}</div>')

    # A tile rack showing the input letters.
    rack = "".join(
        f'<span class="tile">{e(c.upper())}</span>'
        for c in data.get("letters", "")
    )
    exact = ""
    if data.get("exact"):
        chips = "".join(
            f'<a class="anagram-word primary" '
            f'href="/search?q=define+{quote_plus(w)}">{e(w)}</a>'
            for w in data["exact"]
        )
        exact = (f'<div class="anagram-group"><h4>Full anagrams</h4>'
                 f'<div class="anagram-words">{chips}</div></div>')

    groups = ""
    for length, words in list(data.get("by_length", {}).items())[:6]:
        chips = "".join(
            f'<a class="anagram-word" href="/search?q=define+{quote_plus(w)}">'
            f'{e(w)}</a>' for w in words
        )
        groups += (f'<div class="anagram-group"><h4>{e(length)} letters</h4>'
                   f'<div class="anagram-words">{chips}</div></div>')

    return (
        f'<div class="answer-value" style="font-size:var(--fs-xl)">'
        f'{e(answer.title)}</div>'
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>'
        f'<div class="tile-rack">{rack}</div>{exact}{groups}'
    )


def _morse_body(answer) -> str:
    data = answer.data
    morse = data.get("morse", "")
    # A play button that app.js turns into audible beeps.
    play = (f'<button type="button" class="btn btn-ghost btn-sm morse-play" '
            f'data-morse="{e(morse)}">{icon("play", 14)} Play</button>')
    return (
        f'<div class="answer-value">{e(answer.title)}</div>'
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>'
        f'<div class="answer-detail">{e(answer.detail)}</div>'
        f'<div class="answer-actions" style="margin-top:var(--sp-3)">{play}</div>'
    )


# --------------------------------------------------------------------------- #
# interactive client widgets
#
# These carry no computed value — only markup with data-attributes that app.js
# hydrates into a live component. Because the CSP forbids inline script, the
# behaviour lives entirely in app.js and finds these by their `data-widget`
# marker.
# --------------------------------------------------------------------------- #

def _stopwatch_widget() -> str:
    return (
        '<div class="iw-tabs" role="tablist">'
        '<button class="iw-tab active" data-sw-mode="stopwatch" role="tab">'
        'Stopwatch</button>'
        '<button class="iw-tab" data-sw-mode="timer" role="tab">Timer</button>'
        '</div>'
        '<div class="sw-display" data-sw-display>00:00.<small>00</small></div>'
        '<div class="sw-timer-set" data-sw-set hidden>'
        '<input type="number" min="0" max="99" value="5" data-sw-min '
        'aria-label="Minutes"/><span>min</span>'
        '<input type="number" min="0" max="59" value="0" data-sw-sec '
        'aria-label="Seconds"/><span>sec</span></div>'
        '<div class="iw-controls">'
        '<button class="btn btn-primary" data-sw-start>' + icon("play", 15)
        + ' Start</button>'
        '<button class="btn" data-sw-lap>' + icon("clock", 15) + ' Lap</button>'
        '<button class="btn btn-ghost" data-sw-reset>' + icon("reset", 15)
        + ' Reset</button></div>'
        '<ol class="sw-laps" data-sw-laps></ol>'
    )


def _metronome_widget(bpm: str = "") -> str:
    start_bpm = bpm or "120"
    return (
        f'<div class="metro" data-metro-bpm="{e(start_bpm)}">'
        '<div class="metro-bpm"><span data-metro-display>' + e(start_bpm)
        + '</span> <small>BPM</small></div>'
        '<input type="range" min="40" max="240" value="' + e(start_bpm) + '" '
        'class="metro-slider" data-metro-slider aria-label="Tempo in BPM"/>'
        '<div class="metro-beat" data-metro-beat aria-hidden="true"></div>'
        '<div class="iw-controls">'
        '<button class="btn" data-metro-down>−5</button>'
        '<button class="btn btn-primary" data-metro-toggle>' + icon("play", 15)
        + ' Start</button>'
        '<button class="btn" data-metro-up>+5</button>'
        '<button class="btn btn-ghost" data-metro-tap>Tap tempo</button>'
        '</div>'
        '<div class="metro-meter">Beats per bar: '
        '<select data-metro-beats aria-label="Beats per bar">'
        '<option>1</option><option>2</option><option>3</option>'
        '<option selected>4</option><option>6</option></select></div>'
        '</div>'
    )


_NOISE_SOUNDS = (
    ("white", "White noise", "A flat hiss — masks speech and hum."),
    ("pink", "Pink noise", "Softer, balanced — like steady rainfall."),
    ("brown", "Brown noise", "Deep and rumbling — a waterfall or wind."),
    ("rain", "Rain", "Filtered noise shaped into falling rain."),
    ("ocean", "Ocean waves", "Slow swells that rise and fall."),
)


def _noise_widget() -> str:
    buttons = "".join(
        f'<button class="noise-btn" data-noise="{e(key)}">'
        f'<span class="noise-name">{e(name)}</span>'
        f'<span class="noise-desc">{e(desc)}</span></button>'
        for key, name, desc in _NOISE_SOUNDS
    )
    return (
        f'<div class="noise-grid">{buttons}</div>'
        '<div class="noise-controls" data-noise-controls hidden>'
        '<button class="btn btn-ghost btn-sm" data-noise-stop>' + icon("stop", 14)
        + ' Stop</button>'
        '<label class="noise-vol">Volume '
        '<input type="range" min="0" max="100" value="50" data-noise-vol '
        'aria-label="Volume"/></label></div>'
        '<p class="answer-detail">Generated live with the Web Audio API — '
        'nothing is downloaded, and it plays until you stop it. A timer keeps '
        'it from running forever if you forget.</p>'
    )


def _font_widget() -> str:
    fonts = [
        ("system-ui, sans-serif", "System UI"),
        ("Georgia, serif", "Georgia"),
        ("'Times New Roman', serif", "Times New Roman"),
        ("Arial, sans-serif", "Arial"),
        ("Helvetica, Arial, sans-serif", "Helvetica"),
        ("'Courier New', monospace", "Courier New"),
        ("Verdana, sans-serif", "Verdana"),
        ("'Trebuchet MS', sans-serif", "Trebuchet MS"),
        ("Palatino, serif", "Palatino"),
        ("Garamond, serif", "Garamond"),
        ("'Brush Script MT', cursive", "Brush Script"),
        ("Impact, sans-serif", "Impact"),
    ]
    options = "".join(f'<option value="{e(css)}">{e(name)}</option>'
                      for css, name in fonts)
    return (
        '<div class="font-tool">'
        '<textarea class="font-input" data-font-input '
        'aria-label="Preview text">The quick brown fox jumps over the lazy '
        'dog. 0123456789</textarea>'
        '<div class="font-controls">'
        f'<select data-font-family aria-label="Typeface">{options}</select>'
        '<label>Size <input type="range" min="12" max="96" value="32" '
        'data-font-size aria-label="Font size"/></label>'
        '<button class="iw-toggle" data-font-bold aria-pressed="false">Bold</button>'
        '<button class="iw-toggle" data-font-italic aria-pressed="false">'
        'Italic</button>'
        '</div>'
        '<div class="font-preview" data-font-preview '
        'style="font-family:system-ui,sans-serif;font-size:32px">'
        'The quick brown fox jumps over the lazy dog. 0123456789</div>'
        '</div>'
    )


def _periodic_widget() -> str:
    from ..tools.elements import ELEMENTS, CATEGORY_LABELS
    cells = []
    for number, symbol, name, mass, category, group, period in ELEMENTS:
        # Lanthanides/actinides (group 0) are laid out in the strip below.
        if group == 0:
            row = 9 if period == 6 else 10
            col = (number - 57) + 3 if period == 6 else (number - 89) + 3
        else:
            row, col = period, group
        cells.append(
            f'<button class="pt-cell cat-{e(category)}" '
            f'style="grid-row:{row};grid-column:{col}" '
            f'data-el="{number}|{e(symbol)}|{e(name)}|{e(mass)}|{e(category)}|'
            f'{group}|{period}" data-name="{e(name.lower())}" '
            f'data-symbol="{e(symbol.lower())}">'
            f'<span class="pt-num">{number}</span>'
            f'<span class="pt-sym">{e(symbol)}</span>'
            f'<span class="pt-name">{e(name)}</span></button>'
        )
    # Placeholder markers linking the main table to the f-block strip.
    cells.append('<span class="pt-cell placeholder" '
                 'style="grid-row:6;grid-column:3">57–71</span>')
    cells.append('<span class="pt-cell placeholder" '
                 'style="grid-row:7;grid-column:3">89–103</span>')

    legend = "".join(
        f'<span class="pt-legend-item"><i class="cat-{e(key)}"></i>{e(label)}</span>'
        for key, label in CATEGORY_LABELS.items()
    )
    return (
        '<div class="pt-search-row">'
        '<input type="search" class="pt-search" data-pt-search '
        'placeholder="Filter by name or symbol…" aria-label="Filter elements"/>'
        '</div>'
        f'<div class="pt-scroll"><div class="pt-grid">{"".join(cells)}</div></div>'
        f'<div class="pt-legend">{legend}</div>'
        '<div class="pt-detail" data-pt-detail hidden></div>'
    )


def _universe_widget() -> str:
    import json as _json
    from ..tools.universe import object_data, MIN_LOG, MAX_LOG
    # The object table is embedded as JSON and read by app.js — no request.
    payload = _json.dumps(object_data())
    return (
        f'<div class="uni-tool" data-universe '
        f'data-min="{MIN_LOG}" data-max="{MAX_LOG}" '
        f"data-objects='{html.escape(payload, quote=True)}'>"
        '<div class="uni-stage" data-uni-stage>'
        '<div class="uni-emoji" data-uni-emoji>🧍</div>'
        '<div class="uni-name" data-uni-name>Human</div>'
        '<div class="uni-size" data-uni-size>1 metre</div>'
        '<p class="uni-blurb" data-uni-blurb></p>'
        '</div>'
        '<input type="range" class="uni-slider" data-uni-slider '
        f'min="{MIN_LOG}" max="{MAX_LOG}" value="0" step="0.01" '
        'aria-label="Zoom level"/>'
        '<div class="uni-scale-labels">'
        '<span>quantum</span><span>atomic</span><span>human</span>'
        '<span>planetary</span><span>cosmic</span></div>'
        '<div class="uni-neighbours" data-uni-neighbours></div>'
        '</div>'
    )


def _luggage_widget() -> str:
    from ..tools.luggage import AIRLINES
    seen: dict[str, object] = {}
    for airline in AIRLINES.values():
        seen.setdefault(airline.key, airline)
    options = "".join(
        f'<option value="{e(a.key)}" '
        f'data-dims="{a.length} × {a.width} × {a.depth} cm" '
        f'data-in="{a.length/2.54:.0f} × {a.width/2.54:.0f} × {a.depth/2.54:.0f} in" '
        f'data-weight="{("%g kg" % a.weight) if a.weight else "No strict limit"}" '
        f'data-personal="{e(a.personal)}">{e(a.name)}</option>'
        for a in sorted(seen.values(), key=lambda x: x.name)
    )
    return (
        '<div class="lug-tool" data-luggage>'
        f'<select class="lug-select" data-lug-select aria-label="Airline">'
        f'<option value="">Choose an airline…</option>{options}</select>'
        '<div class="lug-result" data-lug-result hidden></div>'
        '<p class="answer-detail">Published cabin-bag allowances, a snapshot '
        'from early 2026 — always confirm with the airline before you fly, '
        'because a bag one centimetre over is their call.</p>'
        '</div>'
    )


def _recipe_widget() -> str:
    import json as _json
    from ..tools.recipe import SUBSTITUTIONS
    diets = "".join(
        f'<button type="button" class="iw-toggle" data-diet="{e(key)}" '
        f'aria-pressed="false">{e(key.replace("-", " ").title())}</button>'
        for key in SUBSTITUTIONS
    )
    subs = html.escape(_json.dumps(SUBSTITUTIONS), quote=True)
    return (
        f"<div class=\"recipe-tool\" data-recipe data-subs='{subs}'>"
        '<textarea class="recipe-input" data-recipe-input rows="6" '
        'aria-label="Ingredients, one per line" '
        'placeholder="2 cups flour&#10;1 1/2 tsp baking powder&#10;'
        '3 eggs&#10;1 cup milk&#10;100 g butter">2 cups flour\n'
        '1 1/2 tsp baking powder\n3 eggs\n1 cup milk\n100 g butter</textarea>'
        '<div class="recipe-controls">'
        '<div class="recipe-scale">'
        '<button class="btn btn-sm" data-recipe-preset="0.5">Halve</button>'
        '<button class="btn btn-sm" data-recipe-preset="2">Double</button>'
        '<button class="btn btn-sm" data-recipe-preset="3">Triple</button>'
        '<label>Factor <input type="number" data-recipe-factor value="1" '
        'min="0.1" max="20" step="0.25" aria-label="Scale factor"/></label>'
        '</div>'
        '<div class="recipe-yield">'
        '<label>Or, servings <input type="number" data-recipe-from value="4" '
        'min="1" max="99" aria-label="Original servings"/> → '
        '<input type="number" data-recipe-to value="6" min="1" max="99" '
        'aria-label="New servings"/></label></div>'
        f'<div class="recipe-diets">{diets}</div>'
        '</div>'
        '<div class="recipe-output" data-recipe-output></div>'
        '</div>'
    )


def _meeting_widget(zones: list[str]) -> str:
    import json as _json
    from ..tools.timely import meeting_columns, CITY_ZONES
    if not zones:
        zones = ["America/Los_Angeles", "America/New_York", "Europe/London",
                 "Asia/Tokyo"]
    columns = meeting_columns(zones)
    # A datalist of city names so people can add zones by typing a city.
    cities = sorted({c.title() for c in CITY_ZONES if len(c) > 2})
    datalist = "".join(f'<option value="{e(c)}"></option>' for c in cities)
    payload = html.escape(_json.dumps(columns), quote=True)
    return (
        f"<div class=\"meet-tool\" data-meeting data-columns='{payload}'>"
        '<div class="meet-add">'
        '<input type="text" list="meet-cities" data-meet-input '
        'placeholder="Add a city…" aria-label="Add a city"/>'
        f'<datalist id="meet-cities">{datalist}</datalist>'
        '<button class="btn btn-sm" data-meet-add>Add</button></div>'
        '<div class="meet-grid" data-meet-grid></div>'
        '<p class="answer-detail">Green hours are daytime (about 8:00–20:00) '
        'everywhere at once — the window where a call suits everyone. Times '
        'follow each zone&rsquo;s current daylight-saving offset.</p>'
        '</div>'
    )


def _color_picker_widget() -> str:
    return (
        '<div class="cp-tool" data-cp>'
        '<div class="cp-preview" data-cp-preview>'
        '<input type="color" value="#a274ff" data-cp-input '
        'aria-label="Pick a colour"/></div>'
        '<div class="cp-shades" data-cp-shades></div>'
        '<dl class="answer-rows" data-cp-rows></dl>'
        '<p class="answer-detail">Drag the swatch, or type a hex value into the '
        'search box (e.g. <code>#a274ff</code>) for the same conversions inline.'
        '</p></div>'
    )


_WIDGET_BUILDERS = {
    "stopwatch": lambda bpm: _stopwatch_widget(),
    "metronome": lambda bpm: _metronome_widget(bpm),
    "noise": lambda bpm: _noise_widget(),
    "font-preview": lambda bpm: _font_widget(),
    "periodic-table": lambda bpm: _periodic_widget(),
    "color-picker": lambda bpm: _color_picker_widget(),
    "universe": lambda bpm: _universe_widget(),
    "luggage": lambda bpm: _luggage_widget(),
    "recipe": lambda bpm: _recipe_widget(),
    "meeting": lambda bpm: _meeting_widget([]),
}

_WIDGET_META = {
    "stopwatch": ("Stopwatch & timer", "clock"),
    "metronome": ("Metronome", "metronome"),
    "noise": ("Ambient sounds", "waves"),
    "font-preview": ("Font previewer", "type"),
    "periodic-table": ("Periodic table", "atom"),
    "color-picker": ("Colour picker", "color"),
    "universe": ("Scale of the Universe", "atom"),
    "luggage": ("Carry-on checker", "luggage"),
    "recipe": ("Recipe converter", "receipt"),
    "meeting": ("Meeting planner", "globe"),
}


def interactive_widget(widget: str, bpm: str = "", blurb: str = "",
                       data: dict | None = None) -> str:
    builder = _WIDGET_BUILDERS.get(widget)
    if builder is None:
        return ""
    label, icon_name = _WIDGET_META.get(widget, ("Tool", "spark"))
    # The meeting planner needs its zone list; other builders ignore the extra.
    if widget == "meeting":
        inner = _meeting_widget((data or {}).get("zones") or [])
    else:
        inner = builder(bpm)
    mod = "answer-wide" if widget in ("periodic-table", "meeting") else ""
    return (
        f'<section class="answer kind-interactive {mod}" '
        f'data-widget="{e(widget)}" aria-label="{e(label)}">'
        f'<div class="answer-kicker">{icon(icon_name, 14)} {e(label)}</div>'
        + (f'<p class="answer-detail" style="margin-top:0;margin-bottom:'
           f'var(--sp-4)">{e(blurb)}</p>' if blurb else "")
        + inner + "</section>"
    )


def _translate_body(answer) -> str:
    per_word = ""
    if answer.data.get("literal") and answer.rows:
        chips = "".join(
            f'<span class="tr-word"><b>{e(src)}</b> → {e(dst)}</span>'
            for src, dst in answer.rows
        )
        per_word = f'<div class="tr-words">{chips}</div>'
    return (
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>'
        f'<div class="answer-value tr-result">{e(answer.title)}</div>'
        f'{per_word}'
        f'<div class="answer-detail">{e(answer.detail)}</div>'
    )


def _anime_body(answer) -> str:
    import time
    episodes = answer.data.get("episodes", [])
    rows = []
    for ep in episodes:
        when = ep.get("airing_at", 0)
        try:
            label = time.strftime("%a %H:%M", time.localtime(when))
        except (ValueError, OSError):
            label = ""
        cover = ""
        if ep.get("cover"):
            cover = (f'<img class="anime-cover" src="{e(ep["cover"])}" alt="" '
                     f'loading="lazy" decoding="async" '
                     f'referrerpolicy="no-referrer" data-fallback="hide"/>')
        score = (f'<span class="anime-score">★ {ep["score"]}</span>'
                 if ep.get("score") else "")
        title = ep.get("title") or ep.get("romaji") or ""
        url = ep.get("url") or "#"
        rows.append(
            f'<a class="anime-row" href="{e(url)}" target="_blank" '
            f'rel="noopener noreferrer">{cover}'
            f'<span class="anime-info"><span class="anime-title">{e(title)}</span>'
            f'<span class="anime-ep">Episode {ep.get("episode", 0)} · '
            f'{e(ep.get("format", ""))}</span></span>'
            f'<span class="anime-when">{e(label)}{score}</span></a>'
        )
    return f'<div class="anime-list">{"".join(rows)}</div>'


_STREAM_ICONS = {"Stream": "tv", "Rent": "receipt", "Buy": "receipt",
                 "Free": "play", "Free with ads": "play"}


def _stream_body(answer) -> str:
    data = answer.data
    blocks = []
    for group in data.get("groups", []):
        chips = "".join(
            (f'<a class="stream-provider" href="{e(o["url"])}" target="_blank" '
             f'rel="noopener noreferrer">{e(o["provider"])}</a>' if o.get("url")
             else f'<span class="stream-provider">{e(o["provider"])}</span>')
            for o in group.get("offers", [])
        )
        blocks.append(
            f'<div class="stream-group"><h4>{e(group["label"])}</h4>'
            f'<div class="stream-providers">{chips}</div></div>'
        )
    network = ""
    if data.get("network"):
        site = data.get("official_site")
        net = (f'<a href="{e(site)}" target="_blank" rel="noopener noreferrer">'
               f'{e(data["network"])}</a>' if site else e(data["network"]))
        network = (f'<div class="stream-group"><h4>Airs on</h4>'
                   f'<div class="stream-providers">{net}</div></div>')
    detail = (f'<div class="answer-detail">{e(answer.detail)}</div>'
              if answer.detail else "")
    if not blocks and not network:
        blocks.append('<p class="answer-detail">No streaming options found for '
                      'your region.</p>')
    return (
        f'<div class="answer-sub" style="font-family:var(--font)">'
        f'{e(answer.subtitle)}</div>'
        f'<div class="answer-value" style="font-size:var(--fs-xl)">'
        f'{e(answer.title)}</div>{detail}'
        f'<div class="stream-groups">{"".join(blocks)}{network}</div>'
    )


def artist_card(card) -> str:
    """A MusicBrainz artist card: discography, genres, and links."""
    if card is None:
        return ""
    facts = []
    descriptor = card.life_summary
    if card.disambiguation:
        descriptor += f" · {card.disambiguation}"
    years = ""
    if card.begin:
        years = card.begin[:4]
        if card.ended and card.end:
            years += f" – {card.end[:4]}"
        elif card.kind == "Group" and not card.ended:
            years += " – present"

    genres = ""
    if card.genres:
        chips = "".join(
            f'<a class="genre-chip" href="/search?q={quote_plus(g)}">{e(g)}</a>'
            for g in card.genres
        )
        genres = f'<div class="artist-genres">{chips}</div>'

    albums = ""
    if card.albums:
        items = "".join(
            f'<a class="album" href="/search?q={quote_plus(a["title"] + " album")}">'
            f'<span class="album-title">{e(a["title"])}</span>'
            f'<span class="album-year">{e(a["year"])}</span></a>'
            for a in card.albums
        )
        albums = (f'<div class="artist-section"><h4>Discography</h4>'
                  f'<div class="album-grid">{items}</div></div>')

    links = ""
    if card.links:
        items = "".join(
            f'<a class="artist-link" href="{e(url)}" target="_blank" '
            f'rel="noopener noreferrer">{e(label)}'
            f'{icon("external", 12)}</a>'
            for label, url in card.links
        )
        links = (f'<div class="artist-section"><h4>Listen &amp; tickets</h4>'
                 f'<div class="artist-links">{items}</div></div>')

    meta_bits = [b for b in (descriptor, years) if b]
    return (
        f'<section class="answer kind-artist" aria-label="Artist">'
        f'<div class="answer-kicker">{icon("tv", 14)} Artist</div>'
        f'<h2 class="artist-name">{e(card.name)}</h2>'
        f'<p class="artist-meta">{e(" · ".join(meta_bits))}</p>'
        f'{genres}{albums}{links}'
        f'<div class="answer-actions"><span class="answer-source">'
        f'Source: <a href="https://musicbrainz.org/artist/{e(card.mbid)}" '
        f'target="_blank" rel="noopener noreferrer">MusicBrainz</a></span></div>'
        f'</section>'
    )


def answer_card(answer, tool_link: bool = True) -> str:
    """Render an instant answer. Structure varies by kind; the shell is shared."""
    if answer is None:
        return ""

    # Interactive widgets are rendered by their own function, not the shell.
    if answer.kind == "interactive":
        return interactive_widget(answer.data.get("widget", ""),
                                  answer.data.get("bpm", ""),
                                  answer.subtitle, answer.data)

    label, icon_name = _ANSWER_KICKERS.get(answer.kind, ("Answer", "spark"))

    if answer.kind == "weather":
        body = _weather_body(answer)
    elif answer.kind == "color":
        body = _color_body(answer) + _answer_rows(answer.rows)
    elif answer.kind == "qr":
        body = _qr_body(answer)
    elif answer.kind == "bmi":
        body = _bmi_body(answer)
    elif answer.kind == "sun":
        body = _sun_body(answer)
    elif answer.kind == "anagram":
        body = _anagram_body(answer)
    elif answer.kind == "morse":
        body = _morse_body(answer)
    elif answer.kind == "translate":
        body = _translate_body(answer)
    elif answer.kind == "anime":
        body = _anime_body(answer)
    elif answer.kind == "stream":
        body = _stream_body(answer)
    else:
        sub = (f'<div class="answer-sub">{e(answer.subtitle)}</div>'
               if answer.subtitle else "")
        value = f'<div class="answer-value">{e(answer.title)}</div>'
        detail = (f'<div class="answer-detail">{e(answer.detail)}</div>'
                  if answer.detail else "")
        body = sub + value + detail + _answer_rows(answer.rows) \
            + _answer_items(answer.items)

    actions = [_copy_button(answer.copy_value)]
    if answer.kind in ("password", "uuid", "dice", "coin", "random", "lorem"):
        actions.append('<button type="button" class="btn btn-ghost btn-sm" '
                       'data-reload>' + icon("shuffle", 14) + " Again</button>")
    if tool_link and answer.tool:
        actions.append(f'<a class="btn btn-ghost btn-sm" href="/tools/{e(answer.tool)}">'
                       f"Open tool</a>")
    if answer.source:
        source = (f'<a href="{e(answer.source_url)}" target="_blank" '
                  f'rel="noopener noreferrer">{e(answer.source)}</a>'
                  if answer.source_url else e(answer.source))
        actions.append(f'<span class="answer-source">Source: {source}</span>')

    actions_html = ""
    if any(actions):
        actions_html = f'<div class="answer-actions">{"".join(actions)}</div>'

    return (
        f'<section class="answer kind-{e(answer.kind)}" aria-label="{e(label)}">'
        f'<div class="answer-kicker">{icon(icon_name, 14)} {e(label)}</div>'
        f"{body}{actions_html}</section>"
    )


def dictionary_card(entry) -> str:
    """A full dictionary entry — closer to a panel than a one-line answer."""
    if entry is None:
        return ""
    audio = ""
    if entry.audio:
        audio = (f'<button type="button" class="define-audio" '
                 f'data-audio="{e(entry.audio)}" aria-label="Play pronunciation">'
                 f'<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">'
                 f'<path d="M8 5v14l11-7z"/></svg></button>')

    senses = []
    for sense in entry.senses:
        definitions = ""
        for definition, example in sense.definitions:
            example_html = (f'<span class="sense-example">“{e(example)}”</span>'
                            if example else "")
            definitions += f"<li>{e(definition)}{example_html}</li>"
        extra = ""
        if sense.synonyms:
            links = ", ".join(
                f'<a href="/search?q=define+{quote_plus(word)}">{e(word)}</a>'
                for word in sense.synonyms[:6]
            )
            extra += f'<div class="sense-syn">Synonyms: {links}</div>'
        if sense.antonyms:
            links = ", ".join(
                f'<a href="/search?q=define+{quote_plus(word)}">{e(word)}</a>'
                for word in sense.antonyms[:4]
            )
            extra += f'<div class="sense-syn">Antonyms: {links}</div>'
        senses.append(
            f'<div class="sense"><div class="sense-pos">{e(sense.part_of_speech)}'
            f"</div><ol>{definitions}</ol>{extra}</div>"
        )

    origin = (f'<p class="answer-detail">Origin: {e(entry.origin)}</p>'
              if entry.origin else "")

    elsewhere = " · ".join(
        f'<a href="{e(url.format(word=quote_plus(entry.word)))}" target="_blank" '
        f'rel="noopener noreferrer">{e(name)}</a>'
        for name, url in DICTIONARY_LINKS
    )

    return (
        f'<section class="answer kind-define" aria-label="Dictionary">'
        f'<div class="answer-kicker">{icon("book", 14)} Dictionary</div>'
        f'<div class="define-head">'
        f'<span class="define-word">{e(entry.word)}</span>'
        f'<span class="define-phonetic">{e(entry.phonetic)}</span>{audio}'
        f"</div>{''.join(senses)}{origin}"
        f'<div class="answer-actions">'
        f'<span class="answer-source" style="margin-left:0">Also in: {elsewhere}'
        f"</span></div></section>"
    )


# --------------------------------------------------------------------------- #
# supporting blocks
# --------------------------------------------------------------------------- #

def pager(q: str, tab: str, page: int, last: int, **extra) -> str:
    if last <= 1:
        return ""
    return render("components/pager.html", {
        "prev_disabled": "disabled" if page <= 1 else "",
        "prev_href": search_url(q, tab, max(1, page - 1), **extra),
        "page": f"{page:,}",
        "last": f"{last:,}",
        "next_disabled": "disabled" if page >= last else "",
        "next_href": search_url(q, tab, min(last, page + 1), **extra),
    })


def freshness_filters(q: str, tab: str, active: str) -> str:
    options = (("", "Any time"), ("day", "Past day"), ("week", "Past week"),
               ("month", "Past month"), ("year", "Past year"))
    parts = ['<span class="filter-label" title="Filters by when the crawler '
             'last fetched a page">Indexed</span>']
    for key, label in options:
        css = "filter-opt active" if key == active else "filter-opt"
        href = search_url(q, tab, 1, when=key)
        parts.append(f'<a class="{css}" href="{e(href)}">{e(label)}</a>')
    return f'<div class="filter-bar">{"".join(parts)}</div>'


def image_filters(q: str, active: str, page: int = 1) -> str:
    options = (("", "All"), ("large", "Large"), ("medium", "Medium"),
               ("wide", "Wide"), ("tall", "Tall"))
    parts = ['<span class="filter-label">Size</span>']
    for key, label in options:
        css = "filter-opt active" if key == active else "filter-opt"
        href = search_url(q, "images", page, size=key)
        parts.append(f'<a class="{css}" href="{e(href)}">{e(label)}</a>')
    return "".join(parts)


def related_block(items: list[str]) -> str:
    if not items:
        return ""
    cards = "".join(
        f'<a class="related-item" style="--i:{i}" '
        f'href="/search?q={quote_plus(item)}">{icon("search", 14)}'
        f"<span>{e(item)}</span></a>"
        for i, item in enumerate(items)
    )
    return (f'<section class="related"><h2>Related searches</h2>'
            f'<div class="related-grid">{cards}</div></section>')


def did_you_mean_block(suggestion: str, original: str) -> str:
    if not suggestion:
        return ""
    return (
        f'<p class="did-you-mean">Did you mean '
        f'<a href="/search?q={quote_plus(suggestion)}">{e(suggestion)}</a>? '
        f'<span style="color:var(--text-4);font-size:var(--fs-sm)">'
        f'Showing results for {e(original)}.</span></p>'
    )


def hint_chips(items: list[tuple[str, str]]) -> str:
    """The example queries under the home-page search box."""
    return "".join(
        f'<a class="chip" href="/search?q={quote_plus(query)}">{e(label)}</a>'
        for label, query in items
    )


def home_tool_strip(limit: int = 12) -> str:
    return "".join(
        f'<a class="tool-chip" href="/tools/{e(tool.slug)}">'
        f"{icon(tool.icon, 17)}<span>{e(tool.name)}</span></a>"
        for tool in TOOLS[:limit]
    )


# --------------------------------------------------------------------------- #
# content pages
# --------------------------------------------------------------------------- #

def doc_toc(page) -> str:
    return "".join(
        f'<li><a href="#{e(section.id)}">{e(section.heading)}</a></li>'
        for section in page.sections
    )


def doc_sections(page) -> str:
    # Section bodies are authored in serika/web/pages.py — trusted HTML.
    return "".join(
        f'<section class="doc-section" id="{e(section.id)}">'
        f"<h2>{e(section.heading)}</h2>{section.body}</section>"
        for section in page.sections
    )


def tool_cards(tools, start: int = 0) -> str:
    return "".join(
        f'<a class="tool-card" style="--i:{i}" href="/tools/{e(tool.slug)}">'
        f'<div class="tool-card-head">{icon(tool.icon, 18)}'
        f"<h3>{e(tool.name)}</h3></div>"
        f"<p>{e(tool.blurb)}</p>"
        f'<div class="tool-example">{e(tool.example)}</div></a>'
        for i, tool in enumerate(tools, start)
    )


def tool_group_blocks() -> str:
    out = []
    for group, tools in tool_groups():
        out.append(f'<section class="tool-group"><h2>{e(group)}</h2>'
                   f'<div class="tool-grid">{tool_cards(tools)}</div></section>')
    return "".join(out)


def bang_groups() -> str:
    out = []
    for group, items in bang_module.bang_list():
        rows = []
        for bang in items:
            alternates = ", ".join(f"!{k}" for k in bang.keys[1:])
            rows.append(
                f'<div class="bang-item"><span class="bang-key">!{e(bang.keys[0])}'
                f'</span><span class="bang-name">{e(bang.name)}</span>'
                + (f'<span class="bang-alt">{e(alternates)}</span>'
                   if alternates else "")
                + "</div>"
            )
        out.append(f'<section class="bang-group tool-group"><h2>{e(group)}</h2>'
                   f'<div class="bang-grid">{"".join(rows)}</div></section>')
    return "".join(out)


def stat_cards(stats: list[tuple[str, str, str]]) -> str:
    return "".join(
        f'<div class="tool-card" style="--i:{i}">'
        f'<div class="tool-card-head">{icon(icon_name, 18)}<h3>{e(label)}</h3></div>'
        f'<div style="font-size:var(--fs-2xl);font-weight:680;letter-spacing:-0.03em;'
        f'font-variant-numeric:tabular-nums">{e(value)}</div></div>'
        for i, (label, value, icon_name) in enumerate(stats)
    )


def host_rows(hosts) -> str:
    return "".join(
        f'<a class="bang-item" href="/search?q=site%3A{quote_plus(host)}">'
        f'<img class="favicon" src="/icon?h={quote_plus(host)}" alt="" '
        f'width="18" height="18" loading="lazy"/>'
        f'<span class="bang-name">{e(host)}</span>'
        f'<span class="bang-alt">{count:,}</span></a>'
        for host, count in hosts
    )
