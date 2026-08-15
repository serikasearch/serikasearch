"""Lightweight HTML parsing for serikacrawler.

Extracts title, meta, body text, links, favicon and images from a page using
only the standard library's HTMLParser.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag

# Tags whose text content we never want in the index.
_SKIP_CONTENT = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "table", "ul", "ol",
}

# Junk we never want in image search: tracking pixels, spacers, sprites,
# UI chrome buttons. We DON'T block "logo" or "icon" here — users may
# legitimately search for a site's logo. Instead, logos/icons are ranked
# lower in image search (see db.py search_images).
_IMAGE_JUNK = re.compile(
    r"(1x1|pixel|spacer|blank\.|transparent\.|tracking|beacon|analytics|"
    r"sprite|placeholder|loading\.|spinner|avatar-default|"
    r"badge|button|btn|chip|arrow|chevron|"
    r"search-icon|menu|hamburger|"
    r"newsletter|signup|subscribe|cookie|gdpr|optad|ad-|ads/)",
    re.I,
)
# Pattern to detect logos/icons for ranking purposes (not filtering).
_IMAGE_IS_LOGO = re.compile(r"(logo|icon|favicon|badge|emblem|brand-mark)", re.I)
_MIN_IMAGE_DIM = 50  # px — only filter truly tiny (tracking pixels, spacers)
_MIN_IMAGE_AREA = 2500  # 50² — anything smaller is definitely not content


@dataclass
class Image:
    src: str
    alt: str = ""
    title: str = ""
    width: int = 0
    height: int = 0
    is_logo: bool = False


@dataclass
class VideoEmbed:
    url: str        # page URL containing the video
    embed_id: str   # video ID (e.g. YouTube video ID)
    platform: str   # "youtube", "vimeo", etc.
    title: str = ""
    thumbnail: str = ""


@dataclass
class ParsedPage:
    title: str = ""
    description: str = ""
    lang: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    videos: list[VideoEmbed] = field(default_factory=list)
    favicon: str = ""
    noindex: bool = False
    nofollow: bool = False


def _int_attr(value: str | None) -> int:
    if not value:
        return 0
    m = re.match(r"\s*(\d+)", value)
    return int(m.group(1)) if m else 0


class _Extractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.page = ParsedPage()
        self._skip_depth = 0
        self._in_title = False
        self._chunks: list[str] = []

    # ----- tags ------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag in _SKIP_CONTENT:
            self._skip_depth += 1
            return
        if tag == "title":
            # Some pages have multiple <title> tags; keep only the first.
            if not self.page.title:
                self._in_title = True
        elif tag == "html":
            self.page.lang = (attrs_d.get("lang") or "").strip()[:8]
        elif tag == "meta":
            self._handle_meta(attrs_d)
        elif tag == "link":
            self._handle_link_tag(attrs_d)
        elif tag == "a":
            href = attrs_d.get("href")
            rel = (attrs_d.get("rel") or "").lower()
            if href and "nofollow" not in rel:
                self._add_link(href)
        elif tag in ("img", "source"):
            self._add_image(attrs_d)
        elif tag == "iframe":
            self._add_video(attrs_d)
        elif tag == "video":
            self._add_video_tag(attrs_d)
        elif tag in _BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data):
        # <title> lives inside <head>, which is otherwise skipped — capture it
        # before the skip check so page titles aren't lost.
        if self._in_title:
            self.page.title += data
            return
        if self._skip_depth > 0:
            return
        self._chunks.append(data)

    # ----- helpers ---------------------------------------------------------

    def _handle_meta(self, attrs_d: dict):
        name = (attrs_d.get("name") or "").lower()
        prop = (attrs_d.get("property") or "").lower()
        content = attrs_d.get("content") or ""
        if name == "description" and not self.page.description:
            self.page.description = content.strip()
        elif prop == "og:description" and not self.page.description:
            self.page.description = content.strip()
        elif prop == "og:image" and content:
            # The share image is usually the single most representative one.
            self._push_image(content, alt=self.page.title, width=0, height=0)
        elif name == "robots":
            directives = content.lower()
            if "noindex" in directives:
                self.page.noindex = True
            if "nofollow" in directives:
                self.page.nofollow = True

    def _handle_link_tag(self, attrs_d: dict):
        rel = (attrs_d.get("rel") or "").lower()
        href = attrs_d.get("href")
        if not href:
            return
        if "icon" in rel:  # icon, shortcut icon, apple-touch-icon
            resolved = self._resolve(href)
            if resolved and not self.page.favicon:
                self.page.favicon = resolved

    def _add_link(self, href: str):
        absolute = self._resolve(href)
        if absolute:
            self.page.links.append(absolute)
            # Detect YouTube watch URLs as video links.
            self._detect_youtube_url(absolute)

    def _add_video(self, attrs_d: dict):
        """Detect video embeds from <iframe> tags."""
        src = attrs_d.get("src") or ""
        if not src:
            return
        self._detect_embed(src)

    def _add_video_tag(self, attrs_d: dict):
        """Detect <video> tags with a source."""
        src = attrs_d.get("src") or ""
        poster = attrs_d.get("poster") or ""
        if src:
            self._detect_embed(src)
        elif poster:
            # At least we know there's a video with a poster image.
            pass

    def _detect_youtube_url(self, url: str):
        """Detect YouTube watch URLs and register them as video embeds."""
        m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", url)
        if m:
            vid = m.group(1)
            # Avoid duplicates.
            if not any(v.embed_id == vid for v in self.page.videos):
                self.page.videos.append(VideoEmbed(
                    url=url,
                    embed_id=vid,
                    platform="youtube",
                    thumbnail=f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
                ))

    def _detect_embed(self, src: str):
        """Detect video platform embeds from iframe src URLs."""
        # YouTube embed: https://www.youtube.com/embed/VIDEO_ID
        m = re.search(r"youtube\.com/embed/([\w-]{11})", src)
        if m:
            vid = m.group(1)
            if not any(v.embed_id == vid for v in self.page.videos):
                self.page.videos.append(VideoEmbed(
                    url=self.base_url,
                    embed_id=vid,
                    platform="youtube",
                    thumbnail=f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
                ))
            return
        # Vimeo embed: https://player.vimeo.com/video/VIDEO_ID
        m = re.search(r"player\.vimeo\.com/video/(\d+)", src)
        if m:
            vid = m.group(1)
            if not any(v.embed_id == vid for v in self.page.videos):
                self.page.videos.append(VideoEmbed(
                    url=self.base_url,
                    embed_id=vid,
                    platform="vimeo",
                ))
            return

    def _add_image(self, attrs_d: dict):
        # Lazy-loaded images: the real src is in data-* attributes, while
        # src is often a 1x1 placeholder or a data: URI. Prefer data-src and
        # friends when src looks like a placeholder.
        raw_src = attrs_d.get("src") or ""
        lazy_src = (
            attrs_d.get("data-src")
            or attrs_d.get("data-lazy-src")
            or attrs_d.get("data-original")
            or attrs_d.get("data-lazy")
            or attrs_d.get("data-img")
            or ""
        )
        # data-srcset: pick the first URL from the set.
        if not lazy_src:
            dss = attrs_d.get("data-srcset") or ""
            if dss:
                lazy_src = dss.split(",")[0].strip().split(" ")[0]

        # If src is a data: URI or empty, definitely use the lazy attribute.
        # If both exist and src looks like a tiny placeholder, prefer lazy.
        src = ""
        if raw_src and not raw_src.startswith("data:"):
            src = raw_src
        if not src and lazy_src:
            src = lazy_src
        elif src and lazy_src and raw_src.startswith("data:"):
            src = lazy_src

        # srcset fallback: pick the first (usually smallest) URL.
        if not src:
            srcset = attrs_d.get("srcset") or ""
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        if not src:
            return

        # Also try data-srcset if we're using a data: src
        if src.startswith("data:") and lazy_src and not lazy_src.startswith("data:"):
            src = lazy_src

        self._push_image(
            src,
            alt=(attrs_d.get("alt") or "").strip(),
            title=(attrs_d.get("title") or "").strip(),
            width=_int_attr(attrs_d.get("width"))
            or _int_attr(attrs_d.get("data-width")),
            height=_int_attr(attrs_d.get("height"))
            or _int_attr(attrs_d.get("data-height")),
        )

    def _push_image(self, src: str, alt: str = "", title: str = "",
                    width: int = 0, height: int = 0):
        resolved = self._resolve(src)
        if not resolved:
            return
        # Drop data: URIs (base64 placeholders).
        if resolved.startswith("data:"):
            return
        # Drop junk URLs (tracking, UI chrome buttons, spacers).
        if _IMAGE_JUNK.search(resolved):
            return
        # Drop things that declare themselves truly tiny (tracking pixels).
        if width and height:
            if width < _MIN_IMAGE_DIM or height < _MIN_IMAGE_DIM:
                return
            if width * height < _MIN_IMAGE_AREA:
                return
        # Detect logos/icons for ranking — don't filter them out.
        is_logo = bool(_IMAGE_IS_LOGO.search(resolved) or
                       (width and height and width == height and width < 200))
        self.page.images.append(
            Image(src=resolved, alt=alt, title=title, width=width,
                  height=height, is_logo=is_logo)
        )

    def _resolve(self, href: str) -> str:
        href = (href or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return ""
        absolute = urljoin(self.base_url, href)
        absolute, _ = urldefrag(absolute)
        return absolute if absolute.startswith(("http://", "https://")) else ""

    # ----- finish ----------------------------------------------------------

    def finalize(self) -> ParsedPage:
        text = "".join(self._chunks)
        text = re.sub(r"\s+", " ", text).strip()
        self.page.text = text
        self.page.title = re.sub(r"\s+", " ", self.page.title).strip()
        if not self.page.description:
            self.page.description = text[:300]

        self.page.links = _dedupe(self.page.links)

        # De-dupe images by src, preferring the entry that carries alt text.
        best: dict[str, Image] = {}
        for img in self.page.images:
            existing = best.get(img.src)
            if existing is None or (not existing.alt and img.alt):
                best[img.src] = img
        # Images with no descriptive text at all are useless to search.
        self.page.images = [
            i for i in best.values() if i.alt or i.title
        ]
        return self.page


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_html(html: str, base_url: str) -> ParsedPage:
    extractor = _Extractor(base_url)
    try:
        extractor.feed(html)
    except Exception:
        pass
    return extractor.finalize()
