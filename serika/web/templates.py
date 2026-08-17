"""A tiny, dependency-free template engine for SerikaSearch.

It intentionally supports only two things, because that is all the UI needs:

  * component includes:  {% include "components/searchbox.html" %}
  * variable slots:      {{ name }}        (HTML-escaped)
                         {{ name|safe }}   (inserted raw — for prebuilt HTML)

Loops and conditionals are handled in Python by rendering the relevant HTML and
passing it in through a ``|safe`` slot. That keeps the engine trivial and
robust, and keeps all markup in the ``html/`` tree where it's easy to find.

Templates live under ``<project>/html``. Missing variables render as empty
strings so a partial context never blows up a page.
"""

from __future__ import annotations

import html as _html
import os
import re
from functools import lru_cache

# <project root>/html  — two levels up from this file (serika/web/templates.py)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HTML_DIR = os.path.join(_ROOT, "html")
STATIC_DIR = os.path.join(_ROOT, "static")

_INCLUDE_RE = re.compile(r'{%\s*include\s+"([^"]+)"\s*%}')
_VAR_RE = re.compile(r"{{\s*([\w.]+)\s*(\|\s*safe\s*)?}}")
_MAX_INCLUDE_DEPTH = 12


class TemplateError(Exception):
    pass


def _read(rel_path: str) -> str:
    # Normalise and confine to HTML_DIR (defence against traversal).
    safe = os.path.normpath(rel_path).lstrip("/")
    full = os.path.join(HTML_DIR, safe)
    if not os.path.abspath(full).startswith(os.path.abspath(HTML_DIR)):
        raise TemplateError(f"template path escapes html dir: {rel_path}")
    try:
        with open(full, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise TemplateError(f"template not found: {rel_path}") from e


@lru_cache(maxsize=128)
def _resolve_includes(rel_path: str, _depth: int = 0) -> str:
    """Load a template and inline all its {% include %} directives (cached)."""
    if _depth > _MAX_INCLUDE_DEPTH:
        raise TemplateError(f"include recursion too deep at {rel_path}")
    text = _read(rel_path)

    def repl(m: re.Match) -> str:
        return _resolve_includes(m.group(1), _depth + 1)

    return _INCLUDE_RE.sub(repl, text)


@lru_cache(maxsize=128)
def _compile_template(rel_path: str) -> tuple:
    """Pre-split a template into literal segments and variable references.

    Returns a list of ``("lit", str)`` and ``("var", key, is_safe)`` tuples.
    Rendering then becomes a simple iteration + join instead of regex
    substitution, which is ~3-5x faster for the base template that wraps
    every page.
    """
    text = _resolve_includes(rel_path)
    parts = []
    last = 0
    for m in _VAR_RE.finditer(text):
        if m.start() > last:
            parts.append(("lit", text[last:m.start()]))
        key = m.group(1)
        is_safe = bool(m.group(2))
        parts.append(("var", key, is_safe))
        last = m.end()
    if last < len(text):
        parts.append(("lit", text[last:]))
    return tuple(parts)


def render(rel_path: str, context: dict | None = None) -> str:
    """Render a template file with the given context dict."""
    context = context or {}
    parts = _compile_template(rel_path)
    out = []
    for part in parts:
        if part[0] == "lit":
            out.append(part[1])
        else:
            _, key, is_safe = part
            value = context.get(key, "")
            if value is None:
                value = ""
            text_value = value if isinstance(value, str) else str(value)
            out.append(text_value if is_safe else _html.escape(text_value))
    return "".join(out)


def clear_cache() -> None:
    """Drop the compiled-include cache (used when editing templates live)."""
    _resolve_includes.cache_clear()
