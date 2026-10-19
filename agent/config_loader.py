"""Loads and validates the JSON config files."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class TitleRule(BaseModel):
    max_chars: int
    min_chars: int = 0
    ideal_min_chars: int | None = None
    ideal_max_chars: int | None = None
    effective_date: str | None = None
    notes: str = ""


class HighlightsRule(BaseModel):
    enabled: bool
    max_chars: int
    notes: str = ""


class BulletsRule(BaseModel):
    count: int
    max_chars: int
    notes: str = ""


class DescriptionRule(BaseModel):
    max_chars: int
    min_chars: int = 0
    notes: str = ""


class SearchTermsRule(BaseModel):
    max_bytes: int
    notes: str = ""


class PlatformRules(BaseModel):
    display_name: str
    title: TitleRule
    item_highlights: HighlightsRule
    bullets: BulletsRule
    description: DescriptionRule
    search_terms: SearchTermsRule
    forbid_emoji_and_symbols: bool = False
    style_rules: list[str] = Field(default_factory=list)
    note: str = ""


class ComplianceTerm(BaseModel):
    term: str
    category: str
    severity: Literal["high", "medium"]
    match: Literal["word", "phrase"]
    reason: str
    suggestion: str


@lru_cache(maxsize=1)
def load_platform_rules() -> dict[str, PlatformRules]:
    raw = json.loads((CONFIG_DIR / "platform_rules.json").read_text(encoding="utf-8"))
    return {key: PlatformRules.model_validate(value) for key, value in raw.items()}


@lru_cache(maxsize=1)
def load_compliance_terms() -> tuple[ComplianceTerm, ...]:
    raw = json.loads((CONFIG_DIR / "compliance_terms.json").read_text(encoding="utf-8"))
    return tuple(ComplianceTerm.model_validate(t) for t in raw["terms"])


def get_rules(platform: str) -> PlatformRules:
    rules = load_platform_rules()
    if platform not in rules:
        raise ValueError(f"Unknown platform '{platform}'. Supported: {', '.join(rules)}")
    return rules[platform]
