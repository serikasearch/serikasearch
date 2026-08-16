"""Instant answers — the strip above the results.

Typing ``1+1``, ``5 km to miles``, ``weather in tokyo`` or ``#a274ff`` into a
search box should answer the question outright rather than hand back ten links
about it. :func:`resolve` runs a query past every tool in turn and returns the
first :class:`Answer` that matches, or ``None`` so the caller renders ordinary
results.

Order is deliberate: narrow, unambiguous patterns (a colour code, a currency
pair) run before greedy ones (the calculator), so ``100 usd to eur`` is never
mistaken for arithmetic. Every matcher is expected to return ``None`` quickly
for queries that aren't its business — most searches are ordinary searches and
must not pay for this.

Answers carry structured data, never HTML; the web layer renders them. That
keeps this module usable from the CLI and testable without a server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import (anime, calc, convert, generate, live, luggage, qr, recipe,
               stream, timely, translate, units, universe, widgets)

__all__ = ["Answer", "resolve", "TOOLS", "tool_by_slug"]


@dataclass
class Answer:
    """A rendered-by-the-caller instant answer."""
    kind: str                                    # drives the CSS + template
    title: str                                   # the answer itself, set large
    subtitle: str = ""                           # how we read the query
    detail: str = ""                             # supporting line
    rows: list[tuple[str, str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    source: str = ""
    source_url: str = ""
    tool: str = ""                               # slug of the matching tool page
    copy_value: str = ""                         # what a copy button copies
    data: dict = field(default_factory=dict)     # kind-specific extras


# --------------------------------------------------------------------------- #
# individual matchers
# --------------------------------------------------------------------------- #

def _calculator(query: str, ctx: dict) -> Optional[Answer]:
    if not calc.looks_like_math(query):
        return None
    try:
        result = calc.evaluate(query)
    except calc.CalcError:
        return None
    value = calc.format_number(result.value)
    rows = []
    number = result.value
    if isinstance(number, float) and not number.is_integer():
        rows.append(("Rounded", calc.format_number(round(number, 2))))
    if isinstance(number, (int, float)) and float(number).is_integer():
        as_int = int(number)
        if 0 <= as_int < 2 ** 64:
            rows.append(("Hexadecimal", f"0x{as_int:X}"))
            rows.append(("Binary", f"0b{as_int:b}"))
    return Answer(
        kind="calc", title=value,
        subtitle=result.expression if result.expression != query.strip() else "",
        detail=result.note, rows=rows, tool="calculator", copy_value=value,
        data={"expression": result.expression},
    )


def _units(query: str, ctx: dict) -> Optional[Answer]:
    conversion = units.parse_conversion(query)
    if conversion is None:
        return None
    left = f"{units.format_value(conversion.value)} {conversion.from_unit.symbol}"
    right = f"{units.format_value(conversion.result)} {conversion.to_unit.symbol}"
    inverse = units.convert(1.0, conversion.to_unit.key,
                            conversion.from_unit.key)
    return Answer(
        kind="convert", title=right, subtitle=f"{left} =",
        detail=f"{units.DIMENSION_LABELS.get(conversion.dimension, '')} "
               f"· {conversion.from_unit.plural} → {conversion.to_unit.plural}",
        rows=[
            ("Rate", f"1 {conversion.from_unit.symbol} = "
                     f"{units.format_value(units.convert(1.0, conversion.from_unit.key, conversion.to_unit.key).result)} "
                     f"{conversion.to_unit.symbol}"),
            ("Inverse", f"1 {conversion.to_unit.symbol} = "
                        f"{units.format_value(inverse.result)} "
                        f"{conversion.from_unit.symbol}"),
        ],
        tool="unit-converter", copy_value=units.format_value(conversion.result),
        data={
            "dimension": conversion.dimension,
            "from": conversion.from_unit.symbol,
            "to": conversion.to_unit.symbol,
            "value": conversion.value,
        },
    )


def _currency(query: str, ctx: dict) -> Optional[Answer]:
    result = live.parse_currency(query)
    if result is None:
        return None
    formatted = f"{result.result:,.2f}"
    return Answer(
        kind="currency",
        title=f"{result.to_symbol}{formatted}",
        subtitle=f"{result.from_symbol}{result.amount:,.2f} "
                 f"{result.from_code} =",
        detail=f"1 {result.from_code} = {result.rate:,.4f} {result.to_code} "
               f"· European Central Bank reference rate, {result.date}",
        rows=[
            (f"1 {result.from_code}", f"{result.rate:,.4f} {result.to_code}"),
            (f"1 {result.to_code}",
             f"{1 / result.rate:,.4f} {result.from_code}" if result.rate else "—"),
        ],
        source="Frankfurter / ECB", source_url="https://www.frankfurter.app/",
        tool="currency", copy_value=formatted,
        data={"from": result.from_code, "to": result.to_code,
              "amount": result.amount, "rate": result.rate},
    )


def _weather(query: str, ctx: dict) -> Optional[Answer]:
    result = live.parse_weather(query)
    if result is None:
        return None
    return Answer(
        kind="weather",
        title=f"{result.temperature_c:g}°C",
        subtitle=f"{result.place}{', ' + result.country if result.country else ''}",
        detail=f"{result.label} · feels like {result.feels_like_c:g}°C",
        rows=[
            ("Feels like", f"{result.feels_like_c:g}°C / "
                           f"{result.feels_like_f:.0f}°F"),
            ("Humidity", f"{result.humidity}%"),
            ("Wind", f"{result.wind_kmh:g} km/h"),
            ("Precipitation", f"{result.precipitation:g} mm"),
        ],
        source="Open-Meteo", source_url="https://open-meteo.com/",
        tool="weather",
        data={
            "fahrenheit": f"{result.temperature_f:.0f}",
            "icon": result.icon,
            "is_day": result.is_day,
            "days": [
                {"weekday": d.weekday, "high": d.high_c, "low": d.low_c,
                 "icon": d.icon, "label": d.label, "rain": d.precipitation}
                for d in result.days
            ],
        },
    )


def _world_time(query: str, ctx: dict) -> Optional[Answer]:
    result = timely.parse_time_query(query)
    if result is None:
        return None
    return Answer(
        kind="time", title=result.clock, subtitle=result.place,
        detail=f"{result.day} · {result.offset}"
               f"{' ' + result.abbreviation if result.abbreviation else ''}",
        rows=[
            ("12-hour", result.clock_12),
            ("Time zone", result.zone),
            ("UTC offset", result.offset),
            ("Daylight saving", "In effect" if result.is_dst else "Not in effect"),
        ],
        tool="world-clock", copy_value=result.clock,
        data={"iso": result.iso, "zone": result.zone},
    )


def _dates(query: str, ctx: dict) -> Optional[Answer]:
    result = timely.parse_date_query(query)
    if result is None:
        return None
    return Answer(
        kind="date", title=result.label, subtitle="", detail=result.detail,
        tool="date-calculator", copy_value=result.label,
        data={"target": result.target, "days": result.days},
    )


def _timestamps(query: str, ctx: dict) -> Optional[Answer]:
    result = timely.parse_timestamp_query(query)
    if result is None:
        return None
    return Answer(
        kind="timestamp", title=result["title"], subtitle=result["subtitle"],
        rows=result["rows"], tool="timestamp",
        copy_value=result["title"],
    )


def _bases(query: str, ctx: dict) -> Optional[Answer]:
    result = convert.parse_base(query)
    if result is None:
        return None
    return Answer(
        kind="base", title=result.decimal if result.source_base != 10
        else f"0b{result.binary}",
        subtitle=f"Base {result.source_base} input",
        rows=[("Decimal", result.decimal),
              ("Binary", f"0b{result.binary}"),
              ("Octal", f"0o{result.octal}"),
              ("Hexadecimal", f"0x{result.hexadecimal}")],
        tool="base-converter", copy_value=result.decimal,
    )


def _roman(query: str, ctx: dict) -> Optional[Answer]:
    result = convert.parse_roman(query)
    if result is None:
        return None
    source, output = result
    return Answer(
        kind="roman", title=output, subtitle=source,
        detail="Roman numerals", tool="roman-numerals", copy_value=output,
    )


def _colors(query: str, ctx: dict) -> Optional[Answer]:
    color = convert.parse_color(query)
    if color is None:
        return None
    r, g, b = color.rgb
    return Answer(
        kind="color", title=color.hex.upper(),
        subtitle=color.name.capitalize() if color.name else "Colour",
        rows=[
            ("HEX", color.hex.upper()),
            ("RGB", f"rgb({r}, {g}, {b})"),
            ("HSL", f"hsl({color.hsl[0]}, {color.hsl[1]}%, {color.hsl[2]}%)"),
            ("HSV", f"hsv({color.hsv[0]}, {color.hsv[1]}%, {color.hsv[2]}%)"),
            ("CMYK", "cmyk({}%, {}%, {}%, {}%)".format(*color.cmyk)),
        ],
        tool="color-picker", copy_value=color.hex.upper(),
        data={
            "hex": color.hex,
            "on_dark": color.on_dark,
            "shades": convert.color_shades(color),
            "luminance": color.luminance,
        },
    )


def _encodings(query: str, ctx: dict) -> Optional[Answer]:
    result = convert.parse_encoding(query)
    if result is None:
        return None
    return Answer(
        kind="text", title=result.output, subtitle=result.operation,
        detail=f"from “{result.source[:80]}”",
        tool="encoder", copy_value=result.output,
    )


def _hashes(query: str, ctx: dict) -> Optional[Answer]:
    result = convert.parse_hash(query)
    if result is None:
        return None
    return Answer(
        kind="hash", title=result.output, subtitle=result.operation,
        detail=f"of “{result.source[:60]}”",
        rows=result.extra, tool="hash", copy_value=result.output,
    )


def _generators(query: str, ctx: dict) -> Optional[Answer]:
    result = (generate.parse_random(query) or generate.parse_password(query)
              or generate.parse_uuid(query) or generate.parse_lorem(query)
              or generate.parse_word_count(query))
    if result is None:
        return None
    tool_slug = {
        "coin": "random", "dice": "random", "random": "random",
        "password": "password", "uuid": "uuid", "lorem": "lorem",
        "wordcount": "word-count",
    }.get(result.kind, "")
    return Answer(
        kind=result.kind, title=result.title, subtitle=result.subtitle,
        rows=result.rows, items=result.items, tool=tool_slug,
        copy_value=result.title,
    )


_QR_RE = re.compile(
    r"^\s*(?:qr(?:\s*code)?)\s*(?:for|of|generator)?\s*:?\s*(.{1,180})?$", re.I
)


def _qr_code(query: str, ctx: dict) -> Optional[Answer]:
    match = _QR_RE.match(query.strip())
    if not match:
        return None
    payload = (match.group(1) or "").strip()
    if not payload:
        return None
    try:
        svg = qr.make_svg(payload, scale=8, dark="#0b0b12", light="#ffffff")
    except qr.QRError:
        return None
    return Answer(
        kind="qr", title="QR code", subtitle=payload[:120],
        tool="qr", copy_value=payload, data={"svg": svg, "payload": payload},
    )


_IP_RE = re.compile(
    r"^\s*(?:what(?:'s|s| is)?\s+)?(?:my\s+)?ip(?:\s+address)?"
    r"(?:\s+is)?\s*\??$", re.I,
)


def _ip_address(query: str, ctx: dict) -> Optional[Answer]:
    if not _IP_RE.match(query):
        return None
    ip = (ctx or {}).get("client_ip") or ""
    if not ip:
        return None
    rows = [("IP address", ip)]
    user_agent = (ctx or {}).get("user_agent") or ""
    if user_agent:
        rows.append(("User agent", user_agent[:160]))
    return Answer(
        kind="ip", title=ip, subtitle="Your IP address as this server sees it",
        detail="SerikaSearch does not log or store this — it is read from the "
               "current request and discarded when the page finishes rendering.",
        rows=rows, copy_value=ip,
    )


def _widget(kind: str, parse, tool: str):
    """Adapt a :class:`serika.tools.widgets.Widget` parser into a matcher."""
    def matcher(query: str, ctx: dict) -> Optional[Answer]:
        result = parse(query)
        if result is None:
            return None
        return Answer(
            kind=result.kind, title=result.title, subtitle=result.subtitle,
            detail=result.detail, rows=result.rows, items=result.items,
            tool=result.tool or tool, copy_value=result.copy_value,
            data=result.data,
        )
    matcher.__name__ = f"_{kind}"
    return matcher


_bmi = _widget("bmi", widgets.parse_bmi, "bmi")
_split = _widget("split", widgets.parse_split, "split")
_anagram = _widget("anagram", widgets.parse_anagram, "anagram")
_morse = _widget("morse", widgets.parse_morse, "morse")


def _translate(query: str, ctx: dict) -> Optional[Answer]:
    result = translate.parse_translate(query)
    if result is None:
        return None
    rows = [(word, gloss) for word, gloss in result.per_word] \
        if result.literal else []
    detail = (f"Word-by-word into {result.target_name} — a literal gloss, not "
              f"fluent {result.target_name}." if result.literal
              else f"{result.target_name} · {result.target_endonym}")
    return Answer(
        kind="translate", title=result.result,
        subtitle=f"“{result.source}” in {result.target_name}",
        detail=detail, rows=rows, tool="translate",
        copy_value=result.result,
        data={"code": result.target_code, "literal": result.literal},
    )


def _luggage(query: str, ctx: dict) -> Optional[Answer]:
    result = luggage.parse_luggage(query)
    if result is None:
        return None
    if result.airline is None:
        return Answer(kind="interactive", title="Carry-on checker",
                      subtitle="Pick an airline to see its cabin-bag limits.",
                      tool="luggage", data={"widget": "luggage"})
    a = result.airline
    weight = f"{a.weight:g} kg" if a.weight else "No strict weight limit"
    rows = [
        ("Dimensions", f"{a.length} × {a.width} × {a.depth} cm"),
        ("In inches", f"{a.length/2.54:.0f} × {a.width/2.54:.0f} × "
                      f"{a.depth/2.54:.0f} in"),
        ("Weight", weight),
    ]
    if a.personal:
        rows.append(("Personal item", a.personal))
    return Answer(
        kind="luggage", title=f"{a.length} × {a.width} × {a.depth} cm",
        subtitle=f"{a.name} cabin bag", detail=weight, rows=rows,
        tool="luggage", copy_value=f"{a.length} x {a.width} x {a.depth} cm",
        source=f"Published allowance, {result.snapshot}",
        data={"airline": a.name},
    )


def _anime(query: str, ctx: dict) -> Optional[Answer]:
    schedule = anime.parse_anime(query)
    if schedule is None:
        return None
    return Answer(
        kind="anime", title="Airing schedule",
        subtitle="Upcoming episodes worldwide",
        source="AniList", source_url="https://anilist.co/",
        tool="anime",
        data={"episodes": [{
            "title": ep.english or ep.title, "romaji": ep.title,
            "episode": ep.episode, "airing_at": ep.airing_at,
            "format": ep.format, "url": ep.url, "cover": ep.cover,
            "score": ep.score, "genres": ep.genres,
        } for ep in schedule.episodes]},
    )


def _stream(query: str, ctx: dict) -> Optional[Answer]:
    result = stream.parse_stream(query)
    if result is None:
        return None
    return Answer(
        kind="stream",
        title=f"{result.title}" + (f" ({result.year})" if result.year else ""),
        subtitle=f"Where to watch in {result.country_name}",
        detail=result.description,
        source="JustWatch", source_url="https://www.justwatch.com/",
        tool="stream",
        data={
            "groups": [
                {"label": label,
                 "offers": [{"provider": o.provider, "url": o.url}
                            for o in offers]}
                for label, offers in stream.ordered_groups(result)
            ],
            "network": result.network,
            "official_site": result.official_site,
        },
    )


def _recipe(query: str, ctx: dict) -> Optional[Answer]:
    if recipe.parse_recipe(query) is None:
        return None
    return Answer(kind="interactive", title="Recipe converter",
                  subtitle="Scale a recipe up or down and swap ingredients.",
                  tool="recipe", data={"widget": "recipe"})


def _meeting(query: str, ctx: dict) -> Optional[Answer]:
    result = timely.parse_meeting(query)
    if result is None:
        return None
    return Answer(kind="interactive", title="Meeting planner",
                  subtitle="Find the hours that work across time zones.",
                  tool="meeting-planner",
                  data={"widget": "meeting", "zones": result["zones"]})


def _universe(query: str, ctx: dict) -> Optional[Answer]:
    if not re.match(
        r"^\s*(?:scale\s+of\s+(?:the\s+)?universe|universe\s+scale|"
        r"powers\s+of\s+ten|size\s+comparison|size\s+of\s+the\s+universe|"
        r"cosmic\s+scale)\s*$", query.strip(), re.I,
    ):
        return None
    return Answer(kind="interactive", title="Scale of the Universe",
                  subtitle="Slide from the Planck length to the observable "
                           "universe.",
                  tool="scale-of-universe", data={"widget": "universe"})


def _sun(query: str, ctx: dict) -> Optional[Answer]:
    result = live.parse_sun(query)
    if result is None:
        return None
    return Answer(
        kind="sun", title=f"↑ {result.sunrise}",
        subtitle=f"{result.place}{', ' + result.country if result.country else ''}",
        detail=f"Sunrise {result.sunrise} · Sunset {result.sunset} · "
               f"{result.day_length} of daylight",
        rows=[("Sunrise", result.sunrise), ("Sunset", result.sunset),
              ("Day length", result.day_length)],
        source="Open-Meteo", source_url="https://open-meteo.com/",
        tool="sun", copy_value=f"{result.sunrise} / {result.sunset}",
        data={"sunset": result.sunset, "sunrise": result.sunrise},
    )


# Interactive client widgets: the answer carries no computed value, only a
# marker that app.js hydrates into a live component. Triggered by a bare noun.
_INTERACTIVE = {
    "stopwatch": (re.compile(r"^\s*(?:stop\s*watch|timer|count\s*down(?:\s+timer)?)\s*$", re.I),
                  "Stopwatch & timer", "Start, lap, and count down — runs entirely in your browser."),
    "metronome": (re.compile(r"^\s*metronome\s*(?:\d{2,3}\s*bpm)?\s*$", re.I),
                  "Metronome", "Keeps time with an audible click. Tap tempo or set the BPM."),
    "noise": (re.compile(r"^\s*(?:white\s*noise|pink\s*noise|brown\s*noise|"
                         r"ambient\s+(?:sound|noise)|rain\s+sound|ocean\s+sound|"
                         r"noise\s+machine|focus\s+sound)s?\s*$", re.I),
              "Ambient sounds", "White, pink and brown noise plus rain and ocean, generated live."),
    "periodic-table": (re.compile(r"^\s*periodic\s+table(?:\s+of\s+(?:the\s+)?elements)?\s*$", re.I),
                       "Periodic table", "Every element, searchable, with the key facts on tap."),
    "font-preview": (re.compile(r"^\s*(?:font\s+(?:preview|visuali[sz]er|tester|sandbox)|"
                               r"type\s+tester)\s*$", re.I),
                     "Font previewer", "See any sentence in a range of web-safe typefaces."),
    "color-picker": (re.compile(r"^\s*colou?r\s*picker\s*$", re.I),
                     "Colour picker", "Pick a colour and read it in every format at once."),
}


def _interactive(query: str, ctx: dict) -> Optional[Answer]:
    text = query.strip()
    for slug, spec in _INTERACTIVE.items():
        pattern, name, blurb = spec
        if pattern.match(text):
            bpm = ""
            if slug == "metronome":
                m = re.search(r"(\d{2,3})\s*bpm", text, re.I)
                bpm = m.group(1) if m else ""
            return Answer(
                kind="interactive", title=name, subtitle=blurb,
                tool=slug if slug != "periodic-table" else "periodic-table",
                data={"widget": slug, "bpm": bpm},
            )
    return None


# The chain, in resolution order.
_MATCHERS: tuple[Callable[[str, dict], Optional[Answer]], ...] = (
    _weather,
    _sun,
    _anime,
    _stream,
    _luggage,
    _translate,
    _recipe,
    _meeting,
    _universe,
    _currency,
    _units,
    _world_time,
    _dates,
    _timestamps,
    _ip_address,
    _colors,
    _bmi,
    _split,
    _anagram,
    _morse,
    _bases,
    _roman,
    _encodings,
    _hashes,
    _qr_code,
    _interactive,
    _generators,
    _calculator,       # greedy, so it goes last
)


def resolve(query: str, ctx: dict | None = None) -> Optional[Answer]:
    """Return the first instant answer that matches ``query``."""
    query = (query or "").strip()
    if not query or len(query) > 4000:
        return None
    ctx = ctx or {}
    for matcher in _MATCHERS:
        try:
            answer = matcher(query, ctx)
        except Exception:
            # A broken tool must never take down a search: fall through.
            continue
        if answer is not None:
            return answer
    return None


# --------------------------------------------------------------------------- #
# the tool directory (/tools)
# --------------------------------------------------------------------------- #

@dataclass
class ToolInfo:
    slug: str
    name: str
    blurb: str
    group: str
    example: str
    icon: str


TOOLS: tuple[ToolInfo, ...] = (
    ToolInfo("calculator", "Calculator",
             "Arithmetic, powers, roots, trigonometry and percentages.",
             "Maths", "sqrt(144) + 12%", "calc"),
    ToolInfo("unit-converter", "Unit converter",
             "Length, mass, volume, area, speed, energy, data and more.",
             "Maths", "5 km to miles", "convert"),
    ToolInfo("percentage", "Percentage calculator",
             "Percentage of, percentage change, discounts and tips.",
             "Maths", "20% off 250", "percent"),
    ToolInfo("base-converter", "Number base converter",
             "Decimal, binary, octal and hexadecimal, side by side.",
             "Maths", "255 in binary", "binary"),
    ToolInfo("roman-numerals", "Roman numerals",
             "Convert both ways between Roman and Arabic numerals.",
             "Maths", "MCMXCIV", "roman"),
    ToolInfo("currency", "Currency converter",
             "Live European Central Bank reference rates for 30+ currencies.",
             "Everyday", "100 usd to eur", "currency"),
    ToolInfo("weather", "Weather",
             "Current conditions and a six-day forecast for any place.",
             "Everyday", "weather in tokyo", "weather"),
    ToolInfo("world-clock", "World clock",
             "The current time anywhere, with UTC offsets and DST.",
             "Everyday", "time in tokyo", "clock"),
    ToolInfo("date-calculator", "Date calculator",
             "Days until, days between, weekday of a date, and age.",
             "Everyday", "days until christmas", "calendar"),
    ToolInfo("timestamp", "Unix timestamp",
             "Convert between epoch seconds and human dates.",
             "Developer", "1700000000 to date", "clock"),
    ToolInfo("color-picker", "Colour converter",
             "HEX, RGB, HSL, HSV and CMYK, with a generated shade ramp.",
             "Design", "#a274ff", "color"),
    ToolInfo("qr", "QR code generator",
             "Turn any text, URL or Wi-Fi string into a scannable code.",
             "Design", "qr code for https://serikasearch.com", "qr"),
    ToolInfo("encoder", "Encoder / decoder",
             "Base64, URL, HTML entity, hex, binary and ROT13.",
             "Developer", "base64 encode hello", "code"),
    ToolInfo("hash", "Hash generator",
             "MD5, SHA-1, SHA-256, SHA-512 and CRC32 checksums.",
             "Developer", "sha256 hello", "hash"),
    ToolInfo("password", "Password generator",
             "Cryptographically random passwords and passphrases.",
             "Developer", "generate password", "key"),
    ToolInfo("uuid", "UUID generator",
             "Version 4 UUIDs in every common formatting.",
             "Developer", "uuid", "id"),
    ToolInfo("random", "Random picker",
             "Coin flips, dice rolls and numbers in a range.",
             "Everyday", "roll 2d6", "dice"),
    ToolInfo("word-count", "Word counter",
             "Words, characters, sentences and reading time.",
             "Text", "word count: your text here", "text"),
    ToolInfo("lorem", "Lorem ipsum",
             "Placeholder paragraphs for mock-ups and layouts.",
             "Text", "lorem ipsum", "text"),
    ToolInfo("dictionary", "Dictionary",
             "Definitions, pronunciation, synonyms and etymology.",
             "Reference", "define serendipity", "book"),
    ToolInfo("bmi", "BMI calculator",
             "Body-mass index from height and weight, metric or imperial.",
             "Health", "bmi 180cm 75kg", "heart"),
    ToolInfo("split", "Bill splitter",
             "Split a check with tip, evenly across any number of people.",
             "Everyday", "split bill 120 by 3 at 18%", "receipt"),
    ToolInfo("sun", "Sunrise & sunset",
             "Today's sunrise, sunset and daylight length anywhere.",
             "Everyday", "sunrise in tokyo", "sun"),
    ToolInfo("anagram", "Anagram solver",
             "Every word you can spell from a rack of letters.",
             "Text", "anagram listen", "tiles"),
    ToolInfo("morse", "Morse code",
             "Encode or decode Morse, with audible playback.",
             "Text", "morse code hello", "dots"),
    ToolInfo("stopwatch", "Stopwatch & timer",
             "A stopwatch with laps and a countdown timer, all in-browser.",
             "Everyday", "stopwatch", "clock"),
    ToolInfo("metronome", "Metronome",
             "An audible metronome with tap-tempo, 40–240 BPM.",
             "Music", "metronome", "metronome"),
    ToolInfo("noise", "Ambient sounds",
             "White, pink and brown noise, rain and ocean — generated live.",
             "Music", "white noise", "waves"),
    ToolInfo("periodic-table", "Periodic table",
             "All 118 elements, searchable, with their key properties.",
             "Reference", "periodic table", "atom"),
    ToolInfo("font-preview", "Font previewer",
             "Preview a sentence across web-safe typefaces and sizes.",
             "Design", "font preview", "type"),
    ToolInfo("translate", "Phrasebook",
             "Common phrases and words across eight languages, offline.",
             "Reference", "thank you in japanese", "globe"),
    ToolInfo("scale-of-universe", "Scale of the Universe",
             "Zoom from the Planck length to the observable universe.",
             "Reference", "scale of universe", "atom"),
    ToolInfo("anime", "Anime schedule",
             "Upcoming anime episodes worldwide, from AniList.",
             "Media", "anime schedule", "tv"),
    ToolInfo("luggage", "Carry-on checker",
             "Cabin-bag size and weight limits for major airlines.",
             "Everyday", "carry on size ryanair", "luggage"),
    ToolInfo("stream", "Where to watch",
             "Streaming availability for films and shows, by country.",
             "Media", "where to watch inception", "tv"),
    ToolInfo("recipe", "Recipe converter",
             "Scale ingredient lists and swap in dietary substitutions.",
             "Everyday", "recipe converter", "receipt"),
    ToolInfo("meeting-planner", "Meeting planner",
             "Find the overlapping working hours across time zones.",
             "Everyday", "meeting planner", "globe"),
    ToolInfo("artist", "Artist & discography",
             "Genres, active years, albums and links, from MusicBrainz.",
             "Media", "taylor swift discography", "tv"),
)

_TOOLS_BY_SLUG = {tool.slug: tool for tool in TOOLS}


def tool_by_slug(slug: str) -> Optional[ToolInfo]:
    return _TOOLS_BY_SLUG.get(slug)


def tool_groups() -> list[tuple[str, list[ToolInfo]]]:
    """Tools bucketed by group, in the order the groups first appear."""
    grouped: dict[str, list[ToolInfo]] = {}
    for tool in TOOLS:
        grouped.setdefault(tool.group, []).append(tool)
    return list(grouped.items())
