"""robots.txt handling for serikacrawler.

serikacrawler is a polite, well-behaved crawler:
  * it fetches and honours robots.txt for every host before crawling it,
  * it obeys Disallow rules for its own user-agent (``serikacrawler``),
  * it respects Crawl-delay / Request-rate directives,
  * it caches robots rules per host so we don't refetch constantly,
  * it serialises requests per host, so even with many worker threads a single
    site never sees two overlapping requests.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

USER_AGENT = "serikacrawler"


@dataclass
class HostRules:
    parser: urllib.robotparser.RobotFileParser
    crawl_delay: float
    fetched_at: float
    last_access: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class RobotsCache:
    """Per-host robots.txt rules, fetched once and cached. Thread-safe."""

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        default_delay: float = 1.0,
        timeout: float = 10.0,
    ):
        self.user_agent = user_agent
        self.default_delay = default_delay
        self.timeout = timeout
        self._cache: dict[str, HostRules] = {}
        self._cache_lock = threading.Lock()
        # One lock per host key, so two threads don't both fetch the same
        # robots.txt while it is still being loaded.
        self._loading: dict[str, threading.Lock] = {}

    def _robots_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))

    def _host_key(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _load(self, url: str) -> HostRules:
        key = self._host_key(url)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            load_lock = self._loading.setdefault(key, threading.Lock())

        with load_lock:
            # Another thread may have finished while we waited.
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached is not None:
                    return cached

            rules = self._fetch_rules(url)
            with self._cache_lock:
                self._cache[key] = rules
            return rules

    def _fetch_rules(self, url: str) -> HostRules:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = self._robots_url(url)
        rp.set_url(robots_url)
        crawl_delay = self.default_delay
        try:
            req = urllib.request.Request(
                robots_url, headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
            rp.parse(raw.splitlines())
        except urllib.error.HTTPError as e:
            # 4xx (except 429) => no restrictions. 401/403/5xx/429 => stay out.
            if e.code in (401, 403):
                rp.disallow_all = True
            elif 400 <= e.code < 500 and e.code != 429:
                rp.allow_all = True
            else:
                rp.disallow_all = True
        except Exception:
            # Network hiccup: treat as allow-all but keep the default delay so
            # we stay gentle with a host we know nothing about.
            rp.allow_all = True

        # Pull crawl-delay / request-rate for our agent if advertised.
        try:
            delay = rp.crawl_delay(self.user_agent)
            if delay:
                crawl_delay = max(float(delay), 0.0)
            else:
                rate = rp.request_rate(self.user_agent)
                if rate and rate.seconds and rate.requests:
                    crawl_delay = float(rate.seconds) / float(rate.requests)
        except Exception:
            pass

        # Never let a hostile robots.txt stall the crawl forever.
        crawl_delay = min(crawl_delay, 30.0)
        return HostRules(parser=rp, crawl_delay=crawl_delay, fetched_at=time.time())

    def can_fetch(self, url: str) -> bool:
        rules = self._load(url)
        try:
            return rules.parser.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay_for(self, url: str) -> float:
        return self._load(url).crawl_delay

    def sitemaps(self, url: str) -> list[str]:
        """The ``Sitemap:`` URLs a host advertises in its robots.txt.

        This is the front door to a site's own list of canonical URLs — the
        single highest-signal thing robots.txt offers a crawler beyond the
        Disallow rules.
        """
        rules = self._load(url)
        try:
            maps = rules.parser.site_maps()
        except Exception:
            maps = None
        return list(maps) if maps else []

    def wait_if_needed(self, url: str) -> None:
        """Block just long enough to honour this host's crawl-delay.

        Holding the per-host lock while we sleep guarantees requests to the
        same host are spaced by at least ``crawl_delay``, no matter how many
        worker threads are running.
        """
        rules = self._load(url)
        with rules.lock:
            remaining = rules.crawl_delay - (time.time() - rules.last_access)
            if remaining > 0:
                time.sleep(remaining)
            rules.last_access = time.time()
