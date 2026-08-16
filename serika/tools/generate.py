"""Generators and text utilities: passwords, dice, UUIDs, lorem, counters.

Everything random here comes from :mod:`secrets` rather than :mod:`random`, so
a password generated in the results page is actually safe to use. Nothing is
logged or stored — generated values exist only in the response that renders
them.
"""

from __future__ import annotations

import re
import secrets
import string
import unicodedata
import uuid
from dataclasses import dataclass, field

__all__ = ["parse_random", "parse_password", "parse_uuid", "parse_lorem",
           "parse_word_count", "generate_password", "password_entropy",
           "lorem_ipsum", "text_statistics", "slugify"]


@dataclass
class Generated:
    kind: str
    title: str
    subtitle: str = ""
    items: list[str] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# dice, coins, random numbers
# --------------------------------------------------------------------------- #

_COIN_RE = re.compile(r"^\s*(?:flip\s+a\s+coin|coin\s*flip|heads\s+or\s+tails)\s*\??$", re.I)
_DICE_RE = re.compile(r"^\s*(?:roll\s+)?(\d{0,2})?\s*d\s*(\d{1,3})\s*(?:dice|die)?\s*\??$", re.I)
_ROLL_RE = re.compile(r"^\s*roll\s+(?:a\s+)?(?:die|dice)\s*\??$", re.I)
_RANDOM_RE = re.compile(
    r"^\s*(?:random\s+number|rand(?:om)?|pick\s+a\s+number)"
    r"(?:\s+between\s+(-?\d+)\s+(?:and|to)\s+(-?\d+)"
    r"|\s+from\s+(-?\d+)\s+to\s+(-?\d+)"
    r"|\s+(\d+)\s*-\s*(\d+))?\s*\??$",
    re.I,
)


def parse_random(query: str) -> Generated | None:
    """Coin flips, dice rolls, and random numbers in a range."""
    if _COIN_RE.match(query):
        result = secrets.choice(("Heads", "Tails"))
        return Generated(kind="coin", title=result, subtitle="Coin flip")

    if _ROLL_RE.match(query):
        return Generated(kind="dice", title=str(secrets.randbelow(6) + 1),
                         subtitle="Rolled one six-sided die")

    m = _DICE_RE.match(query)
    if m and m.group(2):
        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        if not (1 <= count <= 20) or not (2 <= sides <= 1000):
            return None
        rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
        total = sum(rolls)
        return Generated(
            kind="dice",
            title=str(total),
            subtitle=f"{count}d{sides}",
            items=[str(r) for r in rolls],
            rows=[("Rolls", " + ".join(str(r) for r in rolls)),
                  ("Total", str(total)),
                  ("Range", f"{count}–{count * sides}")],
        )

    m = _RANDOM_RE.match(query)
    if m:
        pairs = [(m.group(1), m.group(2)), (m.group(3), m.group(4)),
                 (m.group(5), m.group(6))]
        low, high = 1, 100
        for a, b in pairs:
            if a is not None and b is not None:
                low, high = int(a), int(b)
                break
        if low > high:
            low, high = high, low
        span = high - low
        if span > 10 ** 12:
            return None
        value = low + secrets.randbelow(span + 1)
        return Generated(kind="random", title=f"{value:,}",
                         subtitle=f"Random number between {low:,} and {high:,}")
    return None


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #

_AMBIGUOUS = "Il1O0"
# Accept the many ways people ask: "password", "generate a password",
# "give me a secure password", "i need a strong 24 character password", …
_PASSWORD_RE = re.compile(
    r"^\s*(?:i\s+)?(?:(?:please\s+)?(?:can\s+you\s+)?"
    r"(?:generate|create|make|give|need|want|get)\s+(?:me\s+)?)?"
    r"(?:(?:a|an|some|my|new)\s+)*"
    # adjectives and an optional length, in either order
    r"(?:(?:strong|secure|random|good|new)\s+|"
    r"(\d{1,3})[\s-]*(?:character|char|digit|letter)s?\s+)*"
    r"pass(?:word|phrase)\s*"
    r"(?:generator|please|for\s+me)?\s*"
    r"(?:(\d{1,3})\s*(?:characters?|chars?)?)?\s*\??$",
    re.I,
)
_PASSPHRASE_RE = re.compile(
    r"^\s*(?:generate\s+|create\s+)?(?:random\s+)?pass\s?phrase\s*"
    r"(?:(\d{1,2})\s*words?)?\s*\??$", re.I,
)

