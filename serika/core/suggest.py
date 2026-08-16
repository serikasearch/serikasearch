"""Autocomplete, spelling correction, and related searches.

All three are derived from the *corpus*, never from a query log — SerikaSearch
does not keep one. Completions come from indexed page titles, "did you mean"
compares a misspelling against the vocabulary those titles contain, and related
searches are mined from the phrases that recur across a result set.

That has a nice side effect beyond privacy: suggestions can only ever point at
things the index can actually answer.
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = ["did_you_mean", "related_searches", "extract_phrases"]

_WORD_RE = re.compile(r"[a-z][a-z'-]{1,24}")

# Words that carry no topical weight — never suggested as a related search and
# never offered as a spelling correction.
_STOPWORDS = frozenset("""
a an the and or but if then else of in on at to for from by with without
about into over under again further once here there all any both each few
more most other some such no nor not only own same so than too very can will
just should now is are was were be been being have has had do does did doing
this that these those i you he she it we they them his her its our your their
what which who whom when where why how new best top free online home page site
official www com net org html index review reviews guide guides vs versus
""".split())

# A correction must be a real improvement, not a coin flip: the candidate has
# to appear this many times more often than the word actually typed.
_CORRECTION_RATIO = 12


def _edits_within(word: str, other: str, max_distance: int = 2) -> int | None:
    """Levenshtein distance, abandoned early once it exceeds the budget."""
    if abs(len(word) - len(other)) > max_distance:
        return None
    previous = list(range(len(other) + 1))
    for i, a in enumerate(word, 1):
        current = [i]
        best = i
        for j, b in enumerate(other, 1):
            cost = 0 if a == b else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            best = min(best, value)
        if best > max_distance:
            return None
        previous = current
    distance = previous[-1]
    return distance if distance <= max_distance else None


def did_you_mean(query: str, vocabulary: dict[str, int]) -> str:
    """Suggest a corrected spelling for ``query``, or an empty string.

    Only words the corpus has never seen are candidates for correction, and a
    replacement must be dramatically more common than what was typed — that
    keeps rare-but-real terms (product names, handles) from being "corrected"
    into something the user didn't ask for.
    """
    if not vocabulary or not query or len(query) > 120:
        return ""
    words = query.split()
    if not words or len(words) > 8:
        return ""

    corrected: list[str] = []
    changed = False
    for raw in words:
        token = raw.lower()
        # Only words the corpus has never seen at all are corrected. A word
        # that appears even once is a word someone really wrote, and "fixing"
        # it would be worse than showing no results.
        if (len(token) < 4 or not token.isalpha() or token in _STOPWORDS
                or vocabulary.get(token, 0) > 0):
            corrected.append(raw)
            continue

        best_word, best_score = "", 0
        for candidate, count in vocabulary.items():
            if count < _CORRECTION_RATIO:
                continue
            if abs(len(candidate) - len(token)) > 2:
                continue
            if candidate[0] != token[0]:
                continue          # first-letter typos are rare; this cuts work
            distance = _edits_within(token, candidate)
            if distance is None or distance == 0:
                continue
            score = count // (distance * distance)
            if score > best_score:
                best_word, best_score = candidate, score

        if best_word:
            corrected.append(best_word)
            changed = True
        else:
            corrected.append(raw)

    return " ".join(corrected) if changed else ""


def extract_phrases(titles: list[str], limit: int = 40) -> Counter:
    """Count the meaningful one- and two-word phrases across some titles."""
    counts: Counter = Counter()
    for title in titles[:limit]:
        words = [w for w in _WORD_RE.findall((title or "").lower())]
        meaningful = [w for w in words if w not in _STOPWORDS and len(w) > 2]
        counts.update(meaningful)
        for a, b in zip(words, words[1:]):
            if a in _STOPWORDS or b in _STOPWORDS:
                continue
            if len(a) > 2 and len(b) > 2:
                counts[f"{a} {b}"] += 2      # phrases are worth more than words
    return counts


def related_searches(query: str, titles: list[str], limit: int = 8) -> list[str]:
    """Mine follow-up searches from the titles of the current result set."""
    if not titles:
        return []
    query_words = {w for w in _WORD_RE.findall(query.lower())}
    counts = extract_phrases(titles)

    candidates: list[tuple[str, int]] = []
    for phrase, count in counts.most_common(80):
        if count < 2:
            continue
        phrase_words = set(phrase.split())
        if phrase_words <= query_words:
            continue                      # nothing new to offer
        if len(phrase) < 4 or len(phrase) > 40:
            continue
        candidates.append((phrase, count))

    out: list[str] = []
    seen: set[str] = set()
    for phrase, _ in candidates:
        phrase_words = set(phrase.split())
        # A phrase that already carries one of the query's words stands on its
        # own; an unrelated one is paired with the query so the suggestion
        # reads as a refinement rather than a jump to another subject.
        if phrase_words & query_words:
            suggestion = phrase
        else:
            suggestion = f"{query} {phrase}".strip()
        key = suggestion.lower()
        if key in seen or key == query.lower():
            continue
        # Skip near-duplicates that only add a word already suggested.
        if any(key.startswith(existing) or existing.startswith(key)
               for existing in seen):
            continue
        seen.add(key)
        out.append(suggestion)
        if len(out) >= limit:
            break
    return out
