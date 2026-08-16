"""SerikaSearch index storage — PostgreSQL + Redis backend.

PostgreSQL stores documents, images, videos, and favicons with weighted
tsvector columns for full-text search. Redis manages the crawl frontier
queue and optional search caching.

  * ``documents``  — crawled pages (web search)
  * ``images``     — images discovered on those pages (image search)
  * ``videos``     — detected video embeds (video search)
  * ``favicons``   — cached site icons served locally

Connections are managed by a psycopg2 ThreadedConnectionPool so the
concurrent crawler and threaded web server share a pool safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extensions
import psycopg2.pool
import redis

from .query import ParsedQuery, parse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load database/redis URLs from environment or serika/config.json."""
    # Environment variables take priority (Docker/Cloud deployments).
    db_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    if db_url:
        return {"database_url": db_url, "redis_url": redis_url}
    # Fall back to config.json for local development.
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    cfg_path = os.path.normpath(cfg_path)
    if os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            return json.load(f)
    return {"database_url": "", "redis_url": ""}


# ---------------------------------------------------------------------------
# Schema (PostgreSQL)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          SERIAL PRIMARY KEY,
    url         TEXT UNIQUE NOT NULL,
    title       TEXT,
    description TEXT,
    body        TEXT,
    host        TEXT,
    lang        TEXT,
    category    TEXT,
    word_count  INTEGER DEFAULT 0,
    fetched_at  DOUBLE PRECISION,
    status      INTEGER,
    tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(body, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(url, '')), 'D')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_documents_tsv ON documents USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_documents_host ON documents(host);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);

