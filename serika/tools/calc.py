"""Safe arithmetic evaluation for the instant calculator.

``1+1`` in the search box should answer itself. This module turns a plain-text
expression into a number without ever handing user input to :func:`eval`:
the string is parsed with :mod:`ast` and walked through a whitelist of node
types, operators, functions and constants. Anything outside the whitelist
raises :class:`CalcError` and the caller simply falls through to web results.

It also understands the informal ways people write maths in a search box —
``^`` for exponent, ``×``/``÷`` symbols, thousands separators, ``20% of 80``,
``50 + 10%``, ``12 is what percent of 60`` — by rewriting those to ordinary
expressions before parsing.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass

__all__ = ["CalcError", "CalcResult", "evaluate", "looks_like_math"]


class CalcError(Exception):
    """Raised when an expression is unsafe, malformed, or out of range."""


@dataclass
class CalcResult:
    value: float | int
    expression: str      # the normalised expression we actually evaluated
    note: str = ""       # e.g. "percentage of" — shown under the result


# --------------------------------------------------------------------------- #
# whitelist
# --------------------------------------------------------------------------- #

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Guard rails: 2**100000 would hang the request thread, so cap the exponent
# and the factorial argument rather than trusting the input.
_MAX_EXPONENT = 1024
_MAX_FACTORIAL = 500
_MAX_RESULT = 1e308


def _guarded_pow(a, b):
    if abs(b) > _MAX_EXPONENT and abs(a) > 1:
        raise CalcError("exponent too large")
    return operator.pow(a, b)


_BIN_OPS[ast.Pow] = _guarded_pow


def _factorial(n):
    if n != int(n) or n < 0:
        raise CalcError("factorial needs a non-negative whole number")
    if n > _MAX_FACTORIAL:
        raise CalcError("factorial too large")
    return math.factorial(int(n))


def _log(x, base=None):
    return math.log(x) if base is None else math.log(x, base)


_FUNCTIONS = {
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "min": min, "max": max, "sum": lambda *a: sum(a),
    "pow": _guarded_pow, "exp": math.exp,
    "log": _log, "ln": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "deg": math.degrees, "degrees": math.degrees,
    "rad": math.radians, "radians": math.radians,
    "hypot": math.hypot, "gcd": math.gcd,
    "lcm": getattr(math, "lcm", lambda a, b: abs(a * b) // math.gcd(a, b)),
    "fact": _factorial, "factorial": _factorial,
    "sign": lambda x: (x > 0) - (x < 0),
    "trunc": math.trunc,
}

_CONSTANTS = {
    "pi": math.pi, "π": math.pi, "e": math.e, "tau": math.tau,
    "phi": (1 + math.sqrt(5)) / 2, "golden": (1 + math.sqrt(5)) / 2,
    "inf": math.inf,
}


# --------------------------------------------------------------------------- #
# normalisation — the informal-input layer
# --------------------------------------------------------------------------- #

_SYMBOL_MAP = {
    "×": "*", "·": "*", "✕": "*", "x": "*",   # 'x' only between digits, see below
    "÷": "/", "−": "-", "–": "-", "—": "-",
    "√": "sqrt", "^": "**",
}

# "12 is what percent of 60"
_PCT_OF_TOTAL = re.compile(
    r"^\s*([\d.,]+)\s*(?:is\s+)?what\s*(?:%|percent(?:age)?)\s*(?:of|out\s+of)\s*([\d.,]+)\s*$",
    re.I,
)
# "20% of 80", "what is 20 percent of 80"
_PCT_OF = re.compile(
    r"([\d.,]+)\s*(?:%|percent)\s*of\s*([\d.,]+(?:[eE][+-]?\d+)?)", re.I
)
# "80 + 10%", "80 - 10%"
_PCT_ADD = re.compile(r"([\d.,]+)\s*([+\-])\s*([\d.,]+)\s*%")
# "20% off 80"
_PCT_OFF = re.compile(r"([\d.,]+)\s*(?:%|percent)\s*off\s*([\d.,]+)", re.I)
# "increase from 40 to 60" / percent change
_PCT_CHANGE = re.compile(
    r"(?:percent(?:age)?\s*(?:change|increase|difference)\s*)?from\s*([\d.,]+)\s*to\s*([\d.,]+)",
    re.I,
)

_NUM = re.compile(r"\d")
_MATHY = re.compile(r"[\d)]\s*[-+*/^%]|^\s*(?:sqrt|sin|cos|tan|log|ln|abs)\s*\(")
_WORD_PREFIX = re.compile(
    r"^\s*(?:what(?:'s| is)?|calculate|calc|compute|solve|how much is|=)\s+", re.I
)


def _strip_commas(s: str) -> str:
    """Remove thousands separators (1,234.5 → 1234.5) but keep decimals."""
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)


def _f(s: str) -> float:
    return float(_strip_commas(s))


def normalise(raw: str) -> tuple[str, str]:
    """Return ``(expression, note)`` ready for :func:`ast.parse`."""
    s = raw.strip().rstrip("=?").strip()
    s = _WORD_PREFIX.sub("", s)
    s = _strip_commas(s)
    note = ""

    m = _PCT_OF_TOTAL.search(s)
    if m:
        return f"({_f(m.group(1))} / {_f(m.group(2))}) * 100", "as a percentage"

    m = _PCT_CHANGE.search(s)
    if m and re.search(r"percent|change|increase|%", s, re.I):
        a, b = _f(m.group(1)), _f(m.group(2))
        if a:
            return f"(({b} - {a}) / {a}) * 100", "percent change"

    m = _PCT_OFF.search(s)
    if m:
        pct, total = _f(m.group(1)), _f(m.group(2))
        return f"{total} * (1 - {pct} / 100)", f"{pct:g}% off {total:g}"

    if _PCT_ADD.search(s):
        def add_repl(m: re.Match) -> str:
            base, sign, pct = _f(m.group(1)), m.group(2), _f(m.group(3))
            factor = 1 + (pct / 100 if sign == "+" else -pct / 100)
            return f"({base} * {factor})"
        s2 = _PCT_ADD.sub(add_repl, s)
        if s2 != s:
            s, note = s2, "percentage adjustment"

    if _PCT_OF.search(s):
        s = _PCT_OF.sub(lambda m: f"({_f(m.group(1))} / 100 * {_f(m.group(2))})", s)
        note = note or "percentage of"

    # Symbol folding. 'x' is only a multiplication sign between two numbers,
    # otherwise it's a variable name we don't support anyway.
    s = re.sub(r"(?<=[\d\s)])[x×](?=[\s(]*[\d.(])", "*", s)
    for sym, repl in _SYMBOL_MAP.items():
        if sym == "x":
            continue
        s = s.replace(sym, repl)

    # "5!" → fact(5)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*!", r"fact(\1)", s)
    # "50%" left over at the end of an expression → 0.50
    s = re.sub(r"(?<=[\d)])\s*%(?!\s*\d)", "/100", s)
    # implicit multiplication: 2(3+4), 3pi
    s = re.sub(r"(?<=[\d)])\s*(?=\()", "*", s)
    s = re.sub(r"(?<=\d)\s*(?=(?:pi|e|tau|phi)\b)", "*", s)
    return s.strip(), note


def looks_like_math(raw: str) -> bool:
    """Cheap pre-filter so we don't AST-parse every single search query."""
    s = raw.strip()
    if not s or len(s) > 200:
        return False
    if not _NUM.search(s) and not re.search(r"\b(pi|tau|e)\b", s):
        return False
    if re.search(r"%|percent|\bsqrt\b|\bfact\b|!", s, re.I):
        return True
    return bool(_MATHY.search(s))


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #

