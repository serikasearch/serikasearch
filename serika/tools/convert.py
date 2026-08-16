"""Developer- and everyday-converters that answer straight from the query.

Colour codes, number bases, Roman numerals, text encodings, hashes and byte
sizes. Everything here is pure computation on the query string — no network,
no database — so these answers are effectively free to render.
"""

from __future__ import annotations

import base64
import binascii
import colorsys
import hashlib
import html as _html
import re
import urllib.parse
from dataclasses import dataclass, field

__all__ = ["ColorInfo", "parse_color", "color_shades", "CSS_COLORS",
           "parse_base", "parse_roman", "parse_encoding", "parse_hash",
           "roman_to_int", "int_to_roman"]


# --------------------------------------------------------------------------- #
# colours
# --------------------------------------------------------------------------- #

@dataclass
class ColorInfo:
    hex: str
    rgb: tuple[int, int, int]
    hsl: tuple[int, int, int]
    hsv: tuple[int, int, int]
    cmyk: tuple[int, int, int, int]
    name: str = ""
    luminance: float = 0.0
    on_dark: bool = True     # is white text readable on this colour?


# The CSS named colours people actually search for, plus every hue name that
# maps to a distinct value. Kept inline so colour lookup needs no data file.
CSS_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "lime": "#00ff00", "blue": "#0000ff", "yellow": "#ffff00",
    "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "silver": "#c0c0c0", "gray": "#808080",
    "grey": "#808080", "maroon": "#800000", "olive": "#808000",
    "green": "#008000", "purple": "#800080", "teal": "#008080",
    "navy": "#000080", "orange": "#ffa500", "pink": "#ffc0cb",
    "hotpink": "#ff69b4", "gold": "#ffd700", "indigo": "#4b0082",
    "violet": "#ee82ee", "brown": "#a52a2a", "beige": "#f5f5dc",
    "coral": "#ff7f50", "crimson": "#dc143c", "khaki": "#f0e68c",
    "lavender": "#e6e6fa", "salmon": "#fa8072", "tan": "#d2b48c",
    "turquoise": "#40e0d0", "plum": "#dda0dd", "orchid": "#da70d6",
    "tomato": "#ff6347", "chocolate": "#d2691e", "skyblue": "#87ceeb",
    "steelblue": "#4682b4", "seagreen": "#2e8b57", "slategray": "#708090",
    "midnightblue": "#191970", "royalblue": "#4169e1", "firebrick": "#b22222",
    "forestgreen": "#228b22", "darkgreen": "#006400", "darkblue": "#00008b",
    "darkred": "#8b0000", "darkorange": "#ff8c00", "deeppink": "#ff1493",
    "dodgerblue": "#1e90ff", "limegreen": "#32cd32", "mediumpurple": "#9370db",
    "rebeccapurple": "#663399", "springgreen": "#00ff7f", "wheat": "#f5deb3",
}
_HEX_TO_NAME = {v: k for k, v in CSS_COLORS.items()}

_HEX_RE = re.compile(r"^#?([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.I)
_RGB_RE = re.compile(
    r"^rgba?\(?\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})", re.I
)
_HSL_RE = re.compile(
    r"^hsla?\(?\s*(\d{1,3})\s*[, ]\s*(\d{1,3})%?\s*[, ]\s*(\d{1,3})%?", re.I
)
_COLOR_PREFIX = re.compile(
    r"^\s*(?:colou?r\s+|hex\s+(?:code\s+)?(?:for\s+)?|what\s+colou?r\s+is\s+)", re.I
)


def _build_color(r: int, g: int, b: int) -> ColorInfo:
    r, g, b = (max(0, min(255, int(v))) for v in (r, g, b))
    hex_code = f"#{r:02x}{g:02x}{b:02x}"
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hv, sv, vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    k = 1 - max(r, g, b) / 255
    if k >= 1:
        c = m = y = 0.0
    else:
        c = (1 - r / 255 - k) / (1 - k)
        m = (1 - g / 255 - k) / (1 - k)
        y = (1 - b / 255 - k) / (1 - k)

    # Relative luminance per WCAG 2.1, used to pick readable overlay text.
    def channel(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    return ColorInfo(
        hex=hex_code,
        rgb=(r, g, b),
        hsl=(round(h * 360), round(s * 100), round(l * 100)),
        hsv=(round(hv * 360), round(sv * 100), round(vv * 100)),
        cmyk=(round(c * 100), round(m * 100), round(y * 100), round(k * 100)),
        name=_HEX_TO_NAME.get(hex_code, ""),
        luminance=round(lum, 4),
        on_dark=lum < 0.35,
    )


def parse_color(query: str) -> ColorInfo | None:
    """Read ``#a274ff``, ``rgb(162,116,255)``, ``hsl(...)`` or ``rebeccapurple``."""
    text = _COLOR_PREFIX.sub("", query.strip()).strip().rstrip("?").strip()
    if not text or len(text) > 40:
        return None

    named = CSS_COLORS.get(text.lower().replace(" ", ""))
    if named:
        text = named

    m = _HEX_RE.match(text)
    if m:
        digits = m.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits[:3])
        digits = digits[:6]
        return _build_color(int(digits[0:2], 16), int(digits[2:4], 16),
                            int(digits[4:6], 16))

    m = _RGB_RE.match(text)
    if m:
        return _build_color(*(int(v) for v in m.groups()))

    m = _HSL_RE.match(text)
    if m:
        h, s, l = (int(v) for v in m.groups())
        r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l / 100, s / 100)
        return _build_color(round(r * 255), round(g * 255), round(b * 255))

    return None