CREATE TABLE IF NOT EXISTS images (
    id         SERIAL PRIMARY KEY,
    src        TEXT UNIQUE NOT NULL,
    page_url   TEXT,
    page_title TEXT,
    host       TEXT,
    alt        TEXT,
    title      TEXT,
    width      INTEGER DEFAULT 0,
    height     INTEGER DEFAULT 0,
    is_logo    INTEGER DEFAULT 0,
    category   TEXT,
    added_at   DOUBLE PRECISION,
    tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(alt, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(page_title, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(host, '')), 'C')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_images_tsv ON images USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_images_host ON images(host);
CREATE INDEX IF NOT EXISTS idx_images_category ON images(category);

CREATE TABLE IF NOT EXISTS videos (
    id         SERIAL PRIMARY KEY,
    embed_id   TEXT NOT NULL,
    platform   TEXT NOT NULL,
    page_url   TEXT,
    page_title TEXT,
    host       TEXT,
    thumbnail  TEXT,
    category   TEXT,
    added_at   DOUBLE PRECISION,
    UNIQUE(embed_id, platform),
    tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(page_title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(host, '')), 'B')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_videos_tsv ON videos USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_videos_host ON videos(host);

CREATE TABLE IF NOT EXISTS favicons (
    host         TEXT PRIMARY KEY,
    data         BYTEA,
    content_type TEXT,
    fetched_at   DOUBLE PRECISION
);

-- Open Graph / Twitter card / JSON-LD scraped at crawl time. Kept in its own
-- table so the hot `documents` rows stay narrow: a search touches documents on
-- every query, but rich-card metadata is only read for the ten results shown.
CREATE TABLE IF NOT EXISTS page_meta (
    url            TEXT PRIMARY KEY,
    host           TEXT,
    site_name      TEXT,
    og_type        TEXT,
    image          TEXT,
    image_width    INTEGER DEFAULT 0,
    image_height   INTEGER DEFAULT 0,
    image_alt      TEXT,
    author         TEXT,
    published      TEXT,
    section        TEXT,
    rating         DOUBLE PRECISION,
    rating_count   INTEGER DEFAULT 0,
    price          TEXT,
    price_currency TEXT,
    duration       TEXT,
    headings       TEXT,
    payload        TEXT,
    updated_at     DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_page_meta_host ON page_meta(host);

-- Hosts that asked to be removed. The crawler checks this before every fetch
-- and the search layer filters it out of results, so an opt-out takes effect
-- immediately rather than at the next re-crawl.
CREATE TABLE IF NOT EXISTS blocked_hosts (
    host       TEXT PRIMARY KEY,
    reason     TEXT,
    created_at DOUBLE PRECISION
);

-- Removal requests submitted through /how-to-opt-out, kept so a human can
-- verify site ownership before a block becomes permanent.
CREATE TABLE IF NOT EXISTS optout_requests (
    id         SERIAL PRIMARY KEY,
    host       TEXT NOT NULL,
    email      TEXT,
    scope      TEXT,
    note       TEXT,
    status     TEXT DEFAULT 'received',
    created_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_optout_host ON optout_requests(host);

-- Supports prefix-matched autocomplete straight off the index. text_pattern_ops
-- is what makes `lower(title) LIKE 'foo%'` an index scan instead of a seq scan.
CREATE INDEX IF NOT EXISTS idx_documents_title_prefix
    ON documents (lower(title) text_pattern_ops);

CREATE INDEX IF NOT EXISTS idx_documents_fetched ON documents(fetched_at DESC);
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    url: str
    title: str
    description: str
    host: str
    snippet: str
    score: float


@dataclass
class ImageResult:
    src: str
    page_url: str
    page_title: str
    host: str
    alt: str
    width: int
    height: int
    score: float


_SUGGESTION_TRIM = re.compile(
    r"\s*[-–—|·:]\s*(?:home|official site|official website|welcome).*$", re.I
)
_SUGGESTION_TAIL = re.compile(r"\s*[-–—|]\s*[^-–—|]{1,40}$")

# "Indexed within" windows offered by the results-page filter. These are crawl
# times, not publication times: the crawler knows when it fetched a page, and
# only a minority of pages state when they were written.
FRESHNESS_WINDOWS = {
    "day": 86400,
    "week": 604800,
    "month": 2629746,
    "year": 31556952,
}


def _suggestion_phrase(title: str, prefix: str) -> str:
    """Turn a page title into a short, search-shaped completion."""
    text = re.sub(r"\s+", " ", (title or "")).strip()
    if not text or len(text) > 120:
        return ""
    text = _SUGGESTION_TRIM.sub("", text)
    # Drop a trailing " - Site Name" so completions read like queries.
    if len(text) > 40:
        text = _SUGGESTION_TAIL.sub("", text) or text
    text = text.strip(" -–—|·:,")
    if len(text) < len(prefix) or len(text) > 70:
        return ""
    if prefix not in text.lower():
        return ""
    return text


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class Index:
    """PostgreSQL-backed index with Redis frontier queue."""

    # SEO / ranking helpers (ts_rank_cd returns positive values; higher = better).
    # We ORDER BY composite DESC, so to boost we ADD a positive amount.
    _RICHNESS_WEIGHT = 0.05
    _RECENCY_WEIGHT = 0.1
    _RECENCY_SPAN = 30 * 24 * 3600
    _TITLE_BOOST = 2.0
    _URL_BOOST = 1.0
    _BODY_ONLY_PENALTY = 1.5

    # ts_rank_cd weights: [D, C, B, A] — A=title=1.0, B=desc=0.4, C=body=0.1, D=url=0.05
    _RANK_WEIGHTS = "{0.05, 0.1, 0.4, 1.0}"

    def __init__(self, path: str = ""):
        """``path`` is ignored — URLs come from config.json or environment."""
        cfg = _load_config()
        db_url = cfg.get("database_url", "")
        redis_url = cfg.get("redis_url", "")

        if not db_url:
            raise ValueError("No database_url in config.json or DATABASE_URL env")

        # Pool sizing matters more than it looks: a managed PostgreSQL often
        # allows only ~100 connections in total, and this repository runs two
        # containers (web + crawler) against the same database. Keeping the
        # ceiling modest leaves room for both, for migrations, and for a psql
        # session when something goes wrong.
        min_conn = max(1, int(os.environ.get("DB_POOL_MIN", "2")))
        max_conn = max(min_conn, int(os.environ.get("DB_POOL_MAX", "16")))
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            min_conn, max_conn, db_url,
        )
        self._local = threading.local()
        self._write_lock = threading.RLock()  # kept for compat but unused

        # Redis for frontier + caching.
        self._redis: Optional[redis.Redis] = None
        if redis_url:
            self._redis = redis.from_url(redis_url, decode_responses=True)

        self._init_schema()

    # ----- connection management -------------------------------------------

    def _borrow(self):
        """Take a connection from the pool, waiting briefly if it's saturated.

        A short spin beats failing the request outright: pool exhaustion under
        a burst is normally over in milliseconds, because every web request
        returns its connection as soon as it finishes rendering.
        """
        deadline = time.time() + 5.0
        while True:
            try:
                return self._pool.getconn()
            except psycopg2.pool.PoolError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.02)

    def _get_conn(self):
        """Get a connection from the pool (thread-local)."""
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = self._borrow()
            conn.autocommit = False
            # Prefer GIN index scans over sequential scans for FTS queries.
            # On small tables PostgreSQL defaults to seq scan which is 100x
            # slower for tsvector matches.
            with conn.cursor() as cur:
                cur.execute("SET enable_seqscan = off")
                cur.execute("SET jit = off")
            conn.commit()
            self._local.conn = conn
        return conn

    def _put_conn(self, commit: bool = False):
        """Return the thread-local connection to the pool."""
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            if commit:
                conn.commit()
            self._pool.putconn(conn)
        self._local.conn = None

    def release(self) -> None:
        """Hand this thread's connection back to the pool.

        The web server runs every request on a fresh thread, so without this
        each request would park a connection in a thread-local that dies with
        the thread — the pool would hand out its last connection and then
        PostgreSQL would start refusing clients. Long-lived workers (the
        crawler) simply never call it and keep their connection for the
        duration.

        Any open transaction is rolled back first: a connection returned
        mid-transaction would hold locks and poison the next borrower.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        try:
            if not conn.closed:
                if conn.get_transaction_status() != \
                        psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                    conn.rollback()
                self._pool.putconn(conn)
        except Exception:
            # A broken connection must not be recycled — drop it so the pool
            # opens a fresh one next time.
            try:
                self._pool.putconn(conn, close=True)
            except Exception:
                pass
        finally:
            self._local.conn = None

    @property
    def conn(self):
        """Backward-compat property — returns a thread-local connection."""
        return self._get_conn()

    def _init_schema(self):
        """Create tables and indexes if they don't exist."""
        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
            conn.autocommit = False
        finally:
            self._pool.putconn(conn)

    # ----- documents -------------------------------------------------------

    def has_url(self, url: str) -> bool:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE url=%s LIMIT 1", (url,))
            return cur.fetchone() is not None

    def upsert_document(
        self,
        url: str,
        title: str,
        description: str,
        body: str,
        host: str,
        lang: str = "",
        category: str = "",
        status: int = 200,
    ) -> None:
        word_count = len(body.split())
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (url, title, description, body, host,
                                           lang, category, word_count, fetched_at, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(url) DO UPDATE SET
                        title=EXCLUDED.title,
                        description=EXCLUDED.description,
                        body=EXCLUDED.body,
                        host=EXCLUDED.host,
                        lang=EXCLUDED.lang,
                        category=COALESCE(NULLIF(EXCLUDED.category,''), documents.category),
                        word_count=EXCLUDED.word_count,
                        fetched_at=EXCLUDED.fetched_at,
                        status=EXCLUDED.status
                    """,
                    (url, title, description, body, host, lang, category,
                     word_count, time.time(), status),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def document_count(self, category: str = "") -> int:
        if not category and self._redis:
            cached = self._redis.get("stats:doc_count")
            if cached is not None:
                try:
                    return int(cached)
                except Exception:
                    pass
        conn = self._get_conn()
        with conn.cursor() as cur:
            if category:
                cur.execute("SELECT COUNT(*) FROM documents WHERE category=%s", (category,))
            else:
                cur.execute("SELECT COUNT(*) FROM documents")
            result = cur.fetchone()[0]
        if not category and self._redis:
            try: self._redis.setex("stats:doc_count", 15, result)
            except: pass
        return result

    def hosts(self) -> list[tuple[str, int]]:
        if self._redis:
            cached = self._redis.get("stats:hosts")
            if cached:
                try:
                    return [tuple(r) for r in json.loads(cached)]
                except Exception:
                    pass
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT host, COUNT(*) AS c FROM documents GROUP BY host ORDER BY c DESC"
            )
            result = [(r[0], r[1]) for r in cur.fetchall()]
        if self._redis:
            try: self._redis.setex("stats:hosts", 120, json.dumps(result))
            except: pass
        return result

    def categories(self) -> list[tuple[str, int]]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT category, COUNT(*) AS c FROM documents
                   WHERE category IS NOT NULL AND category <> ''
                   GROUP BY category ORDER BY c DESC"""
            )
            return [(r[0], r[1]) for r in cur.fetchall()]

    def all_hosts(self) -> list[str]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT host FROM documents WHERE host IS NOT NULL AND host <> ''"
            )
            return [r[0] for r in cur.fetchall()]

    # ----- images ----------------------------------------------------------

    def add_image(
        self,
        src: str,
        page_url: str,
        page_title: str,
        host: str,
        alt: str,
        title: str = "",
        width: int = 0,
        height: int = 0,
        is_logo: bool = False,
        category: str = "",
    ) -> bool:
        """Insert an image if we haven't seen this src. Returns True if new."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO images
                       (src, page_url, page_title, host, alt, title, width, height,
                        is_logo, category, added_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (src) DO NOTHING
                       RETURNING id""",
                    (src, page_url, page_title, host, alt, title, width, height,
                     1 if is_logo else 0, category, time.time()),
                )
                row = cur.fetchone()
            conn.commit()
            return row is not None
        except Exception:
            conn.rollback()
            raise

    def add_images_batch(
        self, images: list[dict], category: str = ""
    ) -> int:
        """Batch-insert images. Each dict has keys: src, page_url, page_title,
        host, alt, title, width, height, is_logo, category.
        Returns number of new images added."""
        if not images:
            return 0
        now = time.time()
        rows = [
            (img["src"], img.get("page_url", ""), img.get("page_title", ""),
             img.get("host", ""), img.get("alt", ""), img.get("title", ""),
             img.get("width", 0), img.get("height", 0),
             1 if img.get("is_logo") else 0,
             img.get("category", "") or category, now)
            for img in images
        ]
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    """INSERT INTO images
                       (src, page_url, page_title, host, alt, title, width, height,
                        is_logo, category, added_at)
                       VALUES %s
                       ON CONFLICT (src) DO NOTHING
                       RETURNING id""",
                    rows,
                )
                added = len(cur.fetchall())
            conn.commit()
            return added
        except Exception:
            conn.rollback()
            raise

    def image_count(self, sites: list[str] | None = None) -> int:
        conn = self._get_conn()
        with conn.cursor() as cur:
            if sites:
                likes = " OR ".join("host ILIKE %s" for _ in sites)
                params = [f"%{s}%" for s in sites]
                cur.execute(f"SELECT COUNT(*) FROM images WHERE {likes}", params)
            else:
                cur.execute("SELECT COUNT(*) FROM images")
            return cur.fetchone()[0]

    def browse_images(
        self, sites: list[str] | None = None, limit: int = 40, offset: int = 0
    ) -> list[ImageResult]:
        sql = ("SELECT src, page_url, page_title, host, alt, width, height "
               "FROM images")
        params: list = []
        if sites:
            likes = " OR ".join("host ILIKE %s" for _ in sites)
            sql += f" WHERE {likes}"
            params += [f"%{s}%" for s in sites]
        sql += (" ORDER BY (alt <> '') DESC, (width * height) DESC, "
                "added_at DESC LIMIT %s OFFSET %s")
        params += [limit, offset]
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                ImageResult(
                    src=r[0], page_url=r[1] or "", page_title=r[2] or "",
                    host=r[3] or "", alt=r[4] or "", width=r[5] or 0,
                    height=r[6] or 0, score=0.0,
                )
                for r in cur.fetchall()
            ]

    def first_image_for_page(self, page_url: str) -> Optional[str]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT src FROM images WHERE page_url=%s
                   ORDER BY (width * height) DESC NULLS LAST, id ASC LIMIT 1""",
                (page_url,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def similar_images(
        self, src: str = "", page_url: str = "", host: str = "", limit: int = 12
    ) -> list[ImageResult]:
        seen = {src}
        rows: list = []

        def take(cur):
            for r in cur.fetchall():
                if r[0] in seen or len(rows) >= limit:
                    continue
                seen.add(r[0])
                rows.append(r)

        order = "ORDER BY (alt <> '') DESC, (width * height) DESC NULLS LAST, added_at DESC"
        cols = "src, page_url, page_title, host, alt, width, height"
        conn = self._get_conn()
        with conn.cursor() as cur:
            if page_url:
                cur.execute(
                    f"SELECT {cols} FROM images WHERE page_url=%s AND src<>%s "
                    f"{order} LIMIT %s",
                    (page_url, src, limit),
                )
                take(cur)
            if host and len(rows) < limit:
                cur.execute(
                    f"SELECT {cols} FROM images WHERE host=%s AND src<>%s AND "
                    f"page_url<>%s {order} LIMIT %s",
                    (host, src, page_url, limit - len(rows)),
                )
                take(cur)
        return [
            ImageResult(
                src=r[0], page_url=r[1] or "", page_title=r[2] or "",
                host=r[3] or "", alt=r[4] or "", width=r[5] or 0,
                height=r[6] or 0, score=0.0,
            )
            for r in rows
        ]

    def search_images(
        self, query, limit: int = 40, offset: int = 0
    ) -> list[ImageResult]:
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        if not fts:
            return []

        # Check Redis cache.
        cache_key = f"imgsearch:{hashlib.md5(f'{fts}|{parsed.sites}|{parsed.intitle}|{parsed.inurl}|{limit}|{offset}'.encode()).hexdigest()}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached:
                try:
                    return [ImageResult(**r) for r in json.loads(cached)]
                except Exception:
                    pass

        qtext = fts
        q_lower = qtext.lower()
        logo_penalty = 0.0 if ("logo" in q_lower or "icon" in q_lower) else 1.5

        # Use a CTE to rank+filter first, then apply logo penalty in outer query.
        # Filter out 0x0 images (tracking pixels, broken images) unless we have
        # very few results — they're never useful in image search.
        where = "WHERE i.tsv @@ websearch_to_tsquery('english', %s) AND (i.width > 0 AND i.height > 0)"
        params: list = [qtext]

        all_sites = list(parsed.sites)
        if all_sites:
            likes = " OR ".join("i.host ILIKE %s" for _ in all_sites)
            where += f" AND ({likes})"
            params += [f"%{s}%" for s in all_sites]
        for w in parsed.intitle:
            where += " AND i.page_title ILIKE %s"
            params.append(f"%{w}%")
        for w in parsed.inurl:
            where += " AND i.page_url ILIKE %s"
            params.append(f"%{w}%")

        sql = (
            "WITH ranked AS ("
            "  SELECT i.src, i.page_url, i.page_title, i.host, i.alt,"
            "         i.width, i.height, i.is_logo,"
            f"         ts_rank_cd(%s, i.tsv, websearch_to_tsquery('english', %s)) AS score"
            "  FROM images i " + where +
            ")"
            " SELECT sub.src, sub.page_url, sub.page_title, sub.host, sub.alt,"
            "        sub.width, sub.height, sub.is_logo, sub.score"
            " FROM ranked sub"
            f" ORDER BY (sub.score"
            f"  - {logo_penalty} * sub.is_logo"
            f"  + 0.5 * CASE WHEN sub.width > 100 AND sub.height > 100 THEN 1 ELSE 0 END"
            f"  + 0.3 * CASE WHEN sub.width * sub.height > 50000 THEN 1 ELSE 0 END"
            f"  + 0.1 * CASE WHEN sub.alt <> '' THEN 1 ELSE 0 END"
            f") DESC LIMIT %s OFFSET %s"
        )
        params = [self._RANK_WEIGHTS, qtext] + params + [limit, offset]

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = [
                    ImageResult(
                        src=r[0], page_url=r[1] or "", page_title=r[2] or "",
                        host=r[3] or "", alt=r[4] or "", width=r[5] or 0,
                        height=r[6] or 0, score=float(r[8] or 0),
                    )
                    for r in cur.fetchall()
                ]
        except Exception:
            return []
        # Cache results.
        if self._redis and results:
            try:
                data = json.dumps([
                    {"src": r.src, "page_url": r.page_url, "page_title": r.page_title,
                     "host": r.host, "alt": r.alt, "width": r.width,
                     "height": r.height, "score": r.score}
                    for r in results
                ])
                self._redis.setex(cache_key, 300, data)
            except Exception:
                pass
        return results

    def count_image_matches(self, query, sites: list[str] | None = None) -> int:
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        if not fts:
            return 0
        all_sites = list(sites or []) + list(parsed.sites)
        sql = ("SELECT COUNT(*) FROM images i "
               "WHERE i.tsv @@ websearch_to_tsquery('english', %s) "
               "AND (i.width > 0 AND i.height > 0)")
        params: list = [fts]
        if all_sites:
            likes = " OR ".join("i.host ILIKE %s" for _ in all_sites)
            sql += f" AND ({likes})"
            params += [f"%{s}%" for s in all_sites]
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        except Exception:
            return 0

    # ----- videos ----------------------------------------------------------

    def add_video(
        self, embed_id: str, platform: str, page_url: str,
        page_title: str, host: str, thumbnail: str = "",
        category: str = "",
    ) -> bool:
        """Insert a video if not already indexed. Returns True if new."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO videos
                       (embed_id, platform, page_url, page_title, host,
                        thumbnail, category, added_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (embed_id, platform) DO NOTHING
                       RETURNING id""",
                    (embed_id, platform, page_url, page_title, host,
                     thumbnail, category, time.time()),
                )
                row = cur.fetchone()
            conn.commit()
            return row is not None
        except Exception:
            conn.rollback()
            raise

    def video_count(self) -> int:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM videos")
            return cur.fetchone()[0]

    def search_videos(
        self, query, limit: int = 20, offset: int = 0
    ) -> list[dict]:
        """Search indexed videos by title/host."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts

        # Check Redis cache.
        cache_key = f"vidsearch:{hashlib.md5(f'{fts}|{parsed.sites}|{limit}|{offset}'.encode()).hexdigest()}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached:
                try:
                    return json.loads(cached)
                except Exception:
                    pass

        conn = self._get_conn()
        if not fts:
            sql = ("SELECT embed_id, platform, page_url, page_title, host, "
                   "thumbnail FROM videos ORDER BY added_at DESC "
                   "LIMIT %s OFFSET %s")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [limit, offset])
                    results = [
                        {"embed_id": r[0], "platform": r[1], "page_url": r[2] or "",
                         "page_title": r[3] or "", "host": r[4] or "",
                         "thumbnail": r[5] or ""}
                        for r in cur.fetchall()
                    ]
            except Exception:
                conn.rollback()
                return []
        else:
            sql = ("SELECT v.embed_id, v.platform, v.page_url, v.page_title, "
                   "v.host, v.thumbnail, "
                   "ts_rank_cd(%s, v.tsv, websearch_to_tsquery('english', %s)) AS score "
                   "FROM videos v "
                   "WHERE v.tsv @@ websearch_to_tsquery('english', %s) "
                   "ORDER BY score DESC LIMIT %s OFFSET %s")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [self._RANK_WEIGHTS, fts, fts, limit, offset])
                    results = [
                        {"embed_id": r[0], "platform": r[1], "page_url": r[2] or "",
                         "page_title": r[3] or "", "host": r[4] or "",
                         "thumbnail": r[5] or ""}
                        for r in cur.fetchall()
                    ]
            except Exception:
                conn.rollback()
                return []

        if self._redis and results:
            try:
                self._redis.setex(cache_key, 300, json.dumps(results))
            except Exception:
                pass
        return results

    def count_video_matches(self, query) -> int:
        """Count videos matching a query."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts

        cache_key = f"vidcount:{hashlib.md5(f'{fts}|{parsed.sites}'.encode()).hexdigest()}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached is not None:
                try:
                    return int(cached)
                except Exception:
                    pass

        if not fts:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM videos")
                result = cur.fetchone()[0]
            if self._redis:
                try: self._redis.setex(cache_key, 300, result)
                except: pass
            return result
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM videos v WHERE v.tsv @@ websearch_to_tsquery('english', %s)",
                    [fts],
                )
                result = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            return 0
        if self._redis:
            try: self._redis.setex(cache_key, 300, result)
            except: pass
        return result

    # ----- favicons --------------------------------------------------------

    def set_favicon(self, host: str, data: bytes, content_type: str) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO favicons (host, data, content_type, fetched_at)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (host) DO UPDATE SET
                         data=EXCLUDED.data,
                         content_type=EXCLUDED.content_type,
                         fetched_at=EXCLUDED.fetched_at""",
                    (host, psycopg2.Binary(data), content_type, time.time()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def get_favicon(self, host: str) -> Optional[tuple[bytes, str]]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data, content_type FROM favicons WHERE host=%s", (host,)
            )
            row = cur.fetchone()
            if row and row[0]:
                return bytes(row[0]), row[1] or "image/x-icon"
            return None

    def has_favicon(self, host: str) -> bool:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM favicons WHERE host=%s LIMIT 1", (host,))
            return cur.fetchone() is not None

    def favicon_count(self) -> int:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM favicons")
            return cur.fetchone()[0]

    # ----- frontier (Redis-backed) -----------------------------------------

    def add_to_frontier(self, url: str, depth: int, category: str = "") -> None:
        """Add a single URL to the frontier queue."""
        if not self._redis:
            return
        if self._redis.sismember("crawled", url):
            return
        self._redis.zadd("frontier", {url: depth})
        self._redis.hset(f"frontier:meta:{url}", mapping={
            "depth": depth, "category": category,
        })

    def add_links(self, urls: list[str], depth: int, category: str = "") -> None:
        """Enqueue many URLs at once."""
        if not self._redis or not urls:
            return
        # Check which URLs are already crawled (outside pipeline for real results).
        pipe = self._redis.pipeline()
        for u in urls:
            pipe.sismember("crawled", u)
        already = pipe.execute()
        # Add only uncrawled URLs to the frontier.
        pipe = self._redis.pipeline()
        for u, done in zip(urls, already):
            if not done:
                pipe.zadd("frontier", {u: depth})
                pipe.hset(f"frontier:meta:{u}", mapping={
                    "depth": depth, "category": category,
                })
        pipe.execute()

    def claim_next(self) -> Optional[tuple[str, int, str]]:
        """Atomically take the next frontier URL."""
        if not self._redis:
            return None
        # ZPOPMIN returns [(member, score), ...]
        result = self._redis.zpopmin("frontier", 1)
        if not result:
            return None
        url, depth = result[0]
        depth = int(depth)
        meta = self._redis.hgetall(f"frontier:meta:{url}")
        category = meta.get("category", "") if meta else ""
        self._redis.delete(f"frontier:meta:{url}")
        self._redis.sadd("crawled", url)
        return url, depth, category

    def claim_batch(self, count: int = 10) -> list[tuple[str, int, str]]:
        """Atomically claim multiple frontier URLs at once to reduce Redis
        round-trips. Returns a list of (url, depth, category) tuples."""
        if not self._redis:
            return []
        results = self._redis.zpopmin("frontier", count)
        if not results:
            return []
        out = []
        pipe = self._redis.pipeline()
        for url, depth in results:
            pipe.hgetall(f"frontier:meta:{url}")
            pipe.delete(f"frontier:meta:{url}")
            pipe.sadd("crawled", url)
        pipe_responses = pipe.execute()
        for i, (url, depth) in enumerate(results):
            meta = pipe_responses[i * 3] if i * 3 < len(pipe_responses) else {}
            category = meta.get("category", "") if meta else ""
            out.append((url, int(depth), category))
        return out

    def frontier_pending(self) -> int:
        if not self._redis:
            return 0
        return self._redis.zcard("frontier")

    def commit(self) -> None:
        """Commit any pending writes on the thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            try:
                conn.commit()
            except Exception:
                conn.rollback()

    # ----- web search ------------------------------------------------------

    def _site_clause(self, sites: list[str], prefix: str = " AND") -> tuple[str, list]:
        """Build a SQL host-filter clause for `site:` operators."""
        if not sites:
            return "", []
        likes = " OR ".join("d.host ILIKE %s" for _ in sites)
        return f"{prefix} ({likes})", [f"%{s}%" for s in sites]

    def _freshness_clause(self, freshness: str) -> tuple[str, list]:
        """`AND d.fetched_at > …` for the results-page "indexed within" filter."""
        window = FRESHNESS_WINDOWS.get((freshness or "").lower())
        if not window:
            return "", []
        return " AND d.fetched_at > %s", [time.time() - window]

    def search(
        self, query, limit: int = 10, offset: int = 0, freshness: str = ""
    ) -> list[SearchResult]:
        """Full-text web search with Google-style operators and SEO ranking."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        site_sql, site_params = self._site_clause(parsed.sites)
        fresh_sql, fresh_params = self._freshness_clause(freshness)

        # Check Redis cache first.
        cache_key = f"search:{hashlib.md5(f'{fts}|{parsed.sites}|{parsed.intitle}|{parsed.inurl}|{limit}|{offset}|{freshness}'.encode()).hexdigest()}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached:
                try:
                    return [SearchResult(**r) for r in json.loads(cached)]
                except Exception:
                    pass

        # site: with no text query → host-filtered browse.
        if not fts:
            if not parsed.sites:
                return []
            sql = (
                "SELECT d.url, d.title, d.description, d.host, "
                "'' AS snippet, 0.0 AS score, d.word_count, d.fetched_at "
                "FROM documents d WHERE 1=1" + site_sql + fresh_sql +
                " ORDER BY d.word_count DESC NULLS LAST, d.fetched_at DESC "
                "LIMIT %s OFFSET %s"
            )
            params = site_params + fresh_params + [limit, offset]
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = [self._row_to_result(r) for r in cur.fetchall()]
            self._cache_results(cache_key, results)
            return results

        # intitle/inurl filters
        intitle_sql = ""
        intitle_params: list = []
        for w in parsed.intitle:
            intitle_sql += " AND d.title ILIKE %s"
            intitle_params.append(f"%{w}%")
        inurl_sql = ""
        inurl_params: list = []
        for w in parsed.inurl:
            inurl_sql += " AND d.url ILIKE %s"
            inurl_params.append(f"%{w}%")

        now = time.time()
        query_words = [w for w in fts.lower().replace('"', '').split()
                       if w and w not in ('and', 'or', 'not', 'near')]
        # Build title/url ILIKE params for boost checks.
        title_ilike = " OR ".join(
            "d.title ILIKE %s" for _ in query_words
        ) if query_words else "FALSE"
        title_params = [f"%{w}%" for w in query_words]
        url_ilike = " OR ".join(
            "d.url ILIKE %s" for _ in query_words
        ) if query_words else "FALSE"
        url_params = [f"%{w}%" for w in query_words]

        # Subquery: compute rank + title/url match flags, then ORDER BY in outer.
        rank_sql = (
            "SELECT * FROM ("
            " SELECT d.id, d.url, d.title, d.description, d.host, d.body,"
            " d.word_count, d.fetched_at,"
            f" ts_rank_cd(%s, d.tsv, websearch_to_tsquery('english', %s)) AS rank,"
            f" CASE WHEN ({title_ilike}) THEN 1 ELSE 0 END AS has_title,"
            f" CASE WHEN ({url_ilike}) THEN 1 ELSE 0 END AS has_url"
            " FROM documents d"
            " WHERE d.tsv @@ websearch_to_tsquery('english', %s)"
            + site_sql + intitle_sql + inurl_sql + fresh_sql +
            ") sub"
            f" ORDER BY (sub.rank"
            f"  + {self._TITLE_BOOST} * sub.has_title"
            f"  + {self._URL_BOOST} * sub.has_url"
            f"  - {self._BODY_ONLY_PENALTY} * (1 - sub.has_title)"
            f"  + {self._RICHNESS_WEIGHT} * LEAST(GREATEST(sub.word_count / 5000.0, 0), 1.0)"
            f"  + {self._RECENCY_WEIGHT} * GREATEST(0.0, 1.0 - (%s - sub.fetched_at) / {self._RECENCY_SPAN})"
            f") DESC LIMIT %s OFFSET %s"
        )
        rank_params = ([self._RANK_WEIGHTS, fts]
                        + title_params + url_params
                        + [fts]
                        + site_params + intitle_params + inurl_params
                        + fresh_params
                        + [now, limit, offset])
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(rank_sql, rank_params)
                ranked = cur.fetchall()
        except Exception:
            conn.rollback()
            return []
        if not ranked:
            conn.rollback()
            return []

        # Generate headlines only for the final results.
        results = []
        for r in ranked:
            desc = r[3] or ""
            body = r[5] or ""
            rank = float(r[8] or 0)
            has_title = int(r[9] or 0)
            has_url = int(r[10] or 0)
            score = (
                rank
                + self._TITLE_BOOST * has_title
                + self._URL_BOOST * has_url
                - self._BODY_ONLY_PENALTY * (1 - has_title)
                + self._RICHNESS_WEIGHT * min(max((r[6] or 0) / 5000.0, 0), 1.0)
                + self._RECENCY_WEIGHT * max(0.0, 1.0 - (now - (r[7] or 0)) / self._RECENCY_SPAN)
            )
            snippet = self._make_snippet(desc, body, query_words)
            results.append(SearchResult(
                url=r[1], title=r[2] or r[1], description=desc,
                host=r[4] or "", snippet=snippet, score=score,
            ))
        self._cache_results(cache_key, results)
        return results

    def _cache_results(self, key: str, results: list, ttl: int = 300) -> None:
        """Cache search results in Redis as JSON."""
        if not self._redis or not results:
            return
        try:
            data = json.dumps([
                {"url": r.url, "title": r.title, "description": r.description,
                 "host": r.host, "snippet": r.snippet, "score": r.score}
                for r in results
            ])
            self._redis.setex(key, ttl, data)
        except Exception:
            pass

    @staticmethod
    def _make_snippet(desc: str, body: str, query_words: list[str],
                      max_len: int = 220) -> str:
        """Build a snippet with <mark> around query terms.

        Prefer the description if it contains a query term; otherwise
        extract a window from the body around the first match.
        """
        if not query_words:
            return (desc or body)[:max_len]

        def highlight(text: str) -> str:
            """Wrap query-word matches in <mark> (case-insensitive)."""
            result = text
            for w in query_words:
                if not w or len(w) < 2:
                    continue
                pattern = re.compile(
                    r"\b(" + re.escape(w) + r"\w*)",
                    re.IGNORECASE,
                )
                result = pattern.sub(r"<mark>\1</mark>", result)
            return result

        # Try description first — it's usually cleaner.
        if desc:
            desc_lower = desc.lower()
            for w in query_words:
                if w and len(w) >= 2 and w.lower() in desc_lower:
                    return highlight(desc[:max_len])
            # No match in desc, but it's still useful as a summary.
            return desc[:max_len]

        # Fall back to body: find a window around the first match.
        if not body:
            return ""
        body_lower = body.lower()
        best_pos = -1
        for w in query_words:
            if not w or len(w) < 2:
                continue
            pos = body_lower.find(w.lower())
            if pos >= 0 and (best_pos < 0 or pos < best_pos):
                best_pos = pos
        if best_pos < 0:
            return body[:max_len]
        start = max(0, best_pos - 60)
        end = min(len(body), start + max_len)
        snippet = body[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(body):
            snippet = snippet + "…"
        return highlight(snippet)

    def _row_to_result(self, r) -> SearchResult:
        desc = r[2] or ""
        snippet = r[4] or desc[:220]
        score = r[5]
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        return SearchResult(
            url=r[0], title=r[1] or r[0], description=desc,
            host=r[3] or "", snippet=snippet, score=score,
        )

    def count_matches(self, query, sites: list[str] | None = None,
                      freshness: str = "") -> int:
        """Count documents matching a query (and optional site filter)."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        all_sites = list(sites or []) + list(parsed.sites)
        site_sql, site_params = self._site_clause(all_sites)
        fresh_sql, fresh_params = self._freshness_clause(freshness)

        # Check Redis cache.
        cache_key = f"count:{hashlib.md5(f'{fts}|{all_sites}|{parsed.intitle}|{parsed.inurl}|{freshness}'.encode()).hexdigest()}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached is not None:
                try:
                    return int(cached)
                except Exception:
                    pass

        if not fts:
            if not all_sites:
                return 0
            sql = ("SELECT COUNT(*) FROM documents d WHERE 1=1"
                   + site_sql + fresh_sql)
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, site_params + fresh_params)
                result = cur.fetchone()[0]
            if self._redis:
                try: self._redis.setex(cache_key, 300, result)
                except: pass
            return result

        # intitle/inurl filters for counting
        intitle_sql = ""
        intitle_params: list = []
        for w in parsed.intitle:
            intitle_sql += " AND d.title ILIKE %s"
            intitle_params.append(f"%{w}%")
        inurl_sql = ""
        inurl_params: list = []
        for w in parsed.inurl:
            inurl_sql += " AND d.url ILIKE %s"
            inurl_params.append(f"%{w}%")

        sql = ("SELECT COUNT(*) FROM documents d "
               "WHERE d.tsv @@ websearch_to_tsquery('english', %s)"
               + site_sql + intitle_sql + inurl_sql + fresh_sql)
        params = ([fts] + site_params + intitle_params + inurl_params
                  + fresh_params)
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = cur.fetchone()[0]
        except Exception:
            return 0
        if self._redis:
            try: self._redis.setex(cache_key, 300, result)
            except: pass
        return result

    # ----- rich page metadata ----------------------------------------------

    def set_page_meta(self, url: str, host: str, meta: dict,
                      headings: list[str] | None = None) -> None:
        """Store the Open Graph / JSON-LD metadata scraped for a page."""
        if not meta and not headings:
            return
        schema = meta.get("schema") or {}
        rating = meta.get("rating", schema.get("rating"))
        try:
            rating = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None
        try:
            rating_count = int(meta.get("rating_count",
                                        schema.get("rating_count", 0)) or 0)
        except (TypeError, ValueError):
            rating_count = 0

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO page_meta
                       (url, host, site_name, og_type, image, image_width,
                        image_height, image_alt, author, published, section,
                        rating, rating_count, price, price_currency, duration,
                        headings, payload, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s)
                       ON CONFLICT (url) DO UPDATE SET
                         host=EXCLUDED.host, site_name=EXCLUDED.site_name,
                         og_type=EXCLUDED.og_type, image=EXCLUDED.image,
                         image_width=EXCLUDED.image_width,
                         image_height=EXCLUDED.image_height,
                         image_alt=EXCLUDED.image_alt, author=EXCLUDED.author,
                         published=EXCLUDED.published, section=EXCLUDED.section,
                         rating=EXCLUDED.rating,
                         rating_count=EXCLUDED.rating_count,
                         price=EXCLUDED.price,
                         price_currency=EXCLUDED.price_currency,
                         duration=EXCLUDED.duration,
                         headings=EXCLUDED.headings, payload=EXCLUDED.payload,
                         updated_at=EXCLUDED.updated_at""",
                    (url[:2000], host, (meta.get("site_name") or "")[:200],
                     (meta.get("type") or "")[:60], (meta.get("image") or "")[:1000],
                     int(meta.get("image_width") or 0),
                     int(meta.get("image_height") or 0),
                     (meta.get("image_alt") or "")[:300],
                     (meta.get("author") or "")[:200],
                     (meta.get("published") or "")[:40],
                     (meta.get("section") or "")[:120],
                     rating, rating_count,
                     (str(meta.get("price") or ""))[:32],
                     (meta.get("price_currency") or "")[:8],
                     (str(meta.get("duration") or ""))[:32],
                     json.dumps((headings or [])[:8]),
                     json.dumps(meta)[:16000], time.time()),
                )
            conn.commit()
        except Exception:
            conn.rollback()

    def page_meta(self, urls: list[str]) -> dict[str, dict]:
        """Fetch stored metadata for a page of results, in one round trip."""
        if not urls:
            return {}
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT url, site_name, og_type, image, image_width,
                              image_height, image_alt, author, published,
                              section, rating, rating_count, price,
                              price_currency, duration, headings
                       FROM page_meta WHERE url = ANY(%s)""",
                    (list(urls),),
                )
                rows = cur.fetchall()
        except Exception:
            conn.rollback()
            return {}

        out: dict[str, dict] = {}
        for r in rows:
            try:
                headings = json.loads(r[15] or "[]")
            except ValueError:
                headings = []
            out[r[0]] = {
                "site_name": r[1] or "", "type": r[2] or "",
                "image": r[3] or "", "image_width": r[4] or 0,
                "image_height": r[5] or 0, "image_alt": r[6] or "",
                "author": r[7] or "", "published": r[8] or "",
                "section": r[9] or "", "rating": r[10],
                "rating_count": r[11] or 0, "price": r[12] or "",
                "price_currency": r[13] or "", "duration": r[14] or "",
                "headings": headings,
            }
        return out

    def page_meta_count(self) -> int:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM page_meta")
                return cur.fetchone()[0]
        except Exception:
            conn.rollback()
            return 0

    # ----- opt-out / removals ----------------------------------------------

    def blocked_hosts(self) -> set[str]:
        """Hosts that opted out. Cached in Redis — this is read on every crawl
        decision and every search, so it must not be a database round trip."""
        if self._redis:
            cached = self._redis.get("blocked:hosts")
            if cached is not None:
                try:
                    return set(json.loads(cached))
                except ValueError:
                    pass
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT host FROM blocked_hosts")
                hosts = {r[0] for r in cur.fetchall()}
        except Exception:
            conn.rollback()
            return set()
        if self._redis:
            try:
                self._redis.setex("blocked:hosts", 300, json.dumps(sorted(hosts)))
            except Exception:
                pass
        return hosts

    def is_blocked(self, host: str) -> bool:
        host = (host or "").lower().lstrip(".")
        if not host:
            return False
        blocked = self.blocked_hosts()
        if host in blocked:
            return True
        # A block on example.com also covers www.example.com and sub.example.com.
        parts = host.split(".")
        return any(".".join(parts[i:]) in blocked for i in range(1, len(parts) - 1))

    def block_host(self, host: str, reason: str = "") -> None:
        host = (host or "").lower().strip().lstrip(".")
        if not host:
            return
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO blocked_hosts (host, reason, created_at)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (host) DO UPDATE SET reason=EXCLUDED.reason""",
                    (host, reason[:500], time.time()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if self._redis:
            try:
                self._redis.delete("blocked:hosts")
            except Exception:
                pass

    def purge_host(self, host: str) -> int:
        """Delete everything indexed for a host. Used when an opt-out is
        verified — the point of a removal request is that the data goes."""
        host = (host or "").lower().strip()
        if not host:
            return 0
        pattern = f"%{host}"
        removed = 0
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                for table in ("documents", "images", "videos"):
                    cur.execute(
                        f"DELETE FROM {table} WHERE host = %s OR host LIKE %s",
                        (host, pattern),
                    )
                    removed += cur.rowcount or 0
                cur.execute(
                    "DELETE FROM page_meta WHERE host = %s OR host LIKE %s",
                    (host, pattern),
                )
                cur.execute("DELETE FROM favicons WHERE host = %s", (host,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return removed

    def add_optout_request(self, host: str, email: str, scope: str,
                           note: str) -> int:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO optout_requests
                       (host, email, scope, note, status, created_at)
                       VALUES (%s, %s, %s, %s, 'received', %s) RETURNING id""",
                    (host[:255], email[:255], scope[:40], note[:2000],
                     time.time()),
                )
                request_id = cur.fetchone()[0]
            conn.commit()
            return request_id
        except Exception:
            conn.rollback()
            raise

    # ----- autocomplete ----------------------------------------------------

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """Prefix-matched completions drawn from indexed page titles.

        Deliberately *not* built from what people search for: SerikaSearch
        keeps no query log, so suggestions come from the corpus instead.
        """
        prefix = re.sub(r"\s+", " ", (prefix or "").strip().lower())
        if len(prefix) < 2 or len(prefix) > 60:
            return []

        cache_key = f"sugg:{prefix}"
        if self._redis:
            cached = self._redis.get(cache_key)
            if cached is not None:
                try:
                    return json.loads(cached)
                except ValueError:
                    pass

        conn = self._get_conn()
        rows: list[tuple[str, int]] = []
        try:
            with conn.cursor() as cur:
                # Titles that literally start with what's been typed.
                cur.execute(
                    """SELECT title, word_count FROM documents
                       WHERE lower(title) LIKE %s AND title <> ''
                       ORDER BY word_count DESC NULLS LAST LIMIT 40""",
                    (prefix + "%",),
                )
                rows = list(cur.fetchall())
                if len(rows) < limit:
                    # Then titles that contain the phrase anywhere.
                    cur.execute(
                        """SELECT title, word_count FROM documents
                           WHERE lower(title) LIKE %s AND title <> ''
                           ORDER BY word_count DESC NULLS LAST LIMIT 40""",
                        ("% " + prefix + "%",),
                    )
                    rows += list(cur.fetchall())
        except Exception:
            conn.rollback()
            return []

        suggestions: list[str] = []
        seen: set[str] = set()
        for title, _ in rows:
            phrase = _suggestion_phrase(title, prefix)
            if not phrase:
                continue
            key = phrase.lower()
            if key in seen or key == prefix:
                continue
            seen.add(key)
            suggestions.append(phrase)
            if len(suggestions) >= limit:
                break

        if self._redis:
            try:
                self._redis.setex(cache_key, 900, json.dumps(suggestions))
            except Exception:
                pass
        return suggestions

    def vocabulary(self, limit: int = 20000) -> dict[str, int]:
        """Word frequencies from the corpus, for spelling correction."""
        if self._redis:
            cached = self._redis.get("vocab:v1")
            if cached:
                try:
                    return json.loads(cached)
                except ValueError:
                    pass
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT title FROM documents
                       WHERE title <> '' ORDER BY word_count DESC NULLS LAST
                       LIMIT 8000"""
                )
                titles = [r[0] for r in cur.fetchall()]
        except Exception:
            conn.rollback()
            return {}

        counts: dict[str, int] = {}
        for title in titles:
            for word in re.findall(r"[a-z]{3,20}", title.lower()):
                counts[word] = counts.get(word, 0) + 1
        if len(counts) > limit:
            counts = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:limit])
        if self._redis:
            try:
                self._redis.setex("vocab:v1", 3600, json.dumps(counts))
            except Exception:
                pass
        return counts

    def top_hosts(self, limit: int = 12) -> list[tuple[str, int]]:
        return self.hosts()[:limit]

    def recent_pages(self, limit: int = 12) -> list[SearchResult]:
        """The most recently crawled pages — used on the home page."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT url, title, description, host FROM documents
                       WHERE title <> '' AND word_count > 120
                       ORDER BY fetched_at DESC NULLS LAST LIMIT %s""",
                    (limit,),
                )
                return [
                    SearchResult(url=r[0], title=r[1] or r[0],
                                 description=r[2] or "", host=r[3] or "",
                                 snippet=(r[2] or "")[:160], score=0.0)
                    for r in cur.fetchall()
                ]
        except Exception:
            conn.rollback()
            return []

    # ----- cleanup ---------------------------------------------------------

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
        # Close all pooled connections.
        try:
            self._pool.closeall()
        except Exception:
            pass
