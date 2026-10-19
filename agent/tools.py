"""The listing agent's tools and the run context they share."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.checks import check_lengths, match_terms, normalize_quote, quote_in_draft
from agent.config_loader import ComplianceTerm, PlatformRules
from agent.models import FieldLength, Issue, ListingDraft, ProductFacts, UnverifiedClaim
from agent.prompts import CLAIMS_SYSTEM, COMPLIANCE_SYSTEM, build_listing_copy_block, format_fact_list
from harness.guards import wrap_untrusted
from harness.llm import LLMClient
from harness.registry import ToolOutcome, ToolRegistry
from harness.trace import Trace

registry = ToolRegistry()


@dataclass
class RunContext:
    platform: str
    rules: PlatformRules
    terms: tuple[ComplianceTerm, ...]
    facts: ProductFacts
    llm: LLMClient
    trace: Trace
    last_draft: ListingDraft | None = None
    claims_cache: dict[str, list[UnverifiedClaim]] = field(default_factory=dict)
    compliance_cache: dict[str, list[Issue]] = field(default_factory=dict)
    rejections: int = 0
    accepted: dict[str, Any] | None = None


class NoArgs(BaseModel):
    pass


# ---- LLM output schemas -------------------------------------------------------

class LLMRisk(BaseModel):
    field: str = Field(description="Field name, e.g. 'title', 'bullet_2', 'description'.")
    quote: str = Field(description="Exact text copied from the listing.")
    category: str = Field(description="e.g. implied_medical, unsupported_comparison, implied_certification, safety, eco, brand, promotion.")
    severity: Literal["high", "medium"]
    reason: str
    suggestion: str


class RiskReport(BaseModel):
    risks: list[LLMRisk] = Field(default_factory=list)


class ClaimReport(BaseModel):
    claims: list[UnverifiedClaim] = Field(default_factory=list)


# ---- helpers ------------------------------------------------------------------

def _draft_json(draft: ListingDraft) -> str:
    return json.dumps(draft.fields(), ensure_ascii=False, indent=2)


def _issue_dict(issue: Issue) -> dict[str, Any]:
    return issue.model_dump(exclude_defaults=False)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def run_llm_compliance(ctx: RunContext, draft: ListingDraft) -> list[Issue]:
    key = draft.fingerprint()
    if key in ctx.compliance_cache:
        return ctx.compliance_cache[key]
    report, results = ctx.llm.structured_call(
        system=COMPLIANCE_SYSTEM.format(platform_name=ctx.rules.display_name),
        user_content=f"Review this {ctx.rules.display_name} listing.\n\n{build_listing_copy_block(_draft_json(draft))}",
        tool_name="report_risks",
        tool_description="Report compliance risks found in the listing copy. Each risk must quote the exact text.",
        schema_model=RiskReport,
    )
    for r in results:
        ctx.trace.model_call("compliance review", r.input_tokens, r.output_tokens, r.latency_ms, r.attempts)
    assert isinstance(report, RiskReport)
    issues = [
        Issue(field=r.field, severity=r.severity, category=r.category, source="llm_compliance",
              quote=r.quote, reason=r.reason, suggestion=r.suggestion)
        for r in report.risks if quote_in_draft(r.quote, draft)  # drop findings that don't quote the copy
    ]
    ctx.compliance_cache[key] = issues
    return issues


def merge_issues(primary: list[Issue], secondary: list[Issue]) -> list[Issue]:
    """Merge two issue lists; drop duplicates (same field, overlapping quote). `primary` wins."""
    merged = list(primary)
    for issue in secondary:
        q = normalize_quote(issue.quote)
        dup = any(
            m.field == issue.field and q and (q in normalize_quote(m.quote) or normalize_quote(m.quote) in q)
            for m in merged if normalize_quote(m.quote)
        )
        if not dup:
            merged.append(issue)
    return merged


def run_verify_claims(ctx: RunContext, draft: ListingDraft) -> list[UnverifiedClaim]:
    key = draft.fingerprint()
    if key in ctx.claims_cache:
        return ctx.claims_cache[key]
    user = (
        "Seller's fact list:\n"
        f"{wrap_untrusted(format_fact_list(ctx.facts))}\n\n"
        "Listing copy to check:\n"
        f"{build_listing_copy_block(_draft_json(draft))}"
    )
    report, results = ctx.llm.structured_call(
        system=CLAIMS_SYSTEM,
        user_content=user,
        tool_name="report_claims",
        tool_description="Report specific claims in the copy that the fact list does not support.",
        schema_model=ClaimReport,
    )
    for r in results:
        ctx.trace.model_call("claim verification", r.input_tokens, r.output_tokens, r.latency_ms, r.attempts)
    assert isinstance(report, ClaimReport)
    claims = [c for c in report.claims if quote_in_draft(c.quote, draft)]
    ctx.claims_cache[key] = claims
    return claims


def claims_as_issues(claims: list[UnverifiedClaim]) -> list[Issue]:
    return [
        Issue(field=c.field, severity="high" if c.specific else "medium", category=f"unsupported_{c.kind}",
              source="claim_check", quote=c.quote, reason=c.reason, suggestion=c.suggestion)
        for c in claims
    ]


def code_checks(ctx: RunContext, draft: ListingDraft) -> tuple[list[FieldLength], list[Issue], list[Issue]]:
    report, length_issues = check_lengths(draft, ctx.rules)
    term_issues = match_terms(draft, ctx.terms)
    return report, length_issues, term_issues


# ---- tools --------------------------------------------------------------------

@registry.tool("get_platform_rules", "Get the listing rules (field limits and style rules) for the target platform.", NoArgs)
def get_platform_rules(args: NoArgs, ctx: RunContext) -> dict[str, Any]:
    ctx.trace.emit(f"Reading {ctx.rules.display_name} rules")
    return ctx.rules.model_dump()


@registry.tool("check_length", "Check the length of every field of a draft against the platform limits. Search terms are counted in UTF-8 bytes.", ListingDraft)
def check_length(draft: ListingDraft, ctx: RunContext) -> dict[str, Any]:
    ctx.last_draft = draft
    report, issues = check_lengths(draft, ctx.rules)
    over = [i for i in issues if i.severity == "high"]
    ctx.trace.emit(f"Length check: {_plural(len(over), 'problem')} found" if over else "Length check passed")
    return {"fields": [r.model_dump() for r in report], "issues": [_issue_dict(i) for i in issues]}


@registry.tool("check_compliance", "Check a draft for compliance risks: banned-terms list first, then an AI review for risks the list cannot catch.", ListingDraft)
def check_compliance(draft: ListingDraft, ctx: RunContext) -> dict[str, Any]:
    ctx.last_draft = draft
    ctx.trace.emit("Checking compliance")
    issues = merge_issues(match_terms(draft, ctx.terms), run_llm_compliance(ctx, draft))
    if issues:
        ctx.trace.emit(f"Found {_plural(len(issues), 'compliance risk')}, revising", data={"count": len(issues)})
    else:
        ctx.trace.emit("No compliance risks found")
    return {"issue_count": len(issues), "issues": [_issue_dict(i) for i in issues]}


@registry.tool("verify_claims", "Compare a draft with the seller's fact list and list specific claims that have no basis in the facts.", ListingDraft)
def verify_claims(draft: ListingDraft, ctx: RunContext) -> dict[str, Any]:
    ctx.last_draft = draft
    ctx.trace.emit("Checking claims against your facts")
    claims = run_verify_claims(ctx, draft)
    if claims:
        ctx.trace.emit(f"Found {_plural(len(claims), 'unsupported claim')}, revising", data={"count": len(claims)})
    else:
        ctx.trace.emit("Every claim matches your facts")
    return {
        "unsupported_claims": [c.model_dump() | {"blocks_submission": c.specific} for c in claims],
        "instruction": "Remove each unsupported claim, or rewrite it so it says only what the fact list says.",
    }


@registry.tool(
    "submit_listing",
    "Submit the final draft. The draft is checked again (length, banned terms, unsupported claims). "
    "If it is rejected you get the list of problems; fix them and submit again.",
    ListingDraft,
    terminal=True,
)
def submit_listing(draft: ListingDraft, ctx: RunContext) -> ToolOutcome:
    ctx.last_draft = draft
    ctx.trace.emit("Running final checks")
    report, length_issues, term_issues = code_checks(ctx, draft)
    claims = run_verify_claims(ctx, draft)  # cached if this exact draft was already verified
    specific_claims = [c for c in claims if c.specific]

    blocking = [i for i in length_issues + term_issues if i.severity == "high"]
    if blocking or specific_claims:
        ctx.rejections += 1
        parts = []
        if blocking:
            parts.append(_plural(len(blocking), "rule problem"))
        if specific_claims:
            parts.append(_plural(len(specific_claims), "unsupported claim"))
        ctx.trace.emit(f"Submission rejected: {', '.join(parts)}, revising", data={"rejections": ctx.rejections})
        return ToolOutcome(
            content={
                "accepted": False,
                "blocking_issues": [_issue_dict(i) for i in blocking],
                "unsupported_specific_claims": [c.model_dump() for c in specific_claims],
                "instruction": "Fix every item above (remove unsupported claims entirely unless the fact list supports them), then call submit_listing again.",
            },
            accepted=False,
        )

    # Accepted. Report non-blocking findings honestly.
    soft_claims = [c for c in claims if not c.specific]
    llm_issues = [i for i in ctx.compliance_cache.get(draft.fingerprint(), [])]
    remaining = merge_issues(length_issues + term_issues, llm_issues)
    remaining = merge_issues(remaining, claims_as_issues(soft_claims))
    ctx.accepted = {"draft": draft, "issues": remaining, "claims": claims, "length_report": report}
    ctx.trace.emit("All checks passed")
    return ToolOutcome(
        content={"accepted": True, "notes": [_issue_dict(i) for i in remaining]},
        accepted=True,
        data=ctx.accepted,
    )
