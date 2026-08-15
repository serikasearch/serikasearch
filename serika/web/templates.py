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


def render(rel_path: str, context: dict | None = None) -> str:
    """Render a template file with the given context dict."""
    context = context or {}
    text = _resolve_includes(rel_path)

    def repl(m: re.Match) -> str:
        key, is_safe = m.group(1), m.group(2)
        value = context.get(key, "")
        if value is None:
            value = ""
        text_value = value if isinstance(value, str) else str(value)
        return text_value if is_safe else _html.escape(text_value)

    return _VAR_RE.sub(repl, text)


def clear_cache() -> None:
    """Drop the compiled-include cache (used when editing templates live)."""
    _resolve_includes.cache_clear()
