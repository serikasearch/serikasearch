"""serikacrawler — a polite, robots-respecting web crawler.

Design goals:
  * Respect robots.txt for every host (see :mod:`serika.robots`).
  * Identify itself honestly via a descriptive User-Agent.
  * Stay gentle: per-host crawl-delay, per-host page caps, timeouts.
  * Go wide, not hard: many hosts are crawled in parallel by a worker pool,
    while each individual host is still visited one request at a time.
  * Be resumable: the crawl frontier lives in Redis.
  * Be fast: batch DB writes, connection keep-alive, minimal lock contention.
"""

from __future__ import annotations

import gzip
import http.client
import io
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from urllib.parse import urlsplit

from .db import Index
from .parser import parse_html
from .robots import RobotsCache, USER_AGENT

# A crawler should say who it is and how to find out more.
FULL_USER_AGENT = (
    f"{USER_AGENT}/1.1 (+https://github.com/serikasearch/serikacrawler; "
    f"respects robots.txt)"
)

MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_FAVICON_BYTES = 200 * 1024
ALLOWED_CONTENT = ("text/html", "application/xhtml")
FAVICON_TYPES = ("image/", "application/octet-stream")

# Link targets that are obviously not HTML pages — skip them so the frontier
# doesn't waste budget on downloads we'd just throw away.
_SKIP_EXT = (
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".mp3", ".mp4", ".webm", ".mov", ".avi", ".ogg", ".wav", ".flac",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".css", ".js", ".json", ".xml", ".rss", ".txt", ".csv",
    ".exe", ".dmg", ".deb", ".rpm", ".apk", ".iso",
)
_MAX_URL_LEN = 400


