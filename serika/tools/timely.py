"""Date and time answers: world clock, date arithmetic, and timestamps.

All of it is local computation over :mod:`zoneinfo`, so ``time in tokyo`` and
``days until christmas`` never leave the box. The city table maps the places
people actually type to IANA zones; anything else falls back to matching the
zone names themselves (``time in Europe/Berlin`` works too).
"""

from __future__ import annotations

import calendar
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover — Python < 3.9
    ZoneInfo = None
    def available_timezones():
        return set()

__all__ = ["WorldTime", "parse_time_query", "DateDiff", "parse_date_query",
           "parse_timestamp_query", "resolve_zone", "zone_offset_hours",
           "meeting_slots", "slot_local_label"]


# --------------------------------------------------------------------------- #
# world clock
# --------------------------------------------------------------------------- #

# Cities worth answering directly, keyed by the way people type them.
CITY_ZONES = {
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "paris": "Europe/Paris", "france": "Europe/Paris",
    "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "madrid": "Europe/Madrid", "spain": "Europe/Madrid",
    "rome": "Europe/Rome", "italy": "Europe/Rome",
    "amsterdam": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "brussels": "Europe/Brussels", "vienna": "Europe/Vienna",
    "zurich": "Europe/Zurich", "switzerland": "Europe/Zurich",
    "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo",
    "copenhagen": "Europe/Copenhagen", "helsinki": "Europe/Helsinki",
    "dublin": "Europe/Dublin", "ireland": "Europe/Dublin",
    "lisbon": "Europe/Lisbon", "portugal": "Europe/Lisbon",
    "warsaw": "Europe/Warsaw", "poland": "Europe/Warsaw",
    "prague": "Europe/Prague", "budapest": "Europe/Budapest",
    "athens": "Europe/Athens", "greece": "Europe/Athens",
    "moscow": "Europe/Moscow", "russia": "Europe/Moscow",
    "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul",
    "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv", "ukraine": "Europe/Kyiv",
    "new york": "America/New_York", "nyc": "America/New_York",
    "boston": "America/New_York", "washington": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York",
    "toronto": "America/Toronto", "montreal": "America/Toronto",
    "chicago": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "mexico city": "America/Mexico_City",
    "mexico": "America/Mexico_City", "denver": "America/Denver",
    "phoenix": "America/Phoenix", "las vegas": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "vancouver": "America/Vancouver", "anchorage": "America/Anchorage",
    "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "sao paulo": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires", "lima": "America/Lima",
    "bogota": "America/Bogota", "santiago": "America/Santiago",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "osaka": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "china": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei", "taiwan": "Asia/Taipei",
    "singapore": "Asia/Singapore", "bangkok": "Asia/Bangkok",
    "thailand": "Asia/Bangkok", "jakarta": "Asia/Jakarta",
    "indonesia": "Asia/Jakarta", "manila": "Asia/Manila",
    "philippines": "Asia/Manila", "kuala lumpur": "Asia/Kuala_Lumpur",
    "hanoi": "Asia/Ho_Chi_Minh", "vietnam": "Asia/Ho_Chi_Minh",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "india": "Asia/Kolkata", "karachi": "Asia/Karachi",
    "pakistan": "Asia/Karachi", "dhaka": "Asia/Dhaka",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai", "abu dhabi": "Asia/Dubai",
    "riyadh": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh",
    "tel aviv": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem", "tehran": "Asia/Tehran",
    "cairo": "Africa/Cairo", "egypt": "Africa/Cairo",
    "lagos": "Africa/Lagos", "nigeria": "Africa/Lagos",
    "nairobi": "Africa/Nairobi", "kenya": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg", "cape town": "Africa/Johannesburg",
    "south africa": "Africa/Johannesburg", "casablanca": "Africa/Casablanca",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane", "perth": "Australia/Perth",
    "australia": "Australia/Sydney", "adelaide": "Australia/Adelaide",
    "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
    "utc": "UTC", "gmt": "UTC", "zulu": "UTC",
}

# Common abbreviations people type instead of a city.
ABBREV_ZONES = {
    "est": "America/New_York", "edt": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "cet": "Europe/Paris", "cest": "Europe/Paris",
    "bst": "Europe/London", "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo", "kst": "Asia/Seoul", "aest": "Australia/Sydney",
}


@dataclass
class WorldTime:
    place: str
    zone: str
    iso: str
    clock: str          # "14:32"
    clock_12: str       # "2:32 PM"
    day: str            # "Friday, 15 August 2026"
    offset: str         # "UTC+9"
    abbreviation: str   # "JST"
    is_dst: bool


