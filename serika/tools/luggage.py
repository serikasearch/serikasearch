"""Carry-on baggage limits for major airlines — bundled, not fetched.

Airline allowances change, but slowly, and there is no free live API for them.
So this is a curated snapshot: dimensions in centimetres (length × width ×
depth, wheels and handles included) and weight in kilograms. Each entry notes
the date it reflects, and the widget tells the reader to confirm with the
airline before flying, because a bag that is one centimetre over is the
airline's call, not ours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Airline", "AIRLINES", "lookup_airline", "parse_luggage"]

# Reflects published allowances as of early 2026. Dimensions are the cabin
# (carry-on) bag; personal-item limits are noted in `personal` where they
# differ notably. Weight 0 means "no strict weight limit, must fit and lift".
_SNAPSHOT = "early 2026"


@dataclass
class Airline:
    key: str
    name: str
    length: int          # cm
    width: int           # cm
    depth: int           # cm
    weight: float        # kg, 0 = not strictly enforced
    personal: str = ""   # personal-item note
    note: str = ""


# aliases -> Airline
_TABLE: list[tuple[tuple[str, ...], Airline]] = [
    (("ryanair",), Airline("ryanair", "Ryanair", 55, 40, 20, 10,
        personal="Free small bag 40×20×25 cm; larger cabin bag needs Priority.")),
    (("easyjet", "easy jet"), Airline("easyjet", "easyJet", 45, 36, 20, 15,
        personal="Larger 56×45×25 cm cabin bag with Up Front/extra seat.")),
    (("wizz", "wizzair", "wizz air"), Airline("wizz", "Wizz Air", 40, 30, 20, 10,
        personal="Larger 55×40×23 cm trolley needs WIZZ Priority.")),
    (("british airways", "ba"), Airline("ba", "British Airways", 56, 45, 25, 23,
        personal="Plus a handbag/laptop bag 40×30×15 cm.")),
    (("lufthansa",), Airline("lufthansa", "Lufthansa", 55, 40, 23, 8,
        personal="Plus a personal item 40×30×10 cm.")),
    (("klm",), Airline("klm", "KLM", 55, 35, 25, 12,
        personal="Plus an accessory 40×30×15 cm.")),
    (("air france", "airfrance"), Airline("airfrance", "Air France", 55, 35, 25, 12,
        personal="Plus a personal item 40×30×15 cm.")),
    (("emirates",), Airline("emirates", "Emirates", 55, 38, 20, 7,
        personal="Economy: one bag. Business/First: two pieces.")),
    (("qatar", "qatar airways"), Airline("qatar", "Qatar Airways", 50, 37, 25, 7,
        personal="Economy 7 kg; Business up to 15 kg across two bags.")),
    (("turkish", "turkish airlines"), Airline("turkish", "Turkish Airlines", 55, 40, 23, 8,
        personal="Plus a personal item.")),
    (("delta",), Airline("delta", "Delta", 56, 35, 23, 0,
        personal="Plus a personal item; no weight limit in the US.")),
    (("united",), Airline("united", "United", 56, 35, 22, 0,
        personal="Basic Economy: personal item only, no overhead bag.")),
    (("american", "american airlines", "aa"), Airline("aa", "American Airlines", 56, 36, 23, 0,
        personal="Plus a personal item 45×35×20 cm.")),
    (("southwest",), Airline("southwest", "Southwest", 61, 41, 28, 0,
        personal="Generous 24×16×11 in cabin bag; two free checked bags.")),
    (("jetblue",), Airline("jetblue", "JetBlue", 56, 36, 22, 0,
        personal="Blue Basic: overhead bag may not be included.")),
    (("alaska", "alaska airlines"), Airline("alaska", "Alaska Airlines", 56, 35, 22, 0,
        personal="Plus a personal item 43×33×16 cm.")),
    (("ana",), Airline("ana", "ANA", 55, 40, 25, 10,
        personal="Domestic Japan: 100-seat+ aircraft.")),
    (("jal", "japan airlines"), Airline("jal", "Japan Airlines", 55, 40, 25, 10,
        personal="Plus a personal item.")),
    (("singapore", "singapore airlines"), Airline("singapore", "Singapore Airlines", 55, 40, 20, 7,
        personal="Sum of dimensions must not exceed 115 cm.")),
    (("cathay", "cathay pacific"), Airline("cathay", "Cathay Pacific", 56, 36, 23, 7,
        personal="Plus a small personal item.")),
    (("qantas",), Airline("qantas", "Qantas", 56, 36, 23, 7,
        personal="Two bags up to 7 kg each on many routes.")),
    (("air canada", "aircanada"), Airline("aircanada", "Air Canada", 55, 40, 23, 0,
        personal="Plus a personal item 43×33×16 cm; no weight limit.")),
    (("ryanair", ), Airline("ryanair", "Ryanair", 55, 40, 20, 10)),
    (("vueling",), Airline("vueling", "Vueling", 55, 40, 20, 10,
        personal="Larger cabin bag needs Optima/Priority.")),
    (("norwegian",), Airline("norwegian", "Norwegian", 55, 40, 23, 10,
        personal="LowFare: personal item only.")),
    (("iberia",), Airline("iberia", "Iberia", 56, 40, 25, 10,
        personal="Plus a personal item 40×30×15 cm.")),
    (("aer lingus", "aerlingus"), Airline("aerlingus", "Aer Lingus", 55, 40, 24, 10,
        personal="Plus a small handbag on many fares.")),
    (("frontier",), Airline("frontier", "Frontier", 61, 46, 25, 16,
        personal="Overhead carry-on costs extra; personal item free.")),
    (("spirit",), Airline("spirit", "Spirit", 56, 46, 25, 0,
        personal="Overhead carry-on costs extra; personal item free.")),
]

AIRLINES: dict[str, Airline] = {}
for _aliases, _airline in _TABLE:
    for _alias in _aliases:
        AIRLINES.setdefault(_alias, _airline)


def lookup_airline(text: str) -> Airline | None:
    key = re.sub(r"\s+", " ", (text or "").strip().lower())
    if key in AIRLINES:
        return AIRLINES[key]
    # tolerate "with ryanair", "ryanair airlines", trailing words
    key = re.sub(r"\b(airlines?|airways|air)\b", "", key).strip()
    key = re.sub(r"\s+", " ", key)
    return AIRLINES.get(key)


_LUGGAGE_RE = re.compile(
    r"^\s*(?:(?:carry[\s-]?on|cabin\s+bag|hand\s+luggage|baggage|luggage)\s+"
    r"(?:size|dimensions?|allowance|limit)?\s*(?:for|on)?\s*(.+?)"
    r"|(.+?)\s+(?:carry[\s-]?on|cabin\s+bag|hand\s+luggage|baggage\s+allowance))"
    r"\s*\??$",
    re.I,
)


@dataclass
class LuggageResult:
    airline: Airline | None
    snapshot: str


def parse_luggage(query: str):
    """``carry on size ryanair``, ``ryanair baggage allowance``, or the bare
    ``carry on size`` (which opens the picker)."""
    text = query.strip()
    if len(text) > 80:
        return None
    lowered = text.lower()

    # Bare trigger → open the interactive picker.
    if re.fullmatch(r"(?:carry[\s-]?on(?:\s+size)?|cabin\s+bag(?:\s+size)?|"
                    r"hand\s+luggage(?:\s+size)?|luggage\s+size|baggage\s+size)",
                    lowered):
        return LuggageResult(airline=None, snapshot=_SNAPSHOT)

    m = _LUGGAGE_RE.match(text)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or "").strip()
    if not name:
        return LuggageResult(airline=None, snapshot=_SNAPSHOT)
    airline = lookup_airline(name)
    if airline is None:
        return None
    return LuggageResult(airline=airline, snapshot=_SNAPSHOT)
