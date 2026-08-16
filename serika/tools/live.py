"""Answers that need the network: currency rates and weather.

Both providers are keyless, privacy-respecting public APIs, and every response
is cached (Redis when available, otherwise a small in-process TTL cache) so a
popular query hits the upstream service once rather than once per visitor.
The user's own query never leaves the box in identifiable form — only a city
name or a currency pair does.

Failures are always soft: if the upstream is slow or down, the resolver returns
``None`` and the page simply shows ordinary web results.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = ["set_cache", "CurrencyResult", "parse_currency", "WeatherResult",
           "parse_weather", "SunResult", "parse_sun"]

_TIMEOUT = 5.0
_UA = "serikasearch/1.1 (+https://serikasearch.com/about; instant answers)"

# Redis is injected by the web layer at start-up; until then we use a local
# dictionary so the module works standalone (CLI, tests).
_redis = None
_local_cache: dict[str, tuple[float, str]] = {}
_local_lock = threading.Lock()


def set_cache(redis_client) -> None:
    """Point the module at the shared Redis connection, if there is one."""
    global _redis
    _redis = redis_client


def _cache_get(key: str) -> str | None:
    if _redis is not None:
        try:
            return _redis.get(key)
        except Exception:
            pass
    with _local_lock:
        entry = _local_cache.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        _local_cache.pop(key, None)
    return None


def _cache_set(key: str, value: str, ttl: int) -> None:
    if _redis is not None:
        try:
            _redis.setex(key, ttl, value)
            return
        except Exception:
            pass
    with _local_lock:
        if len(_local_cache) > 500:          # keep the fallback cache bounded
            _local_cache.clear()
        _local_cache[key] = (time.time() + ttl, value)


def _get_json(url: str, cache_key: str = "", ttl: int = 900):
    """GET + parse JSON, with caching and no exceptions escaping."""
    if cache_key:
        cached = _cache_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except ValueError:
                pass
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read(1024 * 512).decode("utf-8", "replace")
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if cache_key:
        _cache_set(cache_key, raw, ttl)
    return data


# --------------------------------------------------------------------------- #
# currency
# --------------------------------------------------------------------------- #

CURRENCIES = {
    "usd": ("US Dollar", "$"), "eur": ("Euro", "€"), "gbp": ("British Pound", "£"),
    "jpy": ("Japanese Yen", "¥"), "chf": ("Swiss Franc", "CHF"),
    "cad": ("Canadian Dollar", "CA$"), "aud": ("Australian Dollar", "A$"),
    "nzd": ("New Zealand Dollar", "NZ$"), "cny": ("Chinese Yuan", "CN¥"),
    "hkd": ("Hong Kong Dollar", "HK$"), "sgd": ("Singapore Dollar", "S$"),
    "sek": ("Swedish Krona", "kr"), "nok": ("Norwegian Krone", "kr"),
    "dkk": ("Danish Krone", "kr"), "pln": ("Polish Złoty", "zł"),
    "czk": ("Czech Koruna", "Kč"), "huf": ("Hungarian Forint", "Ft"),
    "ron": ("Romanian Leu", "lei"), "bgn": ("Bulgarian Lev", "лв"),
    "try": ("Turkish Lira", "₺"), "inr": ("Indian Rupee", "₹"),
    "krw": ("South Korean Won", "₩"), "brl": ("Brazilian Real", "R$"),
    "mxn": ("Mexican Peso", "MX$"), "zar": ("South African Rand", "R"),
    "ils": ("Israeli Shekel", "₪"), "php": ("Philippine Peso", "₱"),
    "thb": ("Thai Baht", "฿"), "myr": ("Malaysian Ringgit", "RM"),
    "idr": ("Indonesian Rupiah", "Rp"), "isk": ("Icelandic Króna", "kr"),
}

# Symbols and nicknames people type instead of an ISO code.
_CURRENCY_ALIASES = {
    "$": "usd", "dollar": "usd", "dollars": "usd", "us dollar": "usd",
    "€": "eur", "euro": "eur", "euros": "eur",
    "£": "gbp", "pound": "gbp", "pounds": "gbp", "quid": "gbp",
    "sterling": "gbp", "¥": "jpy", "yen": "jpy", "yuan": "cny",
    "rmb": "cny", "renminbi": "cny", "rupee": "inr", "rupees": "inr",
    "won": "krw", "real": "brl", "reais": "brl", "peso": "mxn",
    "rand": "zar", "shekel": "ils", "franc": "chf", "krona": "sek",
    "zloty": "pln", "złoty": "pln", "lira": "try", "baht": "thb",
    "ringgit": "myr", "rupiah": "idr", "koruna": "czk", "forint": "huf",
}


@dataclass
class CurrencyResult:
    amount: float
    from_code: str
    to_code: str
    result: float
    rate: float
    date: str
    from_name: str = ""
    to_name: str = ""
    from_symbol: str = ""
    to_symbol: str = ""


_CURRENCY_RE = re.compile(
    r"""^\s*(?:convert\s+|how\s+much\s+is\s+)?
        (?:([$€£¥₹₩₺₪฿])\s*)?
        ([\d.,]+)\s*
        (?:([$€£¥₹₩₺₪฿])|([a-zA-Z]{3,20}(?:\s[a-zA-Z]{3,10})?))?\s*
        (?:to|in|into|as|=|->|→)\s+
        (?:([$€£¥₹₩₺₪฿])|([a-zA-Z]{3,20}(?:\s[a-zA-Z]{3,10})?))\s*\??$""",
    re.I | re.X,
)
# Bare "usd to eur" / "dollar to euro" — implies an amount of 1.
_RATE_RE = re.compile(
    r"^\s*(?:exchange\s+rate\s+)?([a-zA-Z]{3,20})\s*(?:to|in|vs|/)\s*"
    r"([a-zA-Z]{3,20})\s*(?:exchange\s+rate|rate)?\s*\??$", re.I,
)


def _currency_code(token: str | None) -> str | None:
    if not token:
        return None
    key = token.strip().lower()
    if key in CURRENCIES:
        return key.upper()
    if key in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[key].upper()
    singular = key.rstrip("s")
    if singular in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[singular].upper()
    return None


def _fetch_rate(from_code: str, to_code: str, amount: float):
    """One unit rate from the ECB reference set, cached for 30 minutes."""
    url = ("https://api.frankfurter.app/latest?"
           + urllib.parse.urlencode({"from": from_code, "to": to_code}))
    data = _get_json(url, cache_key=f"fx:{from_code}:{to_code}", ttl=1800)
    if not data or "rates" not in data:
        return None
    rate = data["rates"].get(to_code)
    if rate is None:
        return None
    return CurrencyResult(
        amount=amount, from_code=from_code, to_code=to_code,
        result=amount * float(rate), rate=float(rate),
        date=data.get("date", ""),
        from_name=CURRENCIES.get(from_code.lower(), ("", ""))[0],
        to_name=CURRENCIES.get(to_code.lower(), ("", ""))[0],
        from_symbol=CURRENCIES.get(from_code.lower(), ("", ""))[1],
        to_symbol=CURRENCIES.get(to_code.lower(), ("", ""))[1],
    )


def parse_currency(query: str) -> CurrencyResult | None:
    """``100 usd to eur``, ``£20 in dollars``, ``eur to gbp``."""
    text = query.strip()
    if len(text) > 80:
        return None

    m = _CURRENCY_RE.match(text)
    if m:
        lead_symbol, raw_amount, trail_symbol, from_word, to_symbol, to_word = \
            m.groups()
        from_code = _currency_code(lead_symbol or trail_symbol or from_word)
        to_code = _currency_code(to_symbol or to_word)
        if from_code and to_code and from_code != to_code:
            try:
                amount = float(raw_amount.replace(",", ""))
            except ValueError:
                return None
            return _fetch_rate(from_code, to_code, amount)
        return None

    m = _RATE_RE.match(text)
    if m:
        from_code = _currency_code(m.group(1))
        to_code = _currency_code(m.group(2))
        if from_code and to_code and from_code != to_code:
            return _fetch_rate(from_code, to_code, 1.0)
    return None


# --------------------------------------------------------------------------- #
# weather
# --------------------------------------------------------------------------- #

# WMO weather interpretation codes → (label, icon key)
WEATHER_CODES = {
    0: ("Clear sky", "sun"), 1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud-sun"), 3: ("Overcast", "cloud"),
    45: ("Fog", "fog"), 48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"), 57: ("Dense freezing drizzle", "sleet"),
    61: ("Slight rain", "rain"), 63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Heavy freezing rain", "sleet"),
    71: ("Slight snow", "snow"), 73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"), 81: ("Rain showers", "rain"),
    82: ("Violent rain showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm with hail", "storm"),
    99: ("Thunderstorm with heavy hail", "storm"),
}


@dataclass
class WeatherDay:
    date: str
    weekday: str
    high_c: float
    low_c: float
    code: int
    label: str
    icon: str
    precipitation: int


@dataclass
class WeatherResult:
    place: str
    country: str
    temperature_c: float
    feels_like_c: float
    label: str
    icon: str
    humidity: int
    wind_kmh: float
    precipitation: float
    is_day: bool
    local_time: str
    days: list[WeatherDay] = field(default_factory=list)

    @property
    def temperature_f(self) -> float:
        return self.temperature_c * 9 / 5 + 32

    @property
    def feels_like_f(self) -> float:
        return self.feels_like_c * 9 / 5 + 32


_WEATHER_RE = re.compile(
    r"^\s*(?:what(?:'s|s| is)?\s+the\s+)?"
    r"(?:weather|forecast|temperature|temp)\s*"
    r"(?:report|today|now|outside)?\s*"
    r"(?:in|at|for)\s+(.{2,50}?)\s*\??$",
    re.I,
)
_WEATHER_SUFFIX_RE = re.compile(
    r"^\s*(.{2,50}?)\s+(?:weather|forecast|temperature)\s*(?:today|now)?\s*\??$",
    re.I,
)


def _geocode(place: str):
    url = ("https://geocoding-api.open-meteo.com/v1/search?"
           + urllib.parse.urlencode({"name": place, "count": 1,
                                     "language": "en", "format": "json"}))
    data = _get_json(url, cache_key=f"geo:{place.lower()}", ttl=86400 * 7)
    results = (data or {}).get("results") or []
    return results[0] if results else None


def parse_weather(query: str) -> WeatherResult | None:
    """``weather in tokyo``, ``berlin forecast``."""
    text = query.strip()
    if len(text) > 80:
        return None
    m = _WEATHER_RE.match(text) or _WEATHER_SUFFIX_RE.match(text)
    if not m:
        return None
    place = m.group(1).strip()
    if not place or len(place) < 2:
        return None

    location = _geocode(place)
    if not location:
        return None

    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "is_day,precipitation,weather_code,wind_speed_10m"),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 6,
    }))
    data = _get_json(
        url,
        cache_key=f"wx:{round(location['latitude'], 2)}:"
                  f"{round(location['longitude'], 2)}",
        ttl=900,
    )
    if not data or "current" not in data:
        return None

    current = data["current"]
    code = int(current.get("weather_code", 0))
    label, icon = WEATHER_CODES.get(code, ("Unknown", "cloud"))

    days: list[WeatherDay] = []
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    from datetime import date as _date
    for i, day in enumerate(times[:6]):
        day_code = int((daily.get("weather_code") or [0])[i])
        day_label, day_icon = WEATHER_CODES.get(day_code, ("Unknown", "cloud"))
        try:
            weekday = _date.fromisoformat(day).strftime("%a")
        except ValueError:
            weekday = ""
        days.append(WeatherDay(
            date=day, weekday=weekday,
            high_c=round(float((daily.get("temperature_2m_max") or [0])[i])),
            low_c=round(float((daily.get("temperature_2m_min") or [0])[i])),
            code=day_code, label=day_label, icon=day_icon,
            precipitation=int(
                (daily.get("precipitation_probability_max") or [0])[i] or 0
            ),
        ))

    country = location.get("country") or ""
    admin = location.get("admin1") or ""
    place_name = location.get("name") or place.title()

    return WeatherResult(
        place=place_name,
        country=", ".join(p for p in (admin, country) if p),
        temperature_c=round(float(current.get("temperature_2m", 0)), 1),
        feels_like_c=round(float(current.get("apparent_temperature", 0)), 1),
        label=label, icon=icon,
        humidity=int(current.get("relative_humidity_2m", 0)),
        wind_kmh=round(float(current.get("wind_speed_10m", 0)), 1),
        precipitation=float(current.get("precipitation", 0)),
        is_day=bool(current.get("is_day", 1)),
        local_time=str(current.get("time", "")).replace("T", " "),
        days=days,
    )


# --------------------------------------------------------------------------- #
# sunrise / sunset
# --------------------------------------------------------------------------- #

@dataclass
class SunResult:
    place: str
    country: str
    sunrise: str
    sunset: str
    day_length: str
    date: str


_SUN_RE = re.compile(
    r"^\s*(?:what\s+time\s+(?:is|does)\s+)?"
    r"(sunrise|sunset|sun\s*rise|sun\s*set|golden\s+hour|"
    r"sunrise\s+and\s+sunset|sunrise/sunset|daylight)\s*"
    r"(?:time)?\s*(?:in|at|for)\s+(.{2,50}?)\s*\??$",
    re.I,
)
_SUN_SUFFIX_RE = re.compile(
    r"^\s*(.{2,50}?)\s+(sunrise|sunset|sunrise\s+and\s+sunset|daylight)\s*\??$",
    re.I,
)


def parse_sun(query: str) -> SunResult | None:
    """``sunrise in tokyo``, ``sunset in london``, ``berlin sunrise``."""
    text = query.strip()
    if len(text) > 80:
        return None
    m = _SUN_RE.match(text)
    place = ""
    if m:
        place = m.group(2).strip()
    else:
        m = _SUN_SUFFIX_RE.match(text)
        if m:
            place = m.group(1).strip()
    if not place or len(place) < 2:
        return None

    location = _geocode(place)
    if not location:
        return None

    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "daily": "sunrise,sunset,daylight_duration",
        "timezone": "auto",
        "forecast_days": 1,
    }))
    data = _get_json(
        url,
        cache_key=f"sun:{round(location['latitude'], 2)}:"
                  f"{round(location['longitude'], 2)}",
        ttl=3600,
    )
    daily = (data or {}).get("daily") or {}
    sunrise = (daily.get("sunrise") or [""])[0]
    sunset = (daily.get("sunset") or [""])[0]
    if not sunrise or not sunset:
        return None

    def clock(iso: str) -> str:
        return iso.split("T")[-1][:5] if "T" in iso else iso

    seconds = (daily.get("daylight_duration") or [0])[0]
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)

    country = location.get("country") or ""
    admin = location.get("admin1") or ""
    return SunResult(
        place=location.get("name") or place.title(),
        country=", ".join(p for p in (admin, country) if p),
        sunrise=clock(sunrise),
        sunset=clock(sunset),
        day_length=f"{hours}h {minutes}m",
        date=sunrise.split("T")[0] if "T" in sunrise else "",
    )