_TIME_RE = re.compile(
    r"^\s*(?:what(?:'s|s| is)?\s+)?(?:the\s+)?(?:current\s+)?(?:local\s+)?"
    r"(?:time|clock|hour)\s*(?:is\s+it)?\s*(?:right\s+now)?\s*"
    r"(?:in|at|for)\s+(.{2,40}?)\s*\??$",
    re.I,
)
_TIME_BARE_RE = re.compile(
    r"^\s*(?:what(?:'s| is)?\s+the\s+)?(?:current\s+)?time"
    r"(?:\s+is\s+it)?\s*(?:now|right\s+now)?\s*\??$", re.I
)


def _resolve_zone(place: str) -> str | None:
    key = re.sub(r"\s+", " ", place.strip().lower()).strip(" ?,.")
    if key in CITY_ZONES:
        return CITY_ZONES[key]
    if key in ABBREV_ZONES:
        return ABBREV_ZONES[key]
    if ZoneInfo is None:
        return None
    # Accept a real IANA name, case-insensitively.
    candidate = place.strip().replace(" ", "_")
    zones = available_timezones()
    if candidate in zones:
        return candidate
    lowered = {z.lower(): z for z in zones}
    if candidate.lower() in lowered:
        return lowered[candidate.lower()]
    # Last resort: match the city segment of a zone name ("…/Reykjavik").
    tail = key.replace(" ", "_")
    for zone in sorted(zones):
        if zone.lower().rsplit("/", 1)[-1] == tail:
            return zone
    return None


def _build_world_time(place: str, zone_name: str) -> WorldTime | None:
    if ZoneInfo is None:
        return None
    try:
        tz = ZoneInfo(zone_name)
    except Exception:
        return None
    now = datetime.now(tz)
    offset = now.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "−"
    hours, minutes = divmod(abs(total_minutes), 60)
    offset_text = f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")
    return WorldTime(
        place=place.strip().title() if place.islower() else place.strip(),
        zone=zone_name,
        iso=now.isoformat(timespec="seconds"),
        clock=now.strftime("%H:%M"),
        clock_12=now.strftime("%-I:%M %p") if hasattr(now, "strftime") else "",
        day=now.strftime("%A, %-d %B %Y"),
        offset=offset_text,
        abbreviation=now.strftime("%Z"),
        is_dst=bool(now.dst()),
    )


def parse_time_query(query: str) -> WorldTime | None:
    """``time in tokyo`` → the current local time there."""
    m = _TIME_RE.match(query)
    if m:
        place = m.group(1)
        zone = _resolve_zone(place)
        return _build_world_time(place, zone) if zone else None
    if _TIME_BARE_RE.match(query):
        return _build_world_time("Coordinated Universal Time", "UTC")
    return None


def world_clock(zones: list[str]) -> list[WorldTime]:
    """A row of clocks for the world-clock tool page."""
    out = []
    for zone in zones:
        wt = _build_world_time(zone.rsplit("/", 1)[-1].replace("_", " "), zone)
        if wt:
            out.append(wt)
    return out


# --------------------------------------------------------------------------- #
# date arithmetic
# --------------------------------------------------------------------------- #

@dataclass
class DateDiff:
    label: str
    days: int
    detail: str
    target: str = ""


_HOLIDAYS = {
    "christmas": (12, 25), "christmas day": (12, 25), "xmas": (12, 25),
    "christmas eve": (12, 24), "new year": (1, 1), "new years": (1, 1),
    "new year's": (1, 1), "new years day": (1, 1), "halloween": (10, 31),
    "valentines day": (2, 14), "valentine's day": (2, 14),
    "april fools": (4, 1), "april fools day": (4, 1),
    "independence day": (7, 4), "july 4th": (7, 4), "4th of july": (7, 4),
    "boxing day": (12, 26), "st patricks day": (3, 17),
    "st patrick's day": (3, 17), "earth day": (4, 22),
}

_UNTIL_RE = re.compile(
    r"^\s*(?:how\s+many\s+days?|days?)\s+(?:until|till|til|to|before)\s+(.{2,40}?)\s*\??$",
    re.I,
)
_SINCE_RE = re.compile(
    r"^\s*(?:how\s+many\s+days?|days?)\s+(?:since|after|from)\s+(.{2,40}?)\s*\??$",
    re.I,
)
_BETWEEN_RE = re.compile(
    r"^\s*(?:how\s+many\s+)?days?\s+between\s+(.{4,30}?)\s+and\s+(.{4,30}?)\s*\??$",
    re.I,
)
_WEEKDAY_RE = re.compile(
    r"^\s*what\s+day\s+(?:of\s+the\s+week\s+)?(?:is|was|were)\s+(.{4,30}?)\s*\??$",
    re.I,
)
_AGE_RE = re.compile(
    r"^\s*(?:my\s+)?age\s+(?:if\s+)?(?:born\s+)?(?:on\s+)?(.{4,30}?)\s*\??$", re.I
)

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d",
    "%d %B %Y", "%B %d %Y", "%B %d, %Y", "%d %b %Y", "%b %d %Y", "%b %d, %Y",
    "%d.%m.%Y", "%Y%m%d",
)


