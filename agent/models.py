"""Data structures for the listing agent."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["amazon_us", "tiktok_us"]
Severity = Literal["high", "medium", "low"]
IssueSource = Literal["length", "terms_list", "llm_compliance", "claim_check", "format"]
ClaimKind = Literal["number", "measurement", "capacity", "material", "certification", "safety_health", "feature", "other"]

# Claim kinds that block submission when unsupported.
SPECIFIC_CLAIM_KINDS: frozenset[str] = frozenset({"number", "measurement", "capacity", "material", "certification", "safety_health"})


class ListingInput(BaseModel):
    """What the seller types in (Chinese or English)."""

    platform: Platform
    product_name: str = Field(min_length=1)
    description: str = ""
    brand: str | None = None
    category: str | None = None
    features: list[str] = Field(default_factory=list)
    materials: str | None = None
    dimensions: str | None = None
    certifications: list[str] = Field(default_factory=list)
    target_audience: str | None = None
    keywords: list[str] = Field(default_factory=list)
    extra_notes: str | None = None

    @field_validator("product_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("product_name cannot be empty")
        return v.strip()


class Fact(BaseModel):
    statement: str = Field(description="The fact in plain English, e.g. 'Capacity: 40 oz (1.2 L)'.")
    source_quote: str = Field(description="The seller's original words this fact comes from, copied exactly.")


class ProductFacts(BaseModel):
    product_type: str = Field(description="What the product is, in English, e.g. 'insulated stainless steel tumbler'. Empty if unknown.")
    facts: list[Fact] = Field(default_factory=list, description="Only facts the seller stated explicitly.")
    is_sufficient: bool = Field(description="True if the product type is clear and there is at least one concrete selling point.")
    clarifying_questions: list[str] = Field(default_factory=list, description="Short English questions for the seller, only if is_sufficient is false.")


class ListingDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Product title.")
    item_highlights: str = Field(default="", description="Amazon US only: one short highlight line that does not repeat the title. Empty string for TikTok.")
    bullets: list[str] = Field(description="Exactly 5 bullet points.", min_length=5, max_length=5)
    description: str = Field(description="Product description.")
    search_terms: str = Field(description="Backend search terms, space separated.")

    def fields(self) -> dict[str, str]:
        out = {"title": self.title, "item_highlights": self.item_highlights, "description": self.description, "search_terms": self.search_terms}
        for i, b in enumerate(self.bullets, 1):
            out[f"bullet_{i}"] = b
        return out

    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class Issue(BaseModel):
    field: str
    severity: Severity
    category: str
    source: IssueSource
    quote: str = ""
    reason: str
    suggestion: str = ""


class UnverifiedClaim(BaseModel):
    field: str
    quote: str
    kind: ClaimKind
    reason: str
    suggestion: str = ""

    @property
    def specific(self) -> bool:
        return self.kind in SPECIFIC_CLAIM_KINDS


class FieldLength(BaseModel):
    field: str
    length: int
    unit: Literal["chars", "bytes"]
    limit: int | None
    min: int | None = None
    ok: bool


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0


class ListingResult(BaseModel):
    status: Literal["success", "needs_clarification", "incomplete", "failed"]
    message: str
    platform: Platform | None = None
    listing: ListingDraft | None = None
    issues: list[Issue] = Field(default_factory=list)
    unverified_claims: list[UnverifiedClaim] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    facts: ProductFacts | None = None
    length_report: list[FieldLength] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    elapsed_s: float = 0.0
    stop_reason: str | None = None
    note: str | None = None
