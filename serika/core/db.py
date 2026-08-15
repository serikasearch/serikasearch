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

        self._pool = psycopg2.pool.ThreadedConnectionPool(
            1, 20, db_url,
        )
        self._local = threading.local()
        self._write_lock = threading.RLock()

        # Redis for frontier + caching.
        self._redis: Optional[redis.Redis] = None
        if redis_url:
            self._redis = redis.from_url(redis_url, decode_responses=True)

        self._init_schema()

    # ----- connection management -------------------------------------------

    def _get_conn(self):
        """Get a connection from the pool (thread-local)."""
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = self._pool.getconn()
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
        with self._write_lock:
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
            try: self._redis.setex("stats:doc_count", 120, result)
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
        with self._write_lock:
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
        with self._write_lock:
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
        with self._write_lock:
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

    def search(
        self, query, limit: int = 10, offset: int = 0
    ) -> list[SearchResult]:
        """Full-text web search with Google-style operators and SEO ranking."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        site_sql, site_params = self._site_clause(parsed.sites)

        # Check Redis cache first.
        cache_key = f"search:{hashlib.md5(f'{fts}|{parsed.sites}|{parsed.intitle}|{parsed.inurl}|{limit}|{offset}'.encode()).hexdigest()}"
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
                "FROM documents d WHERE 1=1" + site_sql +
                " ORDER BY d.word_count DESC NULLS LAST, d.fetched_at DESC "
                "LIMIT %s OFFSET %s"
            )
            params = site_params + [limit, offset]
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
            + site_sql + intitle_sql + inurl_sql +
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

    def count_matches(self, query, sites: list[str] | None = None) -> int:
        """Count documents matching a query (and optional site filter)."""
        parsed = query if isinstance(query, ParsedQuery) else parse(query)
        fts = parsed.fts
        all_sites = list(sites or []) + list(parsed.sites)
        site_sql, site_params = self._site_clause(all_sites)

        # Check Redis cache.
        cache_key = f"count:{hashlib.md5(f'{fts}|{all_sites}|{parsed.intitle}|{parsed.inurl}'.encode()).hexdigest()}"
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
            sql = "SELECT COUNT(*) FROM documents d WHERE 1=1" + site_sql
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, site_params)
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
               + site_sql + intitle_sql + inurl_sql)
        params = [fts] + site_params + intitle_params + inurl_params
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