def _parse_date(text: str) -> date | None:
    raw = text.strip().strip(",.").lower()
    today = date.today()
    if raw in ("today", "now"):
        return today
    if raw == "tomorrow":
        return today + timedelta(days=1)
    if raw == "yesterday":
        return today - timedelta(days=1)

    if raw in _HOLIDAYS:
        month, day = _HOLIDAYS[raw]
        candidate = date(today.year, month, day)
        return candidate

    cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text.strip(), flags=re.I)
    cleaned = cleaned.strip().strip(",.")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    # Bare "25 december" / "december 25" → the given day this year.
    for fmt in ("%d %B", "%B %d", "%d %b", "%b %d"):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
            return date(today.year, parsed.month, parsed.day)
        except ValueError:
            continue
    return None


def _humanise(days: int) -> str:
    if days == 0:
        return "today"
    years, rest = divmod(abs(days), 365)
    months, rest_days = divmod(rest, 30)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if rest_days and not years:
        parts.append(f"{rest_days} day{'s' if rest_days != 1 else ''}")
    return ", ".join(parts) or f"{abs(days)} days"


def parse_date_query(query: str) -> DateDiff | None:
    """``days until christmas``, ``days between … and …``, ``what day was …``."""
    today = date.today()

    m = _UNTIL_RE.match(query)
    if m:
        target = _parse_date(m.group(1))
        if target is None:
            return None
        # A holiday that already passed this year means the next occurrence.
        if target < today and m.group(1).strip().lower() in _HOLIDAYS:
            target = date(today.year + 1, target.month, target.day)
        delta = (target - today).days
        return DateDiff(
            label=f"{delta:,} day{'s' if abs(delta) != 1 else ''}",
            days=delta,
            detail=(f"until {target.strftime('%A, %-d %B %Y')} · {_humanise(delta)}"
                    if delta >= 0
                    else f"{target.strftime('%A, %-d %B %Y')} has already passed"),
            target=target.isoformat(),
        )

    m = _SINCE_RE.match(query)
    if m:
        target = _parse_date(m.group(1))
        if target is None:
            return None
        delta = (today - target).days
        return DateDiff(
            label=f"{delta:,} day{'s' if abs(delta) != 1 else ''}",
            days=delta,
            detail=f"since {target.strftime('%A, %-d %B %Y')} · {_humanise(delta)}",
            target=target.isoformat(),
        )

    m = _BETWEEN_RE.match(query)
    if m:
        start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
        if start is None or end is None:
            return None
        delta = abs((end - start).days)
        weeks, extra = divmod(delta, 7)
        return DateDiff(
            label=f"{delta:,} day{'s' if delta != 1 else ''}",
            days=delta,
            detail=(f"{start.strftime('%-d %b %Y')} → {end.strftime('%-d %b %Y')}"
                    f" · {weeks:,} weeks and {extra} days · {_humanise(delta)}"),
            target=end.isoformat(),
        )

    m = _WEEKDAY_RE.match(query)
    if m:
        target = _parse_date(m.group(1))
        if target is None:
            return None
        delta = (target - today).days
        return DateDiff(
            label=target.strftime("%A"),
            days=delta,
            detail=(f"{target.strftime('%-d %B %Y')} · "
                    f"week {target.isocalendar()[1]} · "
                    f"day {target.timetuple().tm_yday} of the year"),
            target=target.isoformat(),
        )

    m = _AGE_RE.match(query)
    if m:
        born = _parse_date(m.group(1))
        if born is None or born > today:
            return None
        years = today.year - born.year - (
            (today.month, today.day) < (born.month, born.day)
        )
        days = (today - born).days
        return DateDiff(
            label=f"{years} years old",
            days=days,
            detail=(f"born {born.strftime('%A, %-d %B %Y')} · "
                    f"{days:,} days · {days * 24:,} hours"),
            target=born.isoformat(),
        )
    return None


# --------------------------------------------------------------------------- #
# unix timestamps
# --------------------------------------------------------------------------- #

