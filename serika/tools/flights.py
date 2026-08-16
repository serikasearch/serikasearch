"""Live flight tracking from the OpenSky Network — keyless and free.

OpenSky publishes the live ADS-B state of every aircraft it hears, with an
anonymous REST endpoint. Given a flight's callsign this finds it in the live
feed and reports where it is right now: position, altitude, ground speed,
heading, and country of origin — the genuinely-free slice of "flights".

It cannot do what needs a paid API: scheduled times, gates, or ticket prices.
What it does is real and live. The global feed is large, so it's fetched once
every 15 seconds and shared across requests.

Callsigns: airlines transmit an ICAO callsign (``BAW`` for British Airways),
while tickets show the IATA code (``BA``). The common prefixes are mapped so a
traveller can type either — ``BA2490`` or ``BAW2490``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass

__all__ = ["Flight", "track_flight", "set_cache"]

_TIMEOUT = 8.0
_UA = "serikasearch/1.1 (https://serikasearch.com/about)"
_STATES_URL = "https://opensky-network.org/api/states/all"

_cache = None
# Process-local fallback cache for the (large) global state vector.
_local = {"at": 0.0, "data": None}


def set_cache(redis_client) -> None:
    global _cache
    _cache = redis_client


# IATA -> ICAO airline prefixes for the airlines people actually track.
_IATA_TO_ICAO = {
    "BA": "BAW", "AA": "AAL", "UA": "UAL", "DL": "DAL", "WN": "SWA",
    "AS": "ASA", "B6": "JBU", "NK": "NKS", "F9": "FFT", "HA": "HAL",
    "AC": "ACA", "LH": "DLH", "AF": "AFR", "KL": "KLM", "IB": "IBE",
    "AZ": "ITY", "EK": "UAE", "QR": "QTR", "EY": "ETD", "TK": "THY",
    "SQ": "SIA", "CX": "CPA", "QF": "QFA", "NZ": "ANZ", "JL": "JAL",
    "NH": "ANA", "KE": "KAL", "OZ": "AAR", "CA": "CCA", "MU": "CES",
    "CZ": "CSN", "AI": "AIC", "6E": "IGO", "FR": "RYR", "U2": "EZY",
    "W6": "WZZ", "VS": "VIR", "TP": "TAP", "SK": "SAS", "LX": "SWR",
    "OS": "AUA", "SN": "BEL", "EI": "EIN", "AY": "FIN", "LO": "LOT",
    "VY": "VLG", "DY": "NAX", "WizzAir": "WZZ", "G4": "AAY", "SV": "SVA",
    "ET": "ETH", "MS": "MSR", "SA": "SAA", "GA": "GIA", "TG": "THA",
    "MH": "MAS", "PR": "PAL", "VN": "HVN", "BR": "EVA", "CI": "CAL",
}

# origin-country flag emoji from ISO country name (a small, common subset).
_FLAGS = {
    "United States": "🇺🇸", "United Kingdom": "🇬🇧", "Germany": "🇩🇪",
    "France": "🇫🇷", "Netherlands": "🇳🇱", "Spain": "🇪🇸", "Italy": "🇮🇹",
    "Ireland": "🇮🇪", "Canada": "🇨🇦", "Australia": "🇦🇺", "Japan": "🇯🇵",
    "China": "🇨🇳", "India": "🇮🇳", "Brazil": "🇧🇷", "Turkey": "🇹🇷",
    "United Arab Emirates": "🇦🇪", "Qatar": "🇶🇦", "Singapore": "🇸🇬",
    "Switzerland": "🇨🇭", "Austria": "🇦🇹", "Belgium": "🇧🇪", "Sweden": "🇸🇪",
    "Norway": "🇳🇴", "Denmark": "🇩🇰", "Finland": "🇫🇮", "Poland": "🇵🇱",
    "Portugal": "🇵🇹",
}


@dataclass
class Flight:
    callsign: str
    origin_country: str
    flag: str
    longitude: float
    latitude: float
    altitude_m: float
    velocity_ms: float
    heading: float
    vertical_ms: float
    on_ground: bool
    last_contact: int
    icao24: str

    @property
    def altitude_ft(self) -> int:
        return round(self.altitude_m * 3.28084)

    @property
    def speed_kmh(self) -> int:
        return round(self.velocity_ms * 3.6)

    @property
    def speed_knots(self) -> int:
        return round(self.velocity_ms * 1.94384)

    @property
    def compass(self) -> str:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[round(self.heading / 45) % 8]


_FLIGHT_RE = re.compile(
    r"^\s*(?:track\s+(?:flight\s+)?|(?:where\s+is\s+)?flight\s+|"
    r"flight\s+status\s+|flight\s+tracker\s+)([A-Za-z]{2,3}\s?\d{1,4}[A-Za-z]?)"
    r"\s*\??$",
    re.I,
)


def _normalise_callsign(raw: str) -> list[str]:
    """Return candidate OpenSky callsigns for a user-typed flight number."""
    text = re.sub(r"\s+", "", raw).upper()
    m = re.match(r"^([A-Z]{2,3})(\d{1,4}[A-Z]?)$", text)
    if not m:
        return [text]
    prefix, number = m.group(1), m.group(2)
    candidates = [prefix + number]
    if len(prefix) == 2 and prefix in _IATA_TO_ICAO:
        candidates.insert(0, _IATA_TO_ICAO[prefix] + number)
    return candidates


def _fetch_states() -> list | None:
    """The global live state vector, cached for 15 seconds."""
    now = time.time()
    if _local["data"] is not None and now - _local["at"] < 15:
        return _local["data"]
    if _cache is not None:
        try:
            hit = _cache.get("osky:states")
            if hit:
                data = json.loads(hit)
                _local["data"], _local["at"] = data, now
                return data
        except Exception:
            pass
    request = urllib.request.Request(
        _STATES_URL, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read()
        data = (json.loads(raw) or {}).get("states") or []
    except Exception:
        return _local["data"]        # serve the stale copy if the feed is down
    _local["data"], _local["at"] = data, now
    if _cache is not None:
        try:
            _cache.setex("osky:states", 15, json.dumps(data))
        except Exception:
            pass
    return data


def track_flight(query: str) -> Flight | None:
    """``track flight BA2490`` / ``where is flight UAL123`` → its live state."""
    match = _FLIGHT_RE.match(query.strip())
    if not match:
        return None
    candidates = _normalise_callsign(match.group(1))

    states = _fetch_states()
    if not states:
        return None

    wanted = {c.upper() for c in candidates}
    for state in states:
        callsign = (state[1] or "").strip().upper()
        if callsign in wanted:
            return Flight(
                callsign=callsign,
                origin_country=state[2] or "",
                flag=_FLAGS.get(state[2] or "", "✈"),
                longitude=state[5] if state[5] is not None else 0.0,
                latitude=state[6] if state[6] is not None else 0.0,
                altitude_m=(state[13] if state[13] is not None
                            else state[7]) or 0.0,
                velocity_ms=state[9] or 0.0,
                heading=state[10] or 0.0,
                vertical_ms=state[11] or 0.0,
                on_ground=bool(state[8]),
                last_contact=state[4] or 0,
                icao24=state[0] or "",
            )
    return None