def color_shades(color: ColorInfo, count: int = 9) -> list[str]:
    """A light→dark ramp of the same hue, for the colour tool's palette row."""
    h, s, _ = colorsys.rgb_to_hls(*(v / 255 for v in color.rgb))
    out = []
    for i in range(count):
        lightness = 0.94 - (i / (count - 1)) * 0.86
        r, g, b = colorsys.hls_to_rgb(h, lightness, s)
        out.append(f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}")
    return out


# --------------------------------------------------------------------------- #
# number bases
# --------------------------------------------------------------------------- #

@dataclass
class BaseResult:
    value: int
    source_base: int
    binary: str
    octal: str
    decimal: str
    hexadecimal: str


_BASE_NAMES = {
    "binary": 2, "bin": 2, "base2": 2,
    "octal": 8, "oct": 8, "base8": 8,
    "decimal": 10, "dec": 10, "base10": 10,
    "hexadecimal": 16, "hex": 16, "base16": 16,
}
_BASE_RE = re.compile(
    r"^\s*(?:convert\s+)?([0-9a-fA-Fx#]+)\s+(?:from\s+)?"
    r"(binary|bin|octal|oct|decimal|dec|hexadecimal|hex|base\d+)?\s*"
    r"(?:to|in|into|as)\s+"
    r"(binary|bin|octal|oct|decimal|dec|hexadecimal|hex|base\d+)\s*\??$",
    re.I,
)


def _detect_base(token: str) -> tuple[int, int] | None:
    """Return ``(value, base)`` from a literal like ``0xff`` or ``1011``."""
    t = token.strip().lower().replace("#", "0x")
    try:
        if t.startswith("0x"):
            return int(t, 16), 16
        if t.startswith("0b"):
            return int(t, 2), 2
        if t.startswith("0o"):
            return int(t, 8), 8
        return int(t, 10), 10
    except ValueError:
        return None


def parse_base(query: str) -> BaseResult | None:
    """Read ``255 in binary``, ``0xff to decimal``, ``1011 binary to hex``."""
    m = _BASE_RE.match(query)
    if not m:
        return None
    literal, src_name, _dst_name = m.groups()

    src_base = 0
    if src_name:
        src_base = (_BASE_NAMES.get(src_name.lower())
                    or int(src_name[4:]) if src_name.lower().startswith("base")
                    else _BASE_NAMES.get(src_name.lower(), 0))
    if src_base:
        try:
            value = int(literal.lower().replace("0x", "").replace("0b", ""),
                        src_base)
        except ValueError:
            return None
    else:
        detected = _detect_base(literal)
        if detected is None:
            return None
        value, src_base = detected

    if abs(value) > 2 ** 256:
        return None
    return BaseResult(
        value=value, source_base=src_base,
        binary=format(value, "b"), octal=format(value, "o"),
        decimal=str(value), hexadecimal=format(value, "X"),
    )


# --------------------------------------------------------------------------- #
# Roman numerals
# --------------------------------------------------------------------------- #

_ROMAN_PAIRS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_RE = re.compile(r"^\s*(?:what\s+is\s+)?([MDCLXVI]+)\s*(?:in\s+"
                       r"(?:numbers|decimal|arabic))?\s*\??$", re.I)
_TO_ROMAN_RE = re.compile(r"^\s*(?:convert\s+)?(\d{1,4})\s+(?:in|to|as)\s+"
                          r"roman(?:\s+numerals?)?\s*\??$", re.I)


def int_to_roman(n: int) -> str:
    if not 1 <= n <= 3999:
        raise ValueError("Roman numerals cover 1–3999")
    out = []
    for value, symbol in _ROMAN_PAIRS:
        count, n = divmod(n, value)
        out.append(symbol * count)
    return "".join(out)