_TS_NOW_RE = re.compile(
    r"^\s*(?:current\s+)?(?:unix\s*)?(?:time\s*stamp|timestamp|epoch)"
    r"(?:\s+now)?\s*\??$", re.I,
)
_TS_VALUE_RE = re.compile(
    r"^\s*(?:convert\s+)?(?:unix\s+|epoch\s+)?(?:time\s*stamp\s+)?(\d{9,13})\s*"
    r"(?:(?:unix\s+|epoch\s+)?time\s*stamp)?\s*"
    r"(?:to\s+(?:date|time|human)|as\s+(?:a\s+)?date|in\s+human)?\s*\??$", re.I,
)


def parse_timestamp_query(query: str) -> dict | None:
    """``unix timestamp`` → now; ``1700000000`` → the date it represents."""
    if _TS_NOW_RE.match(query):
        now = _time.time()
        utc = datetime.fromtimestamp(now, tz=timezone.utc)
        return {
            "title": str(int(now)),
            "subtitle": "Current Unix timestamp (seconds since 1 Jan 1970 UTC)",
            "rows": [
                ("Milliseconds", str(int(now * 1000))),
                ("UTC", utc.strftime("%Y-%m-%d %H:%M:%S UTC")),
                ("ISO 8601", utc.isoformat(timespec="seconds")),
                ("RFC 2822", utc.strftime("%a, %d %b %Y %H:%M:%S +0000")),
            ],
        }

    m = _TS_VALUE_RE.match(query)
    if m and re.search(r"timestamp|epoch|unix|to date", query, re.I):
        raw = int(m.group(1))
        seconds = raw / 1000 if raw > 10 ** 11 else raw
        try:
            utc = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return {
            "title": utc.strftime("%-d %B %Y, %H:%M:%S UTC"),
            "subtitle": f"Unix timestamp {raw}",
            "rows": [
                ("ISO 8601", utc.isoformat(timespec="seconds")),
                ("Weekday", utc.strftime("%A")),
                ("Relative", _relative(seconds)),
                ("Seconds", str(int(seconds))),
            ],
        }
    return None