# A compact, memorable word list for passphrases — short, unambiguous words.
_WORDS = (
    "amber anchor apple arrow autumn basil beacon birch bishop bloom bramble "
    "breeze bronze canyon cedar cinder cobalt comet copper coral cove crimson "
    "cypress dahlia delta ember fable falcon fern flint forest garnet glacier "
    "granite harbor harvest hazel heron indigo island ivory jasper juniper "
    "kelp lagoon lantern larch laurel linen lotus lumen maple marble meadow "
    "mesa mint moss nectar nimbus north oak ochre onyx opal orbit orchid otter "
    "pearl pebble pine pollen prairie quartz quill raven reef ridge river "
    "rowan saffron sage sandstone sapphire shale silver slate solstice sparrow "
    "spruce summit sunset tamarind teal thistle thunder tidal timber topaz "
    "tundra umber valley velvet vertex violet walnut willow winter zenith"
).split()


def generate_password(length: int = 20, symbols: bool = True,
                      avoid_ambiguous: bool = True) -> str:
    """A cryptographically random password guaranteed to hit every class."""
    length = max(6, min(128, length))
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    marks = "!@#$%^&*-_=+?" if symbols else ""
    if avoid_ambiguous:
        lower = "".join(c for c in lower if c not in _AMBIGUOUS)
        upper = "".join(c for c in upper if c not in _AMBIGUOUS)
        digits = "".join(c for c in digits if c not in _AMBIGUOUS)

    pools = [lower, upper, digits] + ([marks] if marks else [])
    alphabet = "".join(pools)
    while True:
        chars = [secrets.choice(pool) for pool in pools]
        chars += [secrets.choice(alphabet) for _ in range(length - len(pools))]
        # Shuffle without bias: Fisher–Yates driven by secrets.
        for i in range(len(chars) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            chars[i], chars[j] = chars[j], chars[i]
        candidate = "".join(chars)
        if len(candidate) == length:
            return candidate


def password_entropy(password: str) -> float:
    """Bits of entropy, assuming the attacker knows which classes were used."""
    pool = 0
    if any(c.islower() for c in password):
        pool += 26
    if any(c.isupper() for c in password):
        pool += 26
    if any(c.isdigit() for c in password):
        pool += 10
    if any(not c.isalnum() for c in password):
        pool += 20
    if pool <= 1:
        return 0.0
    import math
    return round(len(password) * math.log2(pool), 1)


def strength_label(bits: float) -> str:
    if bits >= 110:
        return "Excellent"
    if bits >= 80:
        return "Strong"
    if bits >= 60:
        return "Reasonable"
    if bits >= 40:
        return "Weak"
    return "Very weak"


def parse_password(query: str) -> Generated | None:
    m = _PASSPHRASE_RE.match(query)
    if m:
        count = int(m.group(1) or 4)
        count = max(3, min(12, count))
        words = [secrets.choice(_WORDS) for _ in range(count)]
        phrase = "-".join(words)
        bits = round(count * 6.7, 1)  # ~110 word list ⇒ log2(110) ≈ 6.78 bits
        return Generated(
            kind="password", title=phrase,
            subtitle=f"{count}-word passphrase · ~{bits} bits of entropy",
            items=[
                "-".join(secrets.choice(_WORDS) for _ in range(count))
                for _ in range(4)
            ],
        )

    m = _PASSWORD_RE.match(query)
    if not m:
        return None
    length = int(m.group(1) or m.group(2) or 20)
    length = max(8, min(64, length))
    primary = generate_password(length)
    bits = password_entropy(primary)
    return Generated(
        kind="password",
        title=primary,
        subtitle=(f"{length} characters · {bits} bits of entropy · "
                  f"{strength_label(bits)}"),
        items=[generate_password(length) for _ in range(4)],
    )


# --------------------------------------------------------------------------- #
# UUIDs
# --------------------------------------------------------------------------- #

_UUID_RE = re.compile(
    r"^\s*(?:generate\s+|random\s+|new\s+)?(?:uuid|guid)\s*(?:v?4)?\s*"
    r"(?:generator)?\s*\??$", re.I,
)


def parse_uuid(query: str) -> Generated | None:
    if not _UUID_RE.match(query):
        return None
    primary = str(uuid.uuid4())
    return Generated(
        kind="uuid", title=primary, subtitle="Random UUID (version 4)",
        items=[str(uuid.uuid4()) for _ in range(4)],
        rows=[("Uppercase", primary.upper()),
              ("No hyphens", primary.replace("-", "")),
              ("URN", f"urn:uuid:{primary}")],
    )


# --------------------------------------------------------------------------- #
# lorem ipsum
# --------------------------------------------------------------------------- #

_LOREM_RE = re.compile(
    r"^\s*(?:(\d{1,2})\s+)?(?:paragraphs?\s+of\s+)?lorem(?:\s+ipsum)?"
    r"(?:\s+(?:generator|text|(\d{1,2})\s+paragraphs?))?\s*\??$", re.I,
)

_LOREM_SOURCE = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea "
    "commodo consequat duis aute irure in reprehenderit voluptate velit esse "
    "cillum eu fugiat nulla pariatur excepteur sint occaecat cupidatat non "
    "proident sunt culpa qui officia deserunt mollit anim id est laborum "
    "curabitur pretium tincidunt lacus nulla gravida orci a odio nullam varius "
    "turpis et commodo pharetra est eros suscipit magna in ornare sapien"
).split()


