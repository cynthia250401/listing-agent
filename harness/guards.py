"""Input guards: length limits and wrapping untrusted content against prompt injection."""

from __future__ import annotations

import re


def enforce_limits(fields: dict[str, str | None], limits: dict[str, int]) -> list[str]:
    """Return a readable error for each field longer than its limit (in characters)."""
    errors: list[str] = []
    for name, limit in limits.items():
        value = fields.get(name)
        if value is not None and len(value) > limit:
            errors.append(f"'{name}' is too long ({len(value)} characters, max {limit}).")
    return errors


def _tag_pattern(tag: str) -> re.Pattern[str]:
    # Matches <tag>, </tag>, < / tag >, <TAG attr="x"> etc.
    return re.compile(r"<\s*/?\s*" + re.escape(tag) + r"\b[^>]*>", re.IGNORECASE)


def escape_tag(text: str, tag: str) -> str:
    """Neutralize fake opening/closing copies of `tag` inside text."""
    return _tag_pattern(tag).sub(lambda m: "&lt;" + m.group(0)[1:], text)


def wrap_untrusted(text: str, tag: str = "product_data") -> str:
    """Wrap user content in <tag>…</tag> after escaping any copies of that tag inside it."""
    return f"<{tag}>\n{escape_tag(text, tag)}\n</{tag}>"