def _relative(seconds: float) -> str:
    delta = _time.time() - seconds
    direction = "ago" if delta >= 0 else "from now"
    delta = abs(delta)
    for size, name in ((31556952, "year"), (2629746, "month"), (86400, "day"),
                       (3600, "hour"), (60, "minute")):
        if delta >= size:
            n = int(delta // size)
            return f"{n} {name}{'s' if n != 1 else ''} {direction}"
    return "just now"


def month_calendar(year: int = 0, month: int = 0) -> dict:
    """Data for the calendar tool: a month grid plus today's coordinates."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    month = max(1, min(12, month))
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    return {
        "year": year,
        "month": month,
        "name": calendar.month_name[month],
        "weeks": weeks,
        "today": (today.year, today.month, today.day),
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }


# --------------------------------------------------------------------------- #
# meeting planner across time zones
# --------------------------------------------------------------------------- #

_MEETING_RE = re.compile(
    r"^\s*(?:meeting\s+planner|plan\s+(?:a\s+)?meeting|time\s*zone\s+"
    r"(?:meeting|planner|overlap)|world\s+clock\s+meeting|"
    r"schedule\s+across\s+time\s*zones?)\s*(.*)$",
    re.I,
)


def parse_meeting(query: str):
    """``meeting planner``, or ``meeting planner tokyo london new york``.

    Returns a dict the widget hydrates, or ``None``. Cities named in the query
    pre-fill the planner; otherwise it opens with a sensible default set.
    """
    match = _MEETING_RE.match(query.strip())
    if not match:
        return None

    trailing = match.group(1).strip()
    zones: list[str] = []
    if trailing:
        # Split on commas or "and"; resolve each fragment to a zone.
        for fragment in re.split(r"\s*(?:,|\band\b)\s*", trailing):
            fragment = fragment.strip()
            if not fragment:
                continue
            zone = _resolve_zone(fragment)
            if zone:
                zones.append(zone)
    if not zones:
        zones = ["America/Los_Angeles", "America/New_York",
                 "Europe/London", "Asia/Tokyo"]
    # De-duplicate, preserve order, cap at 5 columns.
    seen: set[str] = set()
    unique = [z for z in zones if not (z in seen or seen.add(z))][:5]
    return {"zones": unique}


def meeting_columns(zones: list[str]) -> list[dict]:
    """Per-zone data for the planner grid: label, current offset, and the UTC
    offset in hours so the client can shade each local hour."""
    if ZoneInfo is None:
        return []
    from datetime import datetime as _dt
    out = []
    for zone_name in zones:
        try:
            tz = ZoneInfo(zone_name)
        except Exception:
            continue
        now = _dt.now(tz)
        offset = now.utcoffset() or timedelta(0)
        out.append({
            "zone": zone_name,
            "label": zone_name.rsplit("/", 1)[-1].replace("_", " "),
            "offset_hours": offset.total_seconds() / 3600.0,
            "abbrev": now.strftime("%Z"),
        })
    return out


# --------------------------------------------------------------------------- #
# shareable meeting planner — zone resolution + slot math
# --------------------------------------------------------------------------- #
#
# ``resolve_zone`` is the public face of ``_resolve_zone``: it accepts anything
# a person might type ("Amsterdam", "nyc", "Asia/Kolkata", "cest") and returns
# the IANA zone name or None. The meeting planner's city input calls this via
# /api/zone, which is why a typed city now resolves through the full CITY_ZONES
# table plus the IANA fallback instead of a tiny client-side map.

def resolve_zone(place: str) -> str | None:
    """Resolve a free-form place string to an IANA zone name, or None."""
    return _resolve_zone(place)


def zone_offset_hours(zone_name: str, at: "datetime | None" = None) -> float:
    """Current (or at-moment) UTC offset for a zone, in hours.

    DST-correct: passing ``at`` lets the planner compute the offset that will
    apply on a specific future date, not just right now.
    """
    if ZoneInfo is None or not zone_name:
        return 0.0
    try:
        tz = ZoneInfo(zone_name)
    except Exception:
        return 0.0
    moment = at or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    offset = moment.utcoffset() or timedelta(0)
    return offset.total_seconds() / 3600.0


def _parse_iso_date(s: str) -> date:
    """Parse a strict YYYY-MM-DD date (used by the meeting planner)."""
    y, m, d = (int(x) for x in s.strip().split("-"))
    return date(y, m, d)


def meeting_slots(date_start: str, date_end: str, hour_start: int,
                  hour_end: int, owner_zone: str) -> list[dict]:
    """Build the candidate slots for a plan, in the owner's local frame.

    Each slot is one hour. The grid is laid out as owner-local days × owner-
    local hours, so the columns line up for every viewer. Each slot carries
    its UTC instant (``utc``, the stable key responses store availability
    against) and the owner-local label, so the aggregate view can render
    without knowing any other zone.

    Caps at 240 slots (≈ 16 days × 15 hours) to keep payloads sane.
    """
    if ZoneInfo is None:
        return []
    try:
        tz = ZoneInfo(owner_zone) if owner_zone else timezone.utc
    except Exception:
        tz = timezone.utc
    h0 = max(0, min(23, int(hour_start)))
    h1 = max(h0, min(23, int(hour_end)))
    hours = list(range(h0, h1 + 1))
    try:
        start = _parse_iso_date(date_start)
        end = _parse_iso_date(date_end)
    except Exception:
        return []
    if end < start:
        end = start

    slots: list[dict] = []
    day = start
    while day <= end and len(slots) < 240:
        for h in hours:
            local_dt = datetime(day.year, day.month, day.day, h, 0,
                                tzinfo=tz)
            utc_dt = local_dt.astimezone(timezone.utc)
            slots.append({
                "key": utc_dt.strftime("%Y-%m-%dT%H:00"),
                "owner_date": day.isoformat(),
                "owner_hour": h,
                "owner_label": f"{day.strftime('%a %-d %b')} {h:02d}:00",
            })
            if len(slots) >= 240:
                break
        day += timedelta(days=1)
    return slots


def slot_local_label(utc_key: str, viewer_zone: str) -> dict:
    """Convert a slot's UTC instant into a viewer's local clock label.

    Returns ``{date, hour, label, offset_hours}``. Used by the submission
    form so each respondent sees their own wall-clock time per cell while the
    underlying key stays the shared UTC instant.
    """
    if ZoneInfo is None or not viewer_zone:
        return {"date": "", "hour": 0, "label": "—", "offset_hours": 0.0}
    try:
        tz = ZoneInfo(viewer_zone)
    except Exception:
        return {"date": "", "hour": 0, "label": "—", "offset_hours": 0.0}
    try:
        utc_dt = datetime.strptime(utc_key, "%Y-%m-%dT%H:00")
    except ValueError:
        return {"date": "", "hour": 0, "label": "—", "offset_hours": 0.0}
    local_dt = utc_dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return {
        "date": local_dt.strftime("%Y-%m-%d"),
        "hour": local_dt.hour,
        "label": local_dt.strftime("%a %-d %b %H:00"),
        "offset_hours": (local_dt.utcoffset() or timedelta(0))
                        .total_seconds() / 3600.0,
    }