def _eval_node(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("only numbers are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalcError("unsupported operator")
        left, right = _eval_node(node.left), _eval_node(node.right)
        try:
            return op(left, right)
        except ZeroDivisionError:
            raise CalcError("division by zero") from None
        except (OverflowError, ValueError) as exc:
            raise CalcError(str(exc)) from None

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalcError("unsupported operator")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.Name):
        key = node.id.lower()
        if key in _CONSTANTS:
            return _CONSTANTS[key]
        raise CalcError(f"unknown name: {node.id}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalcError("unsupported call")
        fn = _FUNCTIONS.get(node.func.id.lower())
        if fn is None:
            raise CalcError(f"unknown function: {node.func.id}")
        if node.keywords:
            raise CalcError("keyword arguments are not supported")
        args = [_eval_node(a) for a in node.args]
        try:
            return fn(*args)
        except CalcError:
            raise
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            raise CalcError(str(exc)) from None

    raise CalcError("unsupported expression")


def evaluate(raw: str) -> CalcResult:
    """Evaluate a user-typed expression. Raises :class:`CalcError` on refusal."""
    expr, note = normalise(raw)
    if not expr:
        raise CalcError("empty expression")
    if len(expr) > 400:
        raise CalcError("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise CalcError("could not parse expression") from None

    value = _eval_node(tree)
    if isinstance(value, complex):
        raise CalcError("complex results are not supported")
    if isinstance(value, float):
        if math.isnan(value):
            raise CalcError("not a number")
        if math.isinf(value) or abs(value) > _MAX_RESULT:
            raise CalcError("result out of range")
    return CalcResult(value=value, expression=expr, note=note)


def format_number(value: float | int, max_decimals: int = 10) -> str:
    """Render a result the way a calculator would: no trailing noise, grouped
    thousands, scientific notation only when the number really needs it."""
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        as_int = int(value)
        if abs(as_int) < 10 ** 16:
            return f"{as_int:,}"
        return f"{float(value):.6e}"
    if value != 0 and (abs(value) >= 1e13 or abs(value) < 1e-6):
        return f"{value:.6e}".replace("e", " × 10^").replace("+", "")
    text = f"{value:.{max_decimals}f}".rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    grouped = f"{int(whole):,}" if whole.lstrip("-").isdigit() else whole
    return f"{grouped}.{frac}" if frac else grouped