def lorem_ipsum(paragraphs: int = 3, seeded: bool = True) -> list[str]:
    """Classic filler text — the first paragraph opens the traditional way."""
    paragraphs = max(1, min(10, paragraphs))
    out = []
    for index in range(paragraphs):
        sentences = []
        for _ in range(secrets.randbelow(3) + 3):
            length = secrets.randbelow(9) + 7
            words = [secrets.choice(_LOREM_SOURCE) for _ in range(length)]
            sentences.append(" ".join(words).capitalize() + ".")
        text = " ".join(sentences)
        if index == 0 and seeded:
            text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
                    "sed do eiusmod tempor incididunt ut labore et dolore "
                    "magna aliqua. ") + text
        out.append(text)
    return out


def parse_lorem(query: str) -> Generated | None:
    m = _LOREM_RE.match(query)
    if not m:
        return None
    count = int(m.group(1) or m.group(2) or 3)
    return Generated(kind="lorem", title="Lorem ipsum",
                     subtitle=f"{count} paragraph{'s' if count != 1 else ''} "
                              f"of placeholder text",
                     items=lorem_ipsum(count))


# --------------------------------------------------------------------------- #
# text statistics
# --------------------------------------------------------------------------- #

_COUNT_RE = re.compile(
    r"^\s*(?:word\s+count|character\s+count|count\s+(?:words|characters))"
    r"\s*:?\s+(.{3,4000})$", re.I | re.S,
)
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s|$)")


def text_statistics(text: str) -> dict:
    """Words, characters, sentences, and an estimated reading time."""
    stripped = text.strip()
    words = [w for w in re.split(r"\s+", stripped) if w]
    sentences = [s for s in _SENTENCE_SPLIT.split(stripped) if s.strip()]
    paragraphs = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    letters = sum(1 for c in stripped if c.isalpha())
    reading_seconds = round(len(words) / 238 * 60)
    return {
        "words": len(words),
        "characters": len(stripped),
        "characters_no_spaces": len(re.sub(r"\s", "", stripped)),
        "letters": letters,
        "sentences": max(1, len(sentences)) if stripped else 0,
        "paragraphs": max(1, len(paragraphs)) if stripped else 0,
        "unique_words": len({w.lower().strip(string.punctuation) for w in words}),
        "longest_word": max(words, key=len) if words else "",
        "average_word_length": round(letters / len(words), 1) if words else 0,
        "reading_time": (f"{reading_seconds}s" if reading_seconds < 60
                         else f"{reading_seconds // 60}m {reading_seconds % 60}s"),
    }


def parse_word_count(query: str) -> Generated | None:
    m = _COUNT_RE.match(query.strip())
    if not m:
        return None
    stats = text_statistics(m.group(1))
    return Generated(
        kind="wordcount",
        title=f"{stats['words']:,} words",
        subtitle=f"{stats['characters']:,} characters · "
                 f"{stats['reading_time']} to read",
        rows=[
            ("Characters (no spaces)", f"{stats['characters_no_spaces']:,}"),
            ("Sentences", f"{stats['sentences']:,}"),
            ("Paragraphs", f"{stats['paragraphs']:,}"),
            ("Unique words", f"{stats['unique_words']:,}"),
            ("Average word length", f"{stats['average_word_length']} letters"),
        ],
    )


# --------------------------------------------------------------------------- #
# slugs — used by the reference lookups as well as the text tool
# --------------------------------------------------------------------------- #

def slugify(text: str, separator: str = "-") -> str:
    """ASCII, lower-case, punctuation-free — safe for URLs and file names."""
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^\w\s-]", "", ascii_text).strip().lower()
    return re.sub(r"[\s_-]+", separator, cleaned).strip(separator)
