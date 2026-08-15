"""Command-line interface for SerikaSearch.

  python -m serika serve                      start the search UI
  python -m serika crawl <seedfile> ...       run a crawl batch
  python -m serika loop <seedfile> ...        run forever, refilling seeds
  python -m serika search <query>             search from the terminal
  python -m serika images <query>             image search from the terminal
  python -m serika stats                      index statistics
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time

from . import __version__
from .core.db import Index
from .core.crawler import Crawler, backfill_favicons
from .web.server import serve


def _category_from_filename(path: str) -> str:
    """seeds/top-anime.txt -> 'anime', seeds/top.art.txt -> 'art'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"^top[-_.]", "", stem).strip().lower()


def _read_seed_file(path: str) -> list[str]:
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def _load_seed_groups(targets: list[str], category: str = "") -> list[tuple[str, list[str]]]:
    """Parse targets into (category, urls) groups."""
    groups: list[tuple[str, list[str]]] = []
    direct_urls: list[str] = []
    seed_files: list[str] = []
    for target in targets:
        if os.path.isfile(target) or glob.has_magic(target):
            seed_files.extend(sorted(glob.glob(target)) or [target])
        else:
            direct_urls.append(target)
    if direct_urls:
        groups.append((category, direct_urls))
    for path in seed_files:
        if not os.path.isfile(path):
            print(f"seed file not found: {path}", file=sys.stderr)
            sys.exit(2)
        cat = category or _category_from_filename(path)
        groups.append((cat, _read_seed_file(path)))
    return groups


def _load_all_seeds(seed_dir: str = "seeds/categories") -> list[tuple[str, list[str]]]:
    """Load all seed files from the default directory."""
    groups = []
    if os.path.isdir(seed_dir):
        for fname in sorted(os.listdir(seed_dir)):
            if fname.endswith(".txt"):
                path = os.path.join(seed_dir, fname)
                cat = _category_from_filename(path)
                groups.append((cat, _read_seed_file(path)))
    return groups


# ----- serve -------------------------------------------------------------

def cmd_serve(args) -> int:
    serve(index_path=args.db, host=args.host, port=args.port)
    return 0


# ----- crawl -------------------------------------------------------------

def cmd_crawl(args) -> int:
    index = Index(args.db)
    groups = _load_seed_groups(args.targets, args.category)

    if not groups and index.frontier_pending() == 0:
        print("Nothing to crawl. Pass URLs or a seed file, e.g.\n"
              "  python -m serika crawl seeds/categories/top-anime.txt",
              file=sys.stderr)
        return 2

    if not groups:
        groups = [(args.category, [])]

    total = 0
    for category, urls in groups:
        crawler = Crawler(
            index=index,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            per_host_cap=args.per_host,
            same_host_only=args.same_host,
            default_delay=args.delay,
            timeout=args.timeout,
            workers=args.workers,
            category=category,
            want_images=not args.no_images,
            want_favicons=not args.no_favicons,
            verbose=not args.quiet,
        )
        if urls:
            crawler.add_seeds(urls)
        total += crawler.crawl()

    print(f"\nIndex now holds {index.document_count():,} pages, "
          f"{index.image_count():,} images, {index.video_count():,} videos.")
    print(f"Frontier: {index.frontier_pending():,} pending.")
    index.close()
    return 0


