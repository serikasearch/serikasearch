"""Unit conversion for the instant-answer strip.

``5 km to miles``, ``180 f in c``, ``2 cups to ml``, ``1 GiB in MB`` — each
resolves locally from the table below, with no network call. Units are stored
as *(dimension, factor-to-base, offset)*; converting is a two-step trip through
the dimension's base unit, so adding a unit never means writing a new formula.

Temperature is the one dimension with offsets (°C→K adds 273.15), which is why
the linear form is ``value * factor + offset`` rather than a plain multiply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ConversionError", "Conversion", "convert", "parse_conversion",
           "unit_names", "DIMENSION_LABELS"]


class ConversionError(Exception):
    """Raised when units are unknown or belong to different dimensions."""


@dataclass
class Unit:
    key: str
    dimension: str
    factor: float        # multiply by this to reach the dimension's base unit
    offset: float = 0.0  # then add this (temperature only)
    symbol: str = ""     # short display form, e.g. "km"
    plural: str = ""     # long display form, e.g. "kilometres"


@dataclass
class Conversion:
    value: float
    from_unit: Unit
    to_unit: Unit
    result: float
    dimension: str


DIMENSION_LABELS = {
    "length": "Length", "mass": "Mass", "temperature": "Temperature",
    "volume": "Volume", "area": "Area", "time": "Time", "speed": "Speed",
    "data": "Digital storage", "energy": "Energy", "power": "Power",
    "pressure": "Pressure", "angle": "Angle", "frequency": "Frequency",
}


# --------------------------------------------------------------------------- #
# unit table:  aliases -> (dimension, factor, symbol, plural, offset)
# --------------------------------------------------------------------------- #

_TABLE: list[tuple[tuple[str, ...], str, float, str, str, float]] = [
    # ---- length (base: metre) ----
    (("nm", "nanometre", "nanometer", "nanometres", "nanometers"),
     "length", 1e-9, "nm", "nanometres", 0),
    (("um", "µm", "micrometre", "micrometer", "micron", "microns"),
     "length", 1e-6, "µm", "micrometres", 0),
    (("mm", "millimetre", "millimeter", "millimetres", "millimeters"),
     "length", 1e-3, "mm", "millimetres", 0),
    (("cm", "centimetre", "centimeter", "centimetres", "centimeters"),
     "length", 1e-2, "cm", "centimetres", 0),
    (("m", "metre", "meter", "metres", "meters"),
     "length", 1.0, "m", "metres", 0),
    (("km", "kilometre", "kilometer", "kilometres", "kilometers"),
     "length", 1000.0, "km", "kilometres", 0),
    (("in", "inch", "inches", '"'),
     "length", 0.0254, "in", "inches", 0),
    (("ft", "foot", "feet", "'"),
     "length", 0.3048, "ft", "feet", 0),
    (("yd", "yard", "yards"), "length", 0.9144, "yd", "yards", 0),
    (("mi", "mile", "miles"), "length", 1609.344, "mi", "miles", 0),
    (("nmi", "nauticalmile", "nauticalmiles"),
     "length", 1852.0, "nmi", "nautical miles", 0),
    (("ly", "lightyear", "lightyears"),
     "length", 9.4607304725808e15, "ly", "light-years", 0),
    (("au", "astronomicalunit"), "length", 1.495978707e11, "AU",
     "astronomical units", 0),
    (("pc", "parsec", "parsecs"), "length", 3.0856775814913673e16, "pc",
     "parsecs", 0),

    # ---- mass (base: kilogram) ----
    (("mg", "milligram", "milligrams"), "mass", 1e-6, "mg", "milligrams", 0),
    (("g", "gram", "grams", "gramme", "grammes"), "mass", 1e-3, "g", "grams", 0),
    (("kg", "kilogram", "kilograms", "kilo", "kilos"),
     "mass", 1.0, "kg", "kilograms", 0),
    (("t", "tonne", "tonnes", "metricton", "metrictons"),
     "mass", 1000.0, "t", "tonnes", 0),
    (("oz", "ounce", "ounces"), "mass", 0.028349523125, "oz", "ounces", 0),
    (("lb", "lbs", "pound", "pounds"),
     "mass", 0.45359237, "lb", "pounds", 0),
    (("st", "stone", "stones"), "mass", 6.35029318, "st", "stone", 0),
    (("ton", "tons", "shortton"), "mass", 907.18474, "ton", "short tons", 0),

    # ---- temperature (base: kelvin) ----
    (("c", "°c", "celsius", "centigrade"),
     "temperature", 1.0, "°C", "degrees Celsius", 273.15),
    (("f", "°f", "fahrenheit"),
     "temperature", 5 / 9, "°F", "degrees Fahrenheit", 255.372222222222),
    (("k", "kelvin", "kelvins"), "temperature", 1.0, "K", "kelvin", 0),

    # ---- volume (base: litre) ----
    (("ml", "millilitre", "milliliter", "millilitres", "milliliters", "cc"),
     "volume", 1e-3, "ml", "millilitres", 0),
    (("cl", "centilitre", "centiliter"), "volume", 1e-2, "cl", "centilitres", 0),
    (("l", "litre", "liter", "litres", "liters"),
     "volume", 1.0, "L", "litres", 0),
    (("m3", "cubicmetre", "cubicmeter"), "volume", 1000.0, "m³",
     "cubic metres", 0),
    (("tsp", "teaspoon", "teaspoons"), "volume", 0.00492892159375, "tsp",
     "teaspoons", 0),
    (("tbsp", "tablespoon", "tablespoons"), "volume", 0.01478676478125,
     "tbsp", "tablespoons", 0),
    (("cup", "cups"), "volume", 0.2365882365, "cup", "cups", 0),
    (("pt", "pint", "pints"), "volume", 0.473176473, "pt", "US pints", 0),
    (("qt", "quart", "quarts"), "volume", 0.946352946, "qt", "US quarts", 0),
    (("gal", "gallon", "gallons"), "volume", 3.785411784, "gal",
     "US gallons", 0),
    (("impgal", "imperialgallon"), "volume", 4.54609, "imp gal",
     "imperial gallons", 0),
    (("floz", "fluidounce", "fluidounces"), "volume", 0.0295735295625,
     "fl oz", "US fluid ounces", 0),

    # ---- area (base: square metre) ----
    (("mm2", "sqmm"), "area", 1e-6, "mm²", "square millimetres", 0),
    (("cm2", "sqcm"), "area", 1e-4, "cm²", "square centimetres", 0),
    (("m2", "sqm", "squaremetre", "squaremeter"),
     "area", 1.0, "m²", "square metres", 0),
    (("km2", "sqkm"), "area", 1e6, "km²", "square kilometres", 0),
    (("ha", "hectare", "hectares"), "area", 1e4, "ha", "hectares", 0),
    (("ft2", "sqft", "squarefoot", "squarefeet"),
     "area", 0.09290304, "ft²", "square feet", 0),
    (("in2", "sqin"), "area", 0.00064516, "in²", "square inches", 0),
    (("acre", "acres"), "area", 4046.8564224, "acre", "acres", 0),
    (("mi2", "sqmi"), "area", 2589988.110336, "mi²", "square miles", 0),

    # ---- time (base: second) ----
    (("ns", "nanosecond", "nanoseconds"), "time", 1e-9, "ns", "nanoseconds", 0),
    (("ms", "millisecond", "milliseconds"),
     "time", 1e-3, "ms", "milliseconds", 0),
    (("s", "sec", "secs", "second", "seconds"),
     "time", 1.0, "s", "seconds", 0),
    (("min", "mins", "minute", "minutes"), "time", 60.0, "min", "minutes", 0),
    (("h", "hr", "hrs", "hour", "hours"), "time", 3600.0, "h", "hours", 0),
    (("d", "day", "days"), "time", 86400.0, "d", "days", 0),
    (("wk", "week", "weeks"), "time", 604800.0, "wk", "weeks", 0),
    (("mo", "month", "months"), "time", 2629746.0, "mo", "months", 0),
    (("yr", "year", "years"), "time", 31556952.0, "yr", "years", 0),

    # ---- speed (base: metre per second) ----
    (("mps", "m/s", "metrepersecond"), "speed", 1.0, "m/s",
     "metres per second", 0),
    (("kmh", "km/h", "kph", "kmph"), "speed", 1 / 3.6, "km/h",
     "kilometres per hour", 0),
    (("mph", "mi/h"), "speed", 0.44704, "mph", "miles per hour", 0),
    (("fps", "ft/s"), "speed", 0.3048, "ft/s", "feet per second", 0),
    (("knot", "knots", "kn", "kt"), "speed", 0.514444444444, "kn", "knots", 0),
    (("mach",), "speed", 340.29, "Mach", "Mach", 0),

    # ---- digital storage (base: byte) ----
    (("bit", "bits", "b"), "data", 0.125, "bit", "bits", 0),
    (("byte", "bytes", "B"), "data", 1.0, "B", "bytes", 0),
    (("kb", "kilobyte", "kilobytes"), "data", 1e3, "kB", "kilobytes", 0),
    (("kib", "kibibyte", "kibibytes"), "data", 1024.0, "KiB", "kibibytes", 0),
    (("mb", "megabyte", "megabytes"), "data", 1e6, "MB", "megabytes", 0),
    (("mib", "mebibyte", "mebibytes"), "data", 1048576.0, "MiB", "mebibytes", 0),
    (("gb", "gigabyte", "gigabytes"), "data", 1e9, "GB", "gigabytes", 0),
    (("gib", "gibibyte", "gibibytes"),
     "data", 1073741824.0, "GiB", "gibibytes", 0),
    (("tb", "terabyte", "terabytes"), "data", 1e12, "TB", "terabytes", 0),
    (("tib", "tebibyte", "tebibytes"),
     "data", 1099511627776.0, "TiB", "tebibytes", 0),
    (("pb", "petabyte", "petabytes"), "data", 1e15, "PB", "petabytes", 0),

    # ---- energy (base: joule) ----
    (("j", "joule", "joules"), "energy", 1.0, "J", "joules", 0),
    (("kj", "kilojoule", "kilojoules"), "energy", 1000.0, "kJ", "kilojoules", 0),
    (("cal", "calorie", "calories"), "energy", 4.184, "cal", "calories", 0),
    (("kcal", "kilocalorie", "kilocalories"),
     "energy", 4184.0, "kcal", "kilocalories", 0),
    (("wh", "watthour", "watthours"), "energy", 3600.0, "Wh", "watt-hours", 0),
    (("kwh", "kilowatthour", "kilowatthours"),
     "energy", 3.6e6, "kWh", "kilowatt-hours", 0),
    (("ev", "electronvolt"), "energy", 1.602176634e-19, "eV",
     "electronvolts", 0),
    (("btu",), "energy", 1055.05585262, "BTU", "British thermal units", 0),

    # ---- power (base: watt) ----
    (("w", "watt", "watts"), "power", 1.0, "W", "watts", 0),
    (("kw", "kilowatt", "kilowatts"), "power", 1000.0, "kW", "kilowatts", 0),
    (("mw", "megawatt", "megawatts"), "power", 1e6, "MW", "megawatts", 0),
    (("hp", "horsepower"), "power", 745.6998715823, "hp", "horsepower", 0),

    # ---- pressure (base: pascal) ----
    (("pa", "pascal", "pascals"), "pressure", 1.0, "Pa", "pascals", 0),
    (("hpa", "hectopascal"), "pressure", 100.0, "hPa", "hectopascals", 0),
    (("kpa", "kilopascal"), "pressure", 1000.0, "kPa", "kilopascals", 0),
    (("bar", "bars"), "pressure", 1e5, "bar", "bar", 0),
    (("mbar", "millibar"), "pressure", 100.0, "mbar", "millibar", 0),
    (("atm", "atmosphere", "atmospheres"),
     "pressure", 101325.0, "atm", "atmospheres", 0),
    (("psi",), "pressure", 6894.757293168, "psi",
     "pounds per square inch", 0),
    (("mmhg", "torr"), "pressure", 133.322387415, "mmHg", "mmHg", 0),

    # ---- angle (base: radian) ----
    (("rad", "radian", "radians"), "angle", 1.0, "rad", "radians", 0),
    (("deg", "degree", "degrees", "°"),
     "angle", 0.017453292519943295, "°", "degrees", 0),
    (("grad", "gradian", "gradians"),
     "angle", 0.015707963267948967, "grad", "gradians", 0),
    (("turn", "turns", "revolution", "revolutions"),
     "angle", 6.283185307179586, "turn", "turns", 0),

    # ---- frequency (base: hertz) ----
    (("hz", "hertz"), "frequency", 1.0, "Hz", "hertz", 0),
    (("khz", "kilohertz"), "frequency", 1e3, "kHz", "kilohertz", 0),
    (("mhz", "megahertz"), "frequency", 1e6, "MHz", "megahertz", 0),
    (("ghz", "gigahertz"), "frequency", 1e9, "GHz", "gigahertz", 0),
    (("rpm",), "frequency", 1 / 60, "rpm", "revolutions per minute", 0),
]

# Case-sensitive aliases that must not be lower-cased away. "B" is bytes and
# "b" is bits; everything else resolves case-insensitively.
_CASE_SENSITIVE = {"B": "byte", "b": "bit"}

UNITS: dict[str, Unit] = {}
for _aliases, _dim, _factor, _sym, _plural, _off in _TABLE:
    _canonical = _aliases[0]
    _unit = Unit(key=_canonical, dimension=_dim, factor=_factor, offset=_off,
                 symbol=_sym, plural=_plural)
    for _alias in _aliases:
        UNITS.setdefault(_alias.lower(), _unit)


def unit_names(dimension: str = "") -> list[str]:
    """Distinct canonical units, optionally filtered to one dimension."""
    seen: dict[str, Unit] = {}
    for unit in UNITS.values():
        if dimension and unit.dimension != dimension:
            continue
        seen.setdefault(unit.key, unit)
    return [u.symbol or u.key for u in seen.values()]


def _lookup(token: str) -> Unit | None:
    token = token.strip()
    if not token:
        return None
    if token in _CASE_SENSITIVE:
        return UNITS.get(_CASE_SENSITIVE[token])
    key = token.lower().replace(" ", "").replace("-", "")
    unit = UNITS.get(key)
    if unit is None and key.endswith("s"):
        unit = UNITS.get(key[:-1])
    if unit is None:
        # "square feet" / "sq ft" style, and per-hour compounds.
        key2 = (key.replace("square", "sq").replace("cubic", "cu")
                   .replace("per", "/"))
        unit = UNITS.get(key2)
    return unit


def convert(value: float, from_token: str, to_token: str) -> Conversion:
    """Convert ``value`` between two units, raising on mismatch."""
    src = _lookup(from_token)
    dst = _lookup(to_token)
    if src is None:
        raise ConversionError(f"unknown unit: {from_token}")
    if dst is None:
        raise ConversionError(f"unknown unit: {to_token}")
    if src.dimension != dst.dimension:
        raise ConversionError(
            f"cannot convert {src.dimension} to {dst.dimension}"
        )
    base = value * src.factor + src.offset
    result = (base - dst.offset) / dst.factor
    return Conversion(value=value, from_unit=src, to_unit=dst,
                      result=result, dimension=src.dimension)


# --------------------------------------------------------------------------- #
# query parsing
# --------------------------------------------------------------------------- #

# "5 km to miles" / "convert 5km in mi" / "5 km = miles" / "5km->mi"
_CONVERT_RE = re.compile(
    r"""^\s*(?:convert\s+|how\s+many\s+.*?\s+(?:is|in)\s+)?
        (-?[\d.,]+(?:[eE][+-]?\d+)?)\s*
        ([a-zA-Z°µ²³/"'\s.]{1,24}?)\s*
        (?:to|in|into|as|=|->|→)\s+
        ([a-zA-Z°µ²³/"'\s.]{1,24}?)\s*\??$""",
    re.I | re.X,
)
# "how many ml in a cup", "how many feet in a mile"
_HOW_MANY_RE = re.compile(
    r"""^\s*how\s+many\s+([a-zA-Z°µ²³/"'\s.]{1,24}?)\s+
        (?:are\s+)?(?:in|per)\s+(?:a|an|one|1)\s+
        ([a-zA-Z°µ²³/"'\s.]{1,24}?)\s*\??$""",
    re.I | re.X,
)


def parse_conversion(query: str) -> Conversion | None:
    """Try to read a conversion out of a raw search query. Returns None when
    the query isn't one, so callers can fall through to web results."""
    text = query.strip()
    if not text or len(text) > 120:
        return None

    m = _HOW_MANY_RE.match(text)
    if m:
        try:
            return convert(1.0, m.group(2), m.group(1))
        except ConversionError:
            return None

    m = _CONVERT_RE.match(text)
    if not m:
        return None
    raw_value, from_token, to_token = m.groups()
    try:
        value = float(raw_value.replace(",", ""))
    except ValueError:
        return None
    # Reject the sentence-y false positives: "5 things to do", "3 ways to win".
    if not from_token.strip() or not to_token.strip():
        return None
    try:
        return convert(value, from_token, to_token)
    except ConversionError:
        return None


def format_value(value: float) -> str:
    """Format a converted quantity with a sensible number of digits."""
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1e12 or magnitude < 1e-4:
        return f"{value:.6g}"
    if magnitude >= 100:
        text = f"{value:,.2f}"
    elif magnitude >= 1:
        text = f"{value:,.4f}"
    else:
        text = f"{value:,.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
