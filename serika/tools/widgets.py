"""Server-computed answer widgets that don't need the network.

BMI, bill splitting, anagrams, Morse code, and a few word games. Each parser
returns ``None`` fast for queries that aren't its business, so the resolver can
try them all cheaply.

The anagram solver reads a bundled word list (``data/words.txt``) once and
indexes it by sorted-letter signature, so a lookup is a single dict hit rather
than a scan.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

__all__ = ["parse_bmi", "parse_split", "parse_anagram", "parse_morse",
           "Widget"]


@dataclass
class Widget:
    kind: str
    title: str
    subtitle: str = ""
    detail: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    tool: str = ""
    copy_value: str = ""
    data: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# BMI
# --------------------------------------------------------------------------- #

# "bmi 180cm 75kg", "bmi 5'10 160lb", "bmi 1.8m 75", "bmi 75kg 180cm"
_BMI_RE = re.compile(r"^\s*(?:bmi|body\s*mass\s*index)\b(.*)$", re.I)
_HEIGHT_CM = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:cm|centimet)", re.I)
_HEIGHT_M = re.compile(r"(\d(?:\.\d+)?)\s*m(?:et(?:er|re)s?)?\b", re.I)
_HEIGHT_FTIN = re.compile(r"(\d)\s*(?:'|ft|feet|foot)\s*(\d{1,2}(?:\.\d+)?)?\s*(?:\"|''|in|inch|inches)?", re.I)
_WEIGHT_KG = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:kg|kilo|kilogram)", re.I)
_WEIGHT_LB = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:lb|lbs|pound)", re.I)
_WEIGHT_ST = re.compile(r"(\d{1,2})\s*(?:st|stone)\s*(\d{1,2}(?:\.\d+)?)?", re.I)


def _bmi_category(bmi: float) -> tuple[str, str]:
    if bmi < 18.5:
        return "Underweight", "warn"
    if bmi < 25:
        return "Healthy weight", "good"
    if bmi < 30:
        return "Overweight", "warn"
    return "Obese", "bad"


def parse_bmi(query: str):
    match = _BMI_RE.match(query)
    if not match:
        return None
    body = match.group(1)
    if not body.strip():
        return Widget(kind="bmi", title="BMI calculator",
                      subtitle="Enter a height and weight",
                      tool="bmi", data={"empty": True})

    # height, in metres
    height_m = 0.0
    m = _HEIGHT_CM.search(body)
    if m:
        height_m = float(m.group(1)) / 100
    if not height_m:
        m = _HEIGHT_FTIN.search(body)
        if m and (m.group(2) is not None or "'" in body or "ft" in body.lower()
                  or "feet" in body.lower()):
            feet = float(m.group(1))
            inches = float(m.group(2) or 0)
            height_m = (feet * 12 + inches) * 0.0254
    if not height_m:
        m = _HEIGHT_M.search(body)
        if m:
            height_m = float(m.group(1))

    # weight, in kilograms
    weight_kg = 0.0
    m = _WEIGHT_KG.search(body)
    if m:
        weight_kg = float(m.group(1))
    if not weight_kg:
        m = _WEIGHT_LB.search(body)
        if m:
            weight_kg = float(m.group(1)) * 0.45359237
    if not weight_kg:
        m = _WEIGHT_ST.search(body)
        if m:
            weight_kg = (float(m.group(1)) * 14 +
                         float(m.group(2) or 0)) * 0.45359237
    # Bare numbers, order-independent: the 2-digit one is likely weight.
    if not height_m or not weight_kg:
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", body)]
        for n in numbers:
            if not height_m and 1.2 <= n <= 2.5:
                height_m = n
            elif not height_m and 120 <= n <= 250:
                height_m = n / 100
            elif not weight_kg and 30 <= n <= 300:
                weight_kg = n

    if not height_m or not weight_kg or height_m < 0.5 or height_m > 2.6:
        return Widget(kind="bmi", title="BMI calculator",
                      subtitle="Try “bmi 180cm 75kg” or “bmi 5'10 160lb”",
                      tool="bmi", data={"empty": True})

    bmi = weight_kg / (height_m * height_m)
    label, tone = _bmi_category(bmi)
    # The healthy-weight range for this height, as a takeaway.
    low = 18.5 * height_m * height_m
    high = 24.9 * height_m * height_m

    return Widget(
        kind="bmi",
        title=f"{bmi:.1f}",
        subtitle=f"BMI · {label}",
        detail=f"{weight_kg:.0f} kg at {height_m * 100:.0f} cm",
        rows=[
            ("Category", label),
            ("Healthy range here", f"{low:.0f}–{high:.0f} kg"),
            ("Height", f"{height_m * 100:.0f} cm / "
                       f"{int(height_m / 0.3048)}′"
                       f"{round((height_m / 0.0254) % 12)}″"),
            ("Weight", f"{weight_kg:.1f} kg / {weight_kg / 0.45359237:.0f} lb"),
        ],
        tool="bmi", copy_value=f"{bmi:.1f}",
        data={"bmi": round(bmi, 1), "tone": tone,
              "position": max(0, min(100, (bmi - 15) / (40 - 15) * 100))},
    )


# --------------------------------------------------------------------------- #
# bill splitter
# --------------------------------------------------------------------------- #

_SPLIT_RE = re.compile(
    r"^\s*(?:split\s+(?:the\s+)?bill|split\s+check|bill\s+split|tip\s+(?:calculator|split))\b(.*)$",
    re.I,
)
_TIP_RE = re.compile(
    r"^\s*(?:tip\s+on|calculate\s+tip(?:\s+on)?|(\d+)%\s*tip\s+on)\s+\$?([\d.,]+)"
    r"(?:\s+at\s+(\d+)%|\s+(\d+)%)?\s*$", re.I,
)


def parse_split(query: str):
    match = _SPLIT_RE.match(query)
    if match:
        body = match.group(1)
        amount = _first_money(body)
        if amount is None:
            return Widget(kind="split", title="Bill splitter",
                          subtitle="Try “split bill 120 by 3 at 18%”",
                          tool="split", data={"empty": True})
        tip_pct = _find_percent(body)
        if tip_pct is None:
            tip_pct = 0.0
        people = _find_people(body)
        return _split_widget(amount, tip_pct, people)

    match = _TIP_RE.match(query)
    if match:
        amount = float(match.group(2).replace(",", ""))
        tip_pct = float(match.group(1) or match.group(3) or match.group(4) or 0)
        return _split_widget(amount, tip_pct, 1)
    return None


def _first_money(text: str):
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _find_percent(text: str):
    m = re.search(r"(\d{1,3})\s*%", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{1,3})\s*percent", text, re.I)
    return float(m.group(1)) if m else None


def _find_people(text: str) -> int:
    m = re.search(r"(?:by|between|for|among|/|÷)\s*(\d{1,3})", text, re.I)
    if m:
        return max(1, min(100, int(m.group(1))))
    m = re.search(r"(\d{1,3})\s*(?:people|persons?|ways|guests?|diners?)", text, re.I)
    if m:
        return max(1, min(100, int(m.group(1))))
    return 1


def _split_widget(amount: float, tip_pct: float, people: int) -> Widget:
    tip = amount * tip_pct / 100
    total = amount + tip
    per_person = total / people
    rows = [
        ("Bill", f"${amount:,.2f}"),
        (f"Tip ({tip_pct:g}%)", f"${tip:,.2f}"),
        ("Total", f"${total:,.2f}"),
    ]
    if people > 1:
        rows.append(("Each person", f"${per_person:,.2f}"))
    return Widget(
        kind="split",
        title=f"${per_person:,.2f}" if people > 1 else f"${total:,.2f}",
        subtitle=(f"per person, {people} way{'s' if people != 1 else ''}"
                  if people > 1
                  else (f"total with {tip_pct:g}% tip" if tip_pct else "total")),
        detail=(f"${amount:,.2f} bill + ${tip:,.2f} tip" if tip_pct
                else f"${amount:,.2f} bill"),
        rows=rows, tool="split",
        copy_value=f"{per_person:.2f}" if people > 1 else f"{total:.2f}",
        data={"amount": amount, "tip_pct": tip_pct, "people": people,
              "total": round(total, 2), "per_person": round(per_person, 2)},
    )


# --------------------------------------------------------------------------- #
# anagram / word unscrambler
# --------------------------------------------------------------------------- #

_ANAGRAM_RE = re.compile(
    r"^\s*(?:anagram(?:\s+solver|\s+of)?|unscramble|word\s+unscrambler|"
    r"scrabble(?:\s+solver)?|words?\s+from)\s*:?\s*([a-zA-Z ]{2,15})\s*$",
    re.I,
)

# Scrabble tile values, for ranking and for showing a score.
_TILE_VALUES = {
    **dict.fromkeys("eaionrtlsu", 1), **dict.fromkeys("dg", 2),
    **dict.fromkeys("bcmp", 3), **dict.fromkeys("fhvwy", 4),
    "k": 5, **dict.fromkeys("jx", 8), **dict.fromkeys("qz", 10),
}


@lru_cache(maxsize=1)
def _anagram_index() -> dict[str, list[str]]:
    """Map each sorted-letter signature to the words that share it."""
    path = os.path.join(os.path.dirname(__file__), "data", "words.txt")
    index: dict[str, list[str]] = defaultdict(list)
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                word = line.strip()
                if word:
                    index["".join(sorted(word))].append(word)
    except OSError:
        return {}
    return index


def _word_score(word: str) -> int:
    return sum(_TILE_VALUES.get(c, 0) for c in word)


def parse_anagram(query: str):
    match = _ANAGRAM_RE.match(query)
    if not match:
        return None
    letters = re.sub(r"[^a-z]", "", match.group(1).lower())
    if not 2 <= len(letters) <= 12:
        return None

    index = _anagram_index()
    if not index:
        return None

    signature = "".join(sorted(letters))
    found: set[str] = set()

    # Exact anagrams (all letters used) first, then every shorter sub-word.
    exact = list(index.get(signature, []))
    from itertools import combinations
    available = sorted(letters)
    for length in range(len(available), 1, -1):
        for combo in set(combinations(available, length)):
            found.update(index.get("".join(combo), []))

    ranked = sorted(found, key=lambda w: (-len(w), -_word_score(w), w))
    if not ranked:
        return Widget(kind="anagram", title="No words found",
                      subtitle=f"from “{letters}”", tool="anagram",
                      data={"empty": True})

    top = ranked[:60]
    return Widget(
        kind="anagram",
        title=f"{len(ranked)} word{'s' if len(ranked) != 1 else ''}",
        subtitle=f"from the letters “{letters}”",
        detail=(f"{len(exact)} full anagram{'s' if len(exact) != 1 else ''}: "
                + ", ".join(exact[:6]) if exact else "No full-length anagrams"),
        items=top,
        tool="anagram", copy_value=" ".join(top[:20]),
        data={"letters": letters, "exact": exact[:12],
              "by_length": _group_by_length(top)},
    )


def _group_by_length(words: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for word in words:
        groups[str(len(word))].append(word)
    return dict(sorted(groups.items(), key=lambda kv: -int(kv[0])))


# --------------------------------------------------------------------------- #
# Morse code
# --------------------------------------------------------------------------- #

_MORSE = {
    "a": ".-", "b": "-...", "c": "-.-.", "d": "-..", "e": ".", "f": "..-.",
    "g": "--.", "h": "....", "i": "..", "j": ".---", "k": "-.-", "l": ".-..",
    "m": "--", "n": "-.", "o": "---", "p": ".--.", "q": "--.-", "r": ".-.",
    "s": "...", "t": "-", "u": "..-", "v": "...-", "w": ".--", "x": "-..-",
    "y": "-.--", "z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..",
    "'": ".----.", "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-",
    "&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-", "+": ".-.-.",
    "-": "-....-", "_": "..--.-", '"': ".-..-.", "$": "...-..-", "@": ".--.-.",
}
_MORSE_REVERSE = {v: k for k, v in _MORSE.items()}

_MORSE_RE = re.compile(
    r"^\s*morse(?:\s*code)?\s*(?:converter|translator|encode|decode|of|:)?\s*(.+)$",
    re.I | re.S,
)


def parse_morse(query: str):
    match = _MORSE_RE.match(query)
    if not match:
        return None
    payload = match.group(1).strip()
    if not payload or len(payload) > 500:
        return None

    # Decode when the payload is only dots, dashes and separators.
    if re.fullmatch(r"[.\-/ ·—_|]+", payload):
        words = re.split(r"\s*/\s*|\s{2,}|\s*\|\s*", payload.strip())
        decoded_words = []
        for word in words:
            letters = word.replace("·", ".").replace("—", "-").split()
            decoded = "".join(_MORSE_REVERSE.get(sym, "?") for sym in letters)
            if decoded:
                decoded_words.append(decoded)
        output = " ".join(decoded_words).strip()
        if not output:
            return None
        return Widget(kind="morse", title=output.upper(),
                      subtitle="Morse → text", detail=payload[:120],
                      tool="morse", copy_value=output.upper(),
                      data={"mode": "decode", "morse": payload})

    # Otherwise encode.
    encoded_words = []
    for word in payload.lower().split():
        symbols = [_MORSE[c] for c in word if c in _MORSE]
        if symbols:
            encoded_words.append(" ".join(symbols))
    output = " / ".join(encoded_words)
    if not output:
        return None
    return Widget(
        kind="morse", title=output,
        subtitle="Text → Morse", detail=f"“{payload[:80]}”",
        tool="morse", copy_value=output,
        data={"mode": "encode", "text": payload[:200], "morse": output},
    )