def cmd_loop(args) -> int:
    """Run the crawler forever in a loop, refilling seeds when the frontier empties."""
    index = Index(args.db)
    groups = _load_seed_groups(args.targets, args.category)
    if not groups:
        groups = _load_all_seeds()

    if not groups:
        print("No seeds found. Pass seed files or create seeds/categories/*.txt",
              file=sys.stderr)
        return 2

    print(f"SerikaCrawler 24/7 loop — {len(groups)} seed groups, "
          f"{sum(len(u) for _, u in groups)} seed URLs", file=sys.stderr)
    print(f"Frontier: {index.frontier_pending():,} pending", file=sys.stderr)

    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Cycle {cycle} — {time.strftime('%Y-%m-%d %H:%M:%S')}",
              file=sys.stderr)
        print(f"Frontier: {index.frontier_pending():,} pending", file=sys.stderr)
        print(f"Index: {index.document_count():,} pages, "
              f"{index.image_count():,} images, "
              f"{index.video_count():,} videos", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # Re-add seeds each cycle so new pages on known sites get picked up.
        for category, urls in groups:
            index.add_links(urls, depth=0, category=category)

        # Crawl until the frontier is exhausted (or budget hit).
        for category, urls in groups:
            crawler = Crawler(
                index=index,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                per_host_cap=args.per_host,
                same_host_only=args.same_host,
                default_delay=args.delay,
                timeout=args.timeout,
                workers=args.workers,
                category=category,
                want_images=not args.no_images,
                want_favicons=not args.no_favicons,
                verbose=not args.quiet,
            )
            if urls:
                crawler.add_seeds(urls)
            crawler.crawl()

        print(f"\nCycle {cycle} complete. Frontier: "
              f"{index.frontier_pending():,} pending", file=sys.stderr)

        if index.frontier_pending() == 0:
            print("Frontier exhausted. Waiting before next cycle...",
                  file=sys.stderr)
            time.sleep(args.rest_time)
        else:
            time.sleep(min(args.rest_time, 30))

    index.close()
    return 0


# ----- search ------------------------------------------------------------

def cmd_search(args) -> int:
    index = Index(args.db)
    query = " ".join(args.query)
    total = index.count_matches(query, args.category)
    results = index.search(query, args.limit, 0, args.category)
    print(f"\n{total:,} result(s) for: {query}\n")
    for i, r in enumerate(results, 1):
        snippet = r.snippet.replace("<mark>", "\033[35m").replace("</mark>", "\033[0m")
        print(f"\033[1m{i}. {r.title[:100]}\033[0m")
        print(f"   \033[90m{r.url}\033[0m")
        print(f"   {snippet}\n")
    index.close()
    return 0


def cmd_images(args) -> int:
    index = Index(args.db)
    query = " ".join(args.query)
    total = index.count_image_matches(query, args.category)
    results = index.search_images(query, args.limit, 0, args.category)
    print(f"\n{total:,} image(s) for: {query}\n")
    for i, r in enumerate(results, 1):
        print(f"\033[1m{i}. {(r.alt or r.page_title)[:90]}\033[0m")
        print(f"   \033[35m{r.src}\033[0m")
        print(f"   \033[90mon {r.page_url}\033[0m\n")
    index.close()
    return 0


def cmd_icons(args) -> int:
    index = Index(args.db)
    print("Fetching favicons for indexed hosts…", file=sys.stderr)
    n = backfill_favicons(index, timeout=args.timeout)
    print(f"Fetched {n} new favicon(s); {index.favicon_count()} cached in total.")
    index.close()
    return 0


def cmd_stats(args) -> int:
    index = Index(args.db)
    print(f"SerikaSearch index")
    print(f"  Pages     : {index.document_count():,}")
    print(f"  Images    : {index.image_count():,}")
    print(f"  Videos    : {index.video_count():,}")
    print(f"  Favicons  : {index.favicon_count():,}")
    print(f"  Sites     : {len(index.hosts()):,}")
    print(f"  Frontier  : {index.frontier_pending():,} pending")
    cats = index.categories()
    if cats:
        print("  Categories:")
        for cat, n in cats:
            print(f"    {n:>6,}  {cat}")
    print("  Top sites:")
    for host, n in index.hosts()[:15]:
        print(f"    {n:>6,}  {host}")
    index.close()
    return 0


# ----- CLI ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="serika",
        description="SerikaSearch — a self-hosted search engine with crawler.",
    )
    p.add_argument("--version", action="version", version=f"SerikaSearch {__version__}")
    p.add_argument("--db", default="", help="unused with PostgreSQL; kept for compat")
    sub = p.add_subparsers(dest="command", required=True)

    # serve
    s = sub.add_parser("serve", help="start the search web UI")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    # crawl
    c = sub.add_parser("crawl", help="run a single crawl batch")
    c.add_argument("targets", nargs="*", help="seed URLs and/or seed files")
    c.add_argument("--seed-file", action="append", help="seed file or glob; repeatable")
    c.add_argument("--category", default="", help="tag pages with this category")
    c.add_argument("--max-pages", type=int, default=5000, help="page budget per group")
    c.add_argument("--max-depth", type=int, default=3, help="max link depth")
    c.add_argument("--per-host", type=int, default=40, help="max pages per host")
    c.add_argument("--workers", type=int, default=12, help="parallel workers")
    c.add_argument("--same-host", action="store_true", help="never leave seed hosts")
    c.add_argument("--delay", type=float, default=1.0, help="crawl-delay default")
    c.add_argument("--timeout", type=float, default=15.0, help="request timeout")
    c.add_argument("--no-images", action="store_true", help="skip image indexing")
    c.add_argument("--no-favicons", action="store_true", help="skip favicon fetching")
    c.add_argument("--quiet", action="store_true", help="suppress per-page logging")
    c.set_defaults(func=cmd_crawl)

    # loop (24/7)
    lp = sub.add_parser("loop", help="run forever, refilling seeds each cycle")
    lp.add_argument("targets", nargs="*", help="seed files to reload each cycle")
    lp.add_argument("--category", default="", help="default category for direct URLs")
    lp.add_argument("--max-pages", type=int, default=10000, help="page budget per cycle")
    lp.add_argument("--max-depth", type=int, default=3, help="max link depth")
    lp.add_argument("--per-host", type=int, default=40, help="max pages per host")
    lp.add_argument("--workers", type=int, default=12, help="parallel workers")
    lp.add_argument("--same-host", action="store_true", help="never leave seed hosts")
    lp.add_argument("--delay", type=float, default=1.0, help="crawl-delay default")
    lp.add_argument("--timeout", type=float, default=15.0, help="request timeout")
    lp.add_argument("--rest-time", type=float, default=300.0,
                    help="seconds to wait when frontier is empty (default: 300)")
    lp.add_argument("--no-images", action="store_true", help="skip image indexing")
    lp.add_argument("--no-favicons", action="store_true", help="skip favicon fetching")
    lp.add_argument("--quiet", action="store_true", help="suppress per-page logging")
    lp.set_defaults(func=cmd_loop)

    # search
    q = sub.add_parser("search", help="web search from the terminal")
    q.add_argument("query", nargs="+")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--category", default="")
    q.set_defaults(func=cmd_search)

    # images
    im = sub.add_parser("images", help="image search from the terminal")
    im.add_argument("query", nargs="+")
    im.add_argument("--limit", type=int, default=10)
    im.add_argument("--category", default="")
    im.set_defaults(func=cmd_images)

    # icons
    ic = sub.add_parser("icons", help="backfill favicons for indexed hosts")
    ic.add_argument("--timeout", type=float, default=10.0)
    ic.set_defaults(func=cmd_icons)

    # stats
    st = sub.add_parser("stats", help="show index statistics")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
