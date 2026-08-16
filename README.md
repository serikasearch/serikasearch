# SerikaSearch

An independent search engine: its own 24/7 crawler, its own PostgreSQL index,
its own ranking. Not a skin over somebody else's API. AMOLED-black UI, a purple
accent, no ads, no accounts, no tracking, and no generated answers.

## Structure

```
SerikaSearch/
  serika/
    __main__.py        # CLI: serve, crawl, loop, search, images, enrich, block, icons, stats
    core/
      db.py            # PostgreSQL + Redis backend, pooling, opt-out, suggestions
      crawler.py       # Polite robots-respecting crawler
      parser.py        # HTML parser (text, images, videos, links, headings)
      unfurl.py        # Open Graph / Twitter card / JSON-LD / oEmbed, + safe fetcher
      query.py         # Google-style query parser
      robots.py        # robots.txt cache
      reference.py     # Knowledge panel + dictionary (Wikipedia, Grokipedia, …)
      suggest.py       # Autocomplete, spelling correction, related searches
      bangs.py         # !bang redirects
    tools/             # Instant answers
      calc.py          #   safe expression evaluator
      units.py         #   unit conversion tables
      convert.py       #   colours, bases, Roman numerals, encodings, hashes
      timely.py        #   world clock, date arithmetic, timestamps
      generate.py      #   passwords, UUIDs, dice, lorem, text stats
      live.py          #   currency + weather (cached, keyless APIs)
      qr.py            #   dependency-free QR encoder
    web/
      server.py        # HTTP routing, JSON API, security headers
      render.py        # HTML rendering for every component
      pages.py         # Legal / policy / help page content
      templates.py     # Tiny include + variable template engine
  html/                # Templates
  static/              # CSS, JS, icon
  seeds/categories/    # Seed URLs by category
```

## Quick start

```bash
pip install -r requirements.txt
```

Create `serika/config.json` with your database URLs (or set `DATABASE_URL` and
`REDIS_URL` in the environment, which take priority):

```json
{
  "database_url": "postgres://user:pass@host:port/db",
  "redis_url": "redis://:pass@host:port/0"
}
```

Then:

```bash
python -m serika serve --host 0.0.0.0 --port 8000
```

```bash
python -m serika crawl seeds/categories/top-anime.txt --max-pages 5000
```

```bash
python -m serika loop --workers 50 --delay 0.6
```

## Features

### Search
- **Web, image and video search** over a locally built index
- **Operators**: `site:`, `-term`, `intitle:`, `inurl:`, `"exact phrase"`
- **Justified image grid** — rows packed to a target height and flush to the
  container edge, with infinite scroll and a swipeable lightbox
- **Rich results** — preview image, publish date, byline, rating and price,
  read from each page's own Open Graph and JSON-LD metadata
- **Knowledge panel** with a source switcher: Wikipedia, Simple Wikipedia,
  Grokipedia, Wiktionary, or a reference page already in the index
- **Autocomplete** built from indexed page titles — not from a query log,
  because there isn't one
- **Did you mean** spelling correction and **related searches**, both mined
  from the corpus
- **!bang shortcuts** — `!w`, `!gh`, `!yt` and ~40 more
- **Indexed-within filter**, and an advanced-search query builder

### Instant answers
Thirty-odd tools that answer in the results page. Most need no network at all.

| Type | Get |
|---|---|
| `1+1`, `sqrt(144)`, `20% of 80` | Calculator (AST-parsed, never `eval`) |
| `5 km to miles`, `180 f in c` | Unit conversion across 13 dimensions |
| `100 usd to eur` | Live ECB reference rates |
| `weather in tokyo`, `sunrise in tokyo` | Conditions, 6-day forecast, sun times |
| `time in tokyo` | World clock with DST |
| `days until christmas` | Date arithmetic, age, weekday |
| `define serendipity` | Dictionary with audio, synonyms, etymology |
| `thank you in japanese` | Offline phrasebook, 8 languages (no API) |
| `#a274ff`, `colour picker` | Colour conversion + interactive picker |
| `sha256 hello`, `base64 encode hi` | Hashes and encodings |
| `qr code for …` | QR code (own encoder, no dependency) |
| `generate me a password`, `uuid` | Cryptographically random generators |
| `roll 2d6`, `flip a coin` | Random |
| `255 in binary`, `MCMXCIV` | Bases and Roman numerals |
| `unix timestamp`, `word count: …` | Developer & text tools |
| `bmi 180cm 75kg` | BMI with a banded meter |
| `split bill 120 by 3 at 18%` | Bill/tip splitter |
| `anagram listen` | Anagram / word unscrambler |
| `morse code hello` | Morse encode/decode with audio |
| `carry on size ryanair` | Cabin-bag limits for 28 airlines |
| `anime schedule` | Upcoming anime episodes (AniList) |
| `where to watch inception` | Streaming availability by country (JustWatch) |
| `taylor swift discography` | Artist genres, albums, links (MusicBrainz) |
| `take home pay 60000 uk` | Income-tax & net-pay estimate, 7 countries |
| `track flight BA2490` | Live aircraft position/altitude/speed (OpenSky) |
| `I want to eat food in japanese` | Offline translation of any sentence |

