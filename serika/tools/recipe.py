"""Recipe scaling and ingredient substitutions — pure computation, no network.

Paste an ingredient list, choose a factor (or a new yield), and every quantity
is rescaled: whole numbers, decimals, and cooking fractions like ``1 1/2 cups``
all parse and come back in a tidy fraction. A dietary toggle swaps common
animal products for plant-based stand-ins, with the swap ratios bakers actually
use.

All of it runs in the browser once the widget loads; this module provides the
data the widget is built from and a server-side parser so ``recipe converter``
opens it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

__all__ = ["scale_line", "parse_recipe", "SUBSTITUTIONS"]

# Common vulgar fractions people paste from recipe sites.
_UNICODE_FRACTIONS = {
    "½": ".5", "⅓": " 1/3", "⅔": " 2/3", "¼": ".25", "¾": ".75",
    "⅕": " 1/5", "⅖": " 2/5", "⅗": " 3/5", "⅘": " 4/5",
    "⅙": " 1/6", "⅚": " 5/6", "⅛": ".125", "⅜": ".375", "⅝": ".625", "⅞": ".875",
}

_QTY_RE = re.compile(
    r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d*\.\d+|\d+(?:\.\d+)?)\s*(.*)$"
)


def _parse_quantity(text: str) -> Fraction | None:
    text = text.strip()
    if not text:
        return None
    try:
        if " " in text:                      # "1 1/2"
            whole, frac = text.split(None, 1)
            return Fraction(whole) + Fraction(frac)
        if "." in text:                      # "0.5" or ".5"
            return Fraction(text).limit_denominator(1000)
        return Fraction(text)                # "1/2" or "2"
    except (ValueError, ZeroDivisionError):
        try:
            return Fraction(str(float(text))).limit_denominator(1000)
        except ValueError:
            return None


def _format_quantity(value: Fraction) -> str:
    """Render a quantity the way a recipe would: mixed fractions, tidy eighths."""
    if value == 0:
        return "0"
    # Snap to a sensible cooking resolution so 0.333… → 1/3.
    snapped = value.limit_denominator(16)
    whole = snapped.numerator // snapped.denominator
    remainder = snapped - whole
    if remainder == 0:
        return str(whole)
    frac = f"{remainder.numerator}/{remainder.denominator}"
    return f"{whole} {frac}" if whole else frac


def scale_line(line: str, factor: Fraction) -> str:
    """Rescale the leading quantity in one ingredient line."""
    # Normalise unicode fractions to ASCII first.
    for symbol, ascii_form in _UNICODE_FRACTIONS.items():
        line = line.replace(symbol, ascii_form)
    line = re.sub(r"(\d)\s+\.", r"\1.", line)   # "1 .5" glued back to "1.5"

    match = _QTY_RE.match(line)
    if not match:
        return line.strip()
    quantity = _parse_quantity(match.group(1))
    if quantity is None:
        return line.strip()
    scaled = quantity * factor
    return f"{_format_quantity(scaled)} {match.group(2).strip()}".strip()


# Plant-based substitutions: (matcher, replacement, note). Ratios are the
# common baking equivalents.
SUBSTITUTIONS = {
    "vegan": [
        (r"\bbutter\b", "vegan butter or coconut oil", "1:1"),
        (r"\bmilk\b", "oat or soy milk", "1:1"),
        (r"\bheavy cream\b", "coconut cream", "1:1"),
        (r"\beggs?\b", "flax egg (1 tbsp ground flax + 3 tbsp water each)",
         "per egg"),
        (r"\bhoney\b", "maple syrup or agave", "1:1"),
        (r"\bcheese\b", "nutritional yeast or vegan cheese", "to taste"),
        (r"\byogh?urt\b", "coconut or soy yoghurt", "1:1"),
        (r"\bgelatin\b", "agar-agar", "half the amount"),
    ],
    "gluten-free": [
        (r"\b(all[- ]purpose |plain )?flour\b", "gluten-free 1:1 baking flour",
         "1:1"),
        (r"\bbreadcrumbs\b", "gluten-free breadcrumbs or ground oats", "1:1"),
        (r"\bsoy sauce\b", "tamari", "1:1"),
        (r"\bpasta\b", "gluten-free pasta", "1:1"),
    ],
    "dairy-free": [
        (r"\bbutter\b", "dairy-free butter or oil", "1:1"),
        (r"\bmilk\b", "almond, oat or soy milk", "1:1"),
        (r"\bcream\b", "coconut cream", "1:1"),
        (r"\bcheese\b", "dairy-free cheese", "1:1"),
    ],
}


@dataclass
class RecipeTrigger:
    prefilled: str = ""


_RECIPE_RE = re.compile(
    r"^\s*(?:recipe\s+(?:converter|scaler|calculator|scaling)|"
    r"scale\s+(?:a\s+)?recipe|ingredient\s+(?:scaler|converter)|"
    r"halve\s+(?:a\s+)?recipe|double\s+(?:a\s+)?recipe)\s*$",
    re.I,
)


def parse_recipe(query: str) -> RecipeTrigger | None:
    if _RECIPE_RE.match(query.strip()):
        return RecipeTrigger()
    return None
