"""Google-style search query parsing for SerikaSearch.

Turns a user query into a safe websearch_to_tsquery expression plus a set of
structured filters that the index layer applies as SQL. Supported operators:

  * ``"exact phrase"``     — passed through to tsquery as a phrase
  * ``site:example.com``   — restrict to a host (substring match on host)
  * ``-term``              — exclude pages containing the term
  * ``intitle:term``       — term must appear in the page title (SQL ILIKE)
  * ``inurl:term``         — term must appear in the URL (SQL ILIKE)

The ``fts`` field is a plain-text string suitable for PostgreSQL's
``websearch_to_tsquery('english', ...)``.  The structured fields (``sites``,
``intitle``, ``inurl``) are applied as SQL WHERE clauses by the index layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r'"[^"]+"|\S+')
_WORD = re.compile(r"[^\w]+")

# Operators we recognise as prefixes.
_SITE_RE = re.compile(r"^site:(.+)$", re.I)
_INTITLE_RE = re.compile(r"^intitle:(.+)$", re.I)
_INURL_RE = re.compile(r"^inurl:(.+)$", re.I)


@dataclass
class ParsedQuery:
    """The result of parsing a user query.

    ``fts`` is a plain-text string for ``websearch_to_tsquery`` (empty if
    there's nothing to match).  ``display`` is the cleaned query for showing
    back to the user. The remaining lists are structured filters the index
    layer applies in SQL.
    """
    fts: str = ""
    display: str = ""
    sites: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    intitle: list[str] = field(default_factory=list)
    inurl: list[str] = field(default_factory=list)

    @property
    def has_operators(self) -> bool:
        return bool(self.sites or self.excludes or self.intitle or self.inurl)

    @property
    def is_empty(self) -> bool:
        return not self.fts and not self.sites


def _clean_word(tok: str) -> str:
    return _WORD.sub(" ", tok.replace('"', "").replace("'", "")).strip()


def parse(query: str) -> ParsedQuery:
    """Parse a user query into a websearch_to_tsquery string + structured filters."""
    query = (query or "").strip()
    if not query or query == "*":
        return ParsedQuery()

    tokens = _TOKEN_RE.findall(query)
    fts_parts: list[str] = []
    display_parts: list[str] = []
    sites: list[str] = []
    excludes: list[str] = []
    intitle: list[str] = []
    inurl: list[str] = []

    for tok in tokens:
        m = _SITE_RE.match(tok)
        if m:
            site = m.group(1).strip().strip('"').lower()
            if site:
                sites.append(site)
            continue

        if tok.startswith("-") and len(tok) > 1 and not tok.startswith("--"):
            word = _clean_word(tok[1:])
            if word:
                # websearch_to_tsquery supports -word for exclusion.
                fts_parts.append(f"-{word}")
                excludes.append(word)
            continue

        m = _INTITLE_RE.match(tok)
        if m:
            word = _clean_word(m.group(1))
            if word:
                # intitle: is handled as a SQL ILIKE clause, not in tsquery.
                intitle.append(word)
                # Also add to the text search so it matches the title weight.
                fts_parts.append(word)
            continue

        m = _INURL_RE.match(tok)
        if m:
            word = _clean_word(m.group(1))
            if word:
                inurl.append(word)
                fts_parts.append(word)
            continue

        # Quoted phrase — keep as-is for websearch_to_tsquery.
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            fts_parts.append(tok)
            display_parts.append(tok)
            continue

        # Plain bare word(s).
        clean = _clean_word(tok)
        if not clean:
            continue
        for w in clean.split():
            fts_parts.append(w)
        display_parts.append(tok)

    return ParsedQuery(
        fts=" ".join(fts_parts),
        display=" ".join(display_parts),
        sites=sites,
        excludes=excludes,
        intitle=intitle,
        inurl=inurl,
    )