def roman_to_int(text: str) -> int:
    text = text.strip().upper()
    if not text or any(ch not in _ROMAN_VALUES for ch in text):
        raise ValueError("not a Roman numeral")
    total = 0
    previous = 0
    for ch in reversed(text):
        value = _ROMAN_VALUES[ch]
        total += value if value >= previous else -value
        previous = max(previous, value)
    if int_to_roman(total) != text:
        raise ValueError("not a well-formed Roman numeral")
    return total


def parse_roman(query: str) -> tuple[str, str] | None:
    """Return ``(input, output)`` for a Roman-numeral conversion."""
    m = _TO_ROMAN_RE.match(query)
    if m:
        try:
            n = int(m.group(1))
            return str(n), int_to_roman(n)
        except ValueError:
            return None
    m = _ROMAN_RE.match(query)
    if m and len(m.group(1)) >= 2:
        try:
            return m.group(1).upper(), f"{roman_to_int(m.group(1)):,}"
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# text encodings
# --------------------------------------------------------------------------- #

@dataclass
class EncodingResult:
    operation: str      # "Base64 encode", "URL decode", …
    source: str
    output: str
    extra: list[tuple[str, str]] = field(default_factory=list)


_ENCODE_RE = re.compile(
    r"^\s*(base64|b64|url|uri|html|hex|rot13|binary)\s*"
    r"[- ]?(encode|decode|escape|unescape)\s*:?\s+(.+)$",
    re.I | re.S,
)


def _rot13(text: str) -> str:
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + 13) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + 13) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def parse_encoding(query: str) -> EncodingResult | None:
    """Handle ``base64 encode hello``, ``url decode a%20b``, ``rot13 …``."""
    m = _ENCODE_RE.match(query.strip())
    if not m:
        return None
    kind, action, payload = m.group(1).lower(), m.group(2).lower(), m.group(3)
    payload = payload.strip()
    if not payload or len(payload) > 4000:
        return None
    decoding = action in ("decode", "unescape")

    try:
        if kind in ("base64", "b64"):
            if decoding:
                padded = payload + "=" * (-len(payload) % 4)
                output = base64.b64decode(padded).decode("utf-8", "replace")
            else:
                output = base64.b64encode(payload.encode()).decode()
            label = f"Base64 {'decode' if decoding else 'encode'}"
        elif kind in ("url", "uri"):
            output = (urllib.parse.unquote_plus(payload) if decoding
                      else urllib.parse.quote(payload, safe=""))
            label = f"URL {'decode' if decoding else 'encode'}"
        elif kind == "html":
            output = (_html.unescape(payload) if decoding
                      else _html.escape(payload))
            label = f"HTML {'decode' if decoding else 'encode'}"
        elif kind == "hex":
            if decoding:
                output = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", payload)) \
                    .decode("utf-8", "replace")
            else:
                output = payload.encode().hex()
            label = f"Hex {'decode' if decoding else 'encode'}"
        elif kind == "binary":
            if decoding:
                bits = re.sub(r"[^01]", "", payload)
                output = "".join(
                    chr(int(bits[i:i + 8], 2)) for i in range(0, len(bits) - 7, 8)
                )
            else:
                output = " ".join(format(b, "08b") for b in payload.encode())
            label = f"Binary {'decode' if decoding else 'encode'}"
        else:  # rot13 is its own inverse
            output = _rot13(payload)
            label = "ROT13"
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None

    if not output:
        return None
    return EncodingResult(operation=label, source=payload, output=output[:4000])


# --------------------------------------------------------------------------- #
# hashes
# --------------------------------------------------------------------------- #

_HASH_RE = re.compile(
    r"^\s*(md5|sha1|sha-1|sha256|sha-256|sha512|sha-512|crc32)\s*"
    r"(?:hash|checksum|of|:)?\s+(.+)$",
    re.I | re.S,
)


def parse_hash(query: str) -> EncodingResult | None:
    """``sha256 hello`` → the digest, plus the other common digests alongside."""
    m = _HASH_RE.match(query.strip())
    if not m:
        return None
    algorithm = m.group(1).lower().replace("-", "")
    payload = m.group(2).strip()
    if not payload or len(payload) > 4000:
        return None

    data = payload.encode("utf-8")
    if algorithm == "crc32":
        primary = format(binascii.crc32(data) & 0xFFFFFFFF, "08x")
    else:
        primary = hashlib.new(algorithm, data).hexdigest()

    extra = [
        (name.upper(), hashlib.new(name, data).hexdigest())
        for name in ("md5", "sha1", "sha256", "sha512")
        if name != algorithm
    ]
    extra.append(("CRC32", format(binascii.crc32(data) & 0xFFFFFFFF, "08x")))
    return EncodingResult(
        operation=f"{algorithm.upper()} hash", source=payload,
        output=primary, extra=extra,
    )
