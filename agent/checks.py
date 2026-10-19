"""Pure-code checks shared by the tools and the submit gate."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

from agent.config_loader import ComplianceTerm, PlatformRules
from agent.models import FieldLength, Issue, ListingDraft

# Emoji and the decorative symbols TikTok Shop rejects.
_SYMBOL_PATTERN = re.compile(
    "[©®™℠"          # © ® ™ ℠
    "☀-➿"                      # misc symbols, dingbats
    "⬀-⯿"                      # arrows, stars
    "\U0001f000-\U0001faff"              # emoji blocks
    "️‍]"                      # variation selector, zero-width joiner
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _too_similar(a: str, b: str) -> bool:
    """True if `a` mostly repeats `b` (normalized containment or >= 80% word overlap)."""
    na, nb = " ".join(_WORD_RE.findall(a.lower())), " ".join(_WORD_RE.findall(b.lower()))
    if not na:
        return False
    if na in nb or nb in na:
        return True
    wa = set(na.split())
    return len(wa & set(nb.split())) / len(wa) >= 0.8


def check_lengths(draft: ListingDraft, rules: PlatformRules) -> tuple[list[FieldLength], list[Issue]]:
    report: list[FieldLength] = []
    issues: list[Issue] = []

    def add(field: str, length: int, unit: str, limit: int | None, minimum: int | None = None) -> None:
        ok = (limit is None or length <= limit) and (minimum is None or length >= minimum)
        report.append(FieldLength(field=field, length=length, unit=unit, limit=limit, min=minimum, ok=ok))
        if limit is not None and length > limit:
            issues.append(Issue(field=field, severity="high", category="length", source="length",
                                reason=f"{field} is {length} {unit}, over the limit of {limit}.",
                                suggestion=f"Shorten it by at least {length - limit} {unit}."))

    t = rules.title
    add("title", len(draft.title), "chars", t.max_chars, t.min_chars or None)
    if t.min_chars and len(draft.title) < t.min_chars:
        issues.append(Issue(field="title", severity="high", category="length", source="length",
                            reason=f"Title is {len(draft.title)} characters; the minimum is {t.min_chars}.",
                            suggestion="Add the product type and a key feature from the facts."))
    elif t.ideal_min_chars and t.ideal_max_chars and not (t.ideal_min_chars <= len(draft.title) <= t.ideal_max_chars) and len(draft.title) <= t.max_chars:
        issues.append(Issue(field="title", severity="low", category="length", source="length",
                            reason=f"Title is {len(draft.title)} characters; {t.ideal_min_chars}-{t.ideal_max_chars} works best.",
                            suggestion="Adjust the title length if you can do it naturally."))

    h = rules.item_highlights
    if h.enabled:
        if draft.item_highlights:
            add("item_highlights", len(draft.item_highlights), "chars", h.max_chars)
            if _too_similar(draft.item_highlights, draft.title):
                issues.append(Issue(field="item_highlights", severity="high", category="duplicate", source="length",
                                    quote=draft.item_highlights,
                                    reason="Item Highlights repeats the title.",
                                    suggestion="Use a different benefit or feature from the facts that is not already in the title."))
    elif draft.item_highlights:
        issues.append(Issue(field="item_highlights", severity="high", category="format", source="format",
                            quote=draft.item_highlights,
                            reason=f"{rules.display_name} does not use Item Highlights.",
                            suggestion="Leave item_highlights as an empty string."))

    for i, bullet in enumerate(draft.bullets, 1):
        add(f"bullet_{i}", len(bullet), "chars", rules.bullets.max_chars)

    d = rules.description
    add("description", len(draft.description), "chars", d.max_chars)
    if d.min_chars and len(draft.description) < d.min_chars:
        issues.append(Issue(field="description", severity="medium", category="length", source="length",
                            reason=f"Description is {len(draft.description)} characters; {d.min_chars}+ is recommended.",
                            suggestion="Add more detail, using only facts the seller gave (use cases, care, what's in the box)."))

    add("search_terms", len(draft.search_terms.encode("utf-8")), "bytes", rules.search_terms.max_bytes)

    if rules.forbid_emoji_and_symbols:
        for field, text in draft.fields().items():
            found = sorted(set(_SYMBOL_PATTERN.findall(text)))
            if found:
                issues.append(Issue(field=field, severity="high", category="symbols", source="format",
                                    quote="".join(found),
                                    reason=f"{rules.display_name} does not allow emojis or symbols like ® and ™.",
                                    suggestion="Remove these characters: " + " ".join(found)))
    return report, issues


@lru_cache(maxsize=256)
def _term_regex(term: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", term.strip()) if p]
    body = r"[\s\-]+".join(parts)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


def match_terms(draft: ListingDraft, terms: Iterable[ComplianceTerm]) -> list[Issue]:
    """Whole-word, case-insensitive matching of the banned-terms list against every field."""
    issues: list[Issue] = []
    # Longest terms first, so "best seller" wins over "best" for the same text.
    ordered = sorted(terms, key=lambda t: len(t.term), reverse=True)
    for field, text in draft.fields().items():
        if not text:
            continue
        spans: list[tuple[int, int]] = []
        for term in ordered:
            m = next((x for x in _term_regex(term.term).finditer(text)
                      if not any(x.start() < end and start < x.end() for start, end in spans)), None)
            if m:
                spans.append((m.start(), m.end()))
                issues.append(Issue(field=field, severity=term.severity, category=term.category, source="terms_list",
                                    quote=m.group(0), reason=term.reason, suggestion=term.suggestion))
    return issues


def normalize_quote(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def quote_in_draft(quote: str, draft: ListingDraft) -> bool:
    """True if `quote` (normalized) appears somewhere in the draft."""
    q = normalize_quote(quote)
    if not q:
        return False
    return any(q in normalize_quote(text) for text in draft.fields().values())