class Crawler:
    def __init__(
        self,
        index: Index,
        max_pages: int = 500,
        max_depth: int = 3,
        per_host_cap: int = 100,
        same_host_only: bool = False,
        default_delay: float = 1.0,
        timeout: float = 15.0,
        workers: int = 8,
        category: str = "",
        want_images: bool = True,
        want_favicons: bool = True,
        verbose: bool = True,
    ):
        self.index = index
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.per_host_cap = per_host_cap
        self.same_host_only = same_host_only
        self.timeout = timeout
        self.workers = max(1, workers)
        self.category = category
        self.want_images = want_images
        self.want_favicons = want_favicons
        self.verbose = verbose

        self.robots = RobotsCache(
            user_agent=USER_AGENT, default_delay=default_delay, timeout=timeout
        )
        self.seed_hosts: set[str] = set()
        self.host_counts: dict[str, int] = defaultdict(int)
        self.pages_crawled = 0
        self.images_found = 0
        self._favicon_tried: set[str] = set()
        self._counts_lock = threading.Lock()      # only for counters
        self._log_lock = threading.Lock()
        self._stop = threading.Event()

    # ----- logging ---------------------------------------------------------

    def _log(self, message: str):
        if self.verbose:
            with self._log_lock:
                print(message, file=sys.stderr, flush=True)

    @staticmethod
    def _looks_like_page(url: str) -> bool:
        """Filter out non-HTML link targets before they hit the frontier."""
        if len(url) > _MAX_URL_LEN:
            return False
        path = urlsplit(url).path.lower()
        if not path:
            return True  # bare host — almost certainly a page
        return not path.endswith(_SKIP_EXT)

    # ----- seeding ---------------------------------------------------------

    def add_seeds(self, urls: list[str]) -> None:
        cleaned = []
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            self.seed_hosts.add(urlsplit(url).netloc)
            cleaned.append(url)
        self.index.add_links(cleaned, depth=0, category=self.category)

    # ----- fetching --------------------------------------------------------

    def _open(self, url: str, accept: str):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": FULL_USER_AGENT,
                "Accept": accept,
                "Accept-Encoding": "gzip",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

    @staticmethod
    def _maybe_gunzip(raw: bytes, resp) -> bytes:
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except OSError:
                pass
        return raw

    def _fetch_page(self, url: str) -> tuple[int, str] | None:
        """Return (status, html), or None if the page isn't indexable."""
        try:
            with self._open(url, "text/html,application/xhtml+xml") as resp:
                ctype = resp.headers.get("Content-Type", "").lower()
                if not any(a in ctype for a in ALLOWED_CONTENT):
                    return None
                raw = resp.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    return None
                raw = self._maybe_gunzip(raw, resp)
                charset = "utf-8"
                if "charset=" in ctype:
                    charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
                try:
                    html = raw.decode(charset, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")
                return resp.status, html
        except urllib.error.HTTPError as e:
            self._log(f"  ! HTTP {e.code}  {url}")
        except Exception as e:
            self._log(f"  ! {type(e).__name__}  {url}")
        return None

    def _fetch_favicon(self, host: str, scheme: str, declared: str = "") -> None:
        """Download and cache a site's favicon so result pages stay local."""
        with self._counts_lock:
            if host in self._favicon_tried:
                return
            self._favicon_tried.add(host)
        if self.index.has_favicon(host):
            return

        candidates = [c for c in (declared, f"{scheme}://{host}/favicon.ico") if c]
        for candidate in candidates:
            if not self.robots.can_fetch(candidate):
                continue
            try:
                self.robots.wait_if_needed(candidate)
                with self._open(candidate, "image/*") as resp:
                    ctype = resp.headers.get("Content-Type", "").lower().split(";")[0]
                    if not any(ctype.startswith(t) for t in FAVICON_TYPES):
                        continue
                    raw = resp.read(MAX_FAVICON_BYTES + 1)
                    if not raw or len(raw) > MAX_FAVICON_BYTES:
                        continue
                    raw = self._maybe_gunzip(raw, resp)
                    if ctype.startswith("application/"):
                        ctype = "image/x-icon"
                    self.index.set_favicon(host, raw, ctype)
                    return
            except Exception:
                continue

    # ----- crawl loop ------------------------------------------------------

    def _budget_left(self) -> bool:
        with self._counts_lock:
            return self.pages_crawled < self.max_pages

    def _worker(self):
        empty_polls = 0
        local_queue: list[tuple[str, int, str]] = []
        while not self._stop.is_set():
            if not self._budget_left():
                self._stop.set()
                return
            # Refill local queue from Redis in batches to reduce round-trips.
            if not local_queue:
                local_queue = self.index.claim_batch(10)
            if not local_queue:
                # Other workers may still be discovering links; wait briefly.
                empty_polls += 1
                if empty_polls > 10:
                    return
                time.sleep(0.1)
                continue
            empty_polls = 0
            item = local_queue.pop(0)
            try:
                self._process(*item)
            except Exception as e:
                self._log(f"  ! worker error: {type(e).__name__}: {e}")

    def _process(self, url: str, depth: int, category: str):
        category = category or self.category
        parts = urlsplit(url)
        host = parts.netloc

        # Check per-host cap without holding the lock for long.
        with self._counts_lock:
            if self.host_counts[host] >= self.per_host_cap:
                return
        if self.same_host_only and host not in self.seed_hosts:
            return

        # --- opt-out gate: a site that asked to be removed is never fetched ---
        if self.index.is_blocked(host):
            return

        # --- politeness gate: robots.txt first, always ---
        if not self.robots.can_fetch(url):
            self._log(f"  ⊘ robots.txt disallows  {url}")
            return
        self.robots.wait_if_needed(url)

        result = self._fetch_page(url)
        if result is None:
            return
        status, html = result
        page = parse_html(html, url)

        if page.noindex:
            self._log(f"  ⊘ meta noindex  {url}")
        else:
            self.index.upsert_document(
                url=url,
                title=page.title,
                description=page.description,
                body=page.text,
                host=host,
                lang=page.lang,
                category=category,
                status=status,
            )
            with self._counts_lock:
                self.host_counts[host] += 1
                self.pages_crawled += 1
                n = self.pages_crawled
            self._log(f"  ✓ [{n}] {url}  ({len(page.text.split())}w)")

            # Open Graph / JSON-LD, so results can show a preview image, a
            # byline and a date instead of a bare link.
            if page.meta or page.headings:
                try:
                    self.index.set_page_meta(url, host, page.meta, page.headings)
                except Exception:
                    pass

            # Batch-insert images for speed.
            if self.want_images and page.images:
                img_dicts = [
                    {"src": img.src, "page_url": url, "page_title": page.title,
                     "host": host, "alt": img.alt, "title": img.title,
                     "width": img.width, "height": img.height,
                     "is_logo": img.is_logo, "category": category}
                    for img in page.images
                ]
                try:
                    added = self.index.add_images_batch(img_dicts, category)
                    if added:
                        with self._counts_lock:
                            self.images_found += added
                except Exception:
                    # Fall back to individual inserts if batch fails.
                    for img in page.images:
                        try:
                            if self.index.add_image(
                                src=img.src, page_url=url, page_title=page.title,
                                host=host, alt=img.alt, title=img.title,
                                width=img.width, height=img.height,
                                is_logo=img.is_logo, category=category,
                            ):
                                with self._counts_lock:
                                    self.images_found += 1
                        except Exception:
                            pass

            # Store detected video embeds.
            if page.videos:
                for vid in page.videos:
                    try:
                        self.index.add_video(
                            embed_id=vid.embed_id,
                            platform=vid.platform,
                            page_url=vid.url or url,
                            page_title=page.title or vid.title,
                            host=host,
                            thumbnail=vid.thumbnail,
                            category=category,
                        )
                    except Exception:
                        pass

            if self.want_favicons:
                self._fetch_favicon(host, parts.scheme or "https", page.favicon)

        # enqueue discovered links — no per-link DB check, rely on Redis dedup
        if depth < self.max_depth and not page.nofollow:
            fresh = []
            for link in page.links:
                if not self._looks_like_page(link):
                    continue
                lhost = urlsplit(link).netloc
                if not lhost:
                    continue
                if self.same_host_only and lhost not in self.seed_hosts:
                    continue
                with self._counts_lock:
                    if self.host_counts.get(lhost, 0) >= self.per_host_cap:
                        continue
                fresh.append(link)
            if fresh:
                self.index.add_links(fresh, depth + 1, category)

    def crawl(self) -> int:
        started = time.time()
        label = f" [{self.category}]" if self.category else ""
        self._log(
            f"serikacrawler{label} starting — {self.max_pages} page budget, "
            f"depth {self.max_depth}, {self.workers} workers"
        )
        threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"serika-{i}")
            for i in range(self.workers)
        ]
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self._log("Interrupted — stopping workers…")
            self._stop.set()
            for t in threads:
                t.join(timeout=5)

        elapsed = time.time() - started
        rate = self.pages_crawled / elapsed if elapsed > 0 else 0
        self._log(
            f"Done in {elapsed:.0f}s — {self.pages_crawled} pages "
            f"({rate:.1f}/s), {self.images_found} images this run; "
            f"{self.index.document_count():,} pages / "
            f"{self.index.image_count():,} images in index."
        )
        return self.pages_crawled


def backfill_favicons(index: Index, timeout: float = 10.0, verbose: bool = True) -> int:
    """Fetch favicons for every host already in the index that lacks one."""
    robots = RobotsCache(user_agent=USER_AGENT, default_delay=0.5, timeout=timeout)
    crawler = Crawler(index, verbose=verbose, timeout=timeout)
    crawler.robots = robots
    fetched = 0
    for host in index.all_hosts():
        if index.has_favicon(host):
            continue
        crawler._fetch_favicon(host, "https")
        if index.has_favicon(host):
            fetched += 1
            if verbose:
                print(f"  ✓ favicon  {host}", file=sys.stderr, flush=True)
    return fetched
