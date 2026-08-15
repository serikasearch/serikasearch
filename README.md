# SerikaSearch

A self-hosted search engine with a 24/7 crawler, PostgreSQL full-text search, Redis-backed frontier, and an AMOLED-black, purple-accented web UI.

## Structure

```
SerikaSearch/
  serika/
    __main__.py        # CLI: serve, crawl, loop, search, stats
    core/
      db.py            # PostgreSQL + Redis backend
      crawler.py       # Polite robots-respecting crawler
      parser.py        # HTML parser (images, videos, links)
      query.py         # Google-style query parser
      robots.py        # robots.txt cache
      knowledge.py     # Wikipedia knowledge panels
    web/
      server.py        # HTTP server + API
      templates.py     # Template engine
  html/                # Templates (home, search, images, components)
  static/              # CSS, JS
  seeds/categories/    # Seed URLs by category
  Dockerfile.web       # Web app container
  Dockerfile.crawler   # 24/7 crawler container
  requirements.txt
```

## Quick start (local)

```bash
pip install -r requirements.txt

# Create serika/config.json with your database URLs:
# {
#   "database_url": "postgres://user:pass@host:port/db",
#   "redis_url": "redis://:pass@host:port/0"
# }

# Start the search UI
python -m serika serve --host 0.0.0.0 --port 8000

# Run a crawl batch
python -m serika crawl seeds/categories/top-anime.txt --max-pages 5000

# Run the crawler 24/7
python -m serika loop --workers 12 --delay 0.6
```

## Deployment (Coolify / Docker)

Two containers from the same repo:

- **Web app**: builds with `Dockerfile.web`, serves on port 8000
- **Crawler**: builds with `Dockerfile.crawler`, runs `loop` mode 24/7

Both need `DATABASE_URL` and `REDIS_URL` environment variables.

## Features

- **Web search** with `site:`, `-term`, `intitle:`, `inurl:`, `"phrase"`
- **Image search** with dense grid, size filters, sidebar lightbox
- **Video search** with embedded player pop-out (YouTube, Vimeo, etc.)
- **Knowledge panels** powered by Wikipedia
- **JSON API**: `/api/search`, `/api/images`, `/api/videos`, `/api/stats`
- **Public resources**: `/robots.txt`, `/llms.txt`
- **PostgreSQL** with weighted tsvector + GIN indexes
- **Redis** for search caching and crawl frontier
- **24/7 crawler** with `loop` mode — re-crawls seeds, discovers new sites

## API

| Endpoint | Description |
|---|---|
| `GET /api/search?q=...&limit=...&page=...` | Web search |
| `GET /api/images?q=...&limit=...&page=...` | Image search |
| `GET /api/videos?q=...&limit=...&page=...` | Video search |
| `GET /api/stats` | Index statistics |
| `GET /robots.txt` | Crawler directives |
| `GET /llms.txt` | Machine-readable description |

## License

MIT