**Live data, all keyless and free.** Weather (Open-Meteo), currency (ECB via
Frankfurter), anime (AniList GraphQL), where-to-watch (JustWatch, with TVmaze
for TV networks), and artists (MusicBrainz + honest tour/ticket links, since no
concert API is free and keyless). Every one is cached so a popular query hits
the upstream once.

**Interactive widgets** (live client components — Web Audio, canvas-free,
all behaviour in `app.js` under a strict CSP): `stopwatch`, `metronome`,
`white noise`, `periodic table`, `font preview`, `colour picker`,
`recipe converter` (fraction-aware scaling + dietary swaps),
`meeting planner` (overlapping working hours across time zones), and
`scale of universe` — a logarithmic zoom from the Planck length to the
observable universe.

**A note on translation.** Fluent neural MT needs either an API or a
multi-megabyte on-device model, neither of which fits a dependency-free server
that makes no external requests. So `serika/tools/translate.py` is a bundled
**bilingual dictionary** across eight languages: exact-phrase lookup for common
phrases (fluent), and a word-by-word gloss for *any* other sentence, with the
untranslated words marked. It's offline and it works on arbitrary input — it is
literal, not fluent, and the UI says so.

**Money & travel.** `serika/tools/tax.py` estimates net pay from bundled 2025
tax brackets for the US, UK, Canada, Australia, Ireland, Germany and the
Netherlands (an estimate — it excludes what genuinely can't be guessed, and
says so). `serika/tools/flights.py` tracks any flight live from the OpenSky
Network's keyless ADS-B feed — real position, altitude and speed, the
genuinely-free slice of flight data (scheduled times and prices need a paid
API).

Full list at `/tools`; each has its own page.

### Privacy
- No query log, no accounts, no cookies, no third-party JavaScript
- Favicons are cached locally so result pages fetch no third-party icons
- The requests that *do* leave the site are documented honestly at
  `/privacy#third-parties` — image thumbnails are hot-linked from their origin
  sites, and that is disclosed rather than glossed over
- Site owners can opt out via robots.txt or the form at `/how-to-opt-out`

## API

Keyless, rate-limited, CORS-enabled. Full docs at `/api-docs`.

| Endpoint | Description |
|---|---|
| `GET /api/search?q=&limit=&page=&when=` | Web search, with rich metadata |
| `GET /api/images?q=&limit=&page=&size=` | Image search |
| `GET /api/videos?q=&limit=&page=` | Video search |
| `GET /api/suggest?q=` | Autocomplete |
| `GET /api/answer?q=` | Instant answer only |
| `GET /api/define?w=` | Dictionary entry |
| `GET /api/unfurl?url=` | Open Graph / oEmbed for one URL |
| `GET /api/similar?src=&page=&host=` | Related images |
| `GET /api/stats` | Index statistics |

Also served: `/robots.txt`, `/llms.txt`, `/opensearch.xml`, `/sitemap.xml`,
`/manifest.webmanifest`, `/healthz`.

## Operating notes

- **Connection pool.** Each process opens at most `DB_POOL_MAX` connections
  (default 16, floor `DB_POOL_MIN`, default 2). Web and crawler containers each
  get their own pool, so keep the total under the database's `max_connections`.
- **Rich metadata backfill.** Pages crawled before Open Graph extraction existed
  have no preview data. `python -m serika enrich --limit 2000` fills them in by
  re-fetching each page's `<head>`.
- **Honouring an opt-out.** `python -m serika block example.com --purge` blocks
  future crawls and deletes what is already indexed.
- **Before going live**, fill in the `OPERATOR` block at the top of
  `serika/web/pages.py` — contact addresses, jurisdiction, and the crawler's
  public URL all appear in the legal pages. The policy text describes what this
  software actually does, but it is not legal advice; have it reviewed.

## Deployment (Coolify / Docker)

Two containers from the same repo:

- **Web app** — `Dockerfile.web`, serves on port 8000
- **Crawler** — `Dockerfile.crawler`, runs `loop` mode 24/7

Both need `DATABASE_URL` and `REDIS_URL`.

## License

MIT
