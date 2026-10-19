"""Main entry point: generate_listing(input, on_event=None) -> ListingResult."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent.config_loader import get_rules, load_compliance_terms
from agent.extraction import extract_facts
from agent.models import Issue, ListingDraft, ListingInput, ListingResult, ProductFacts, Usage
from agent.prompts import build_writer_system_prompt, build_writer_user_message
from agent.tools import RunContext, claims_as_issues, code_checks, merge_issues, registry, run_verify_claims
from harness.guards import enforce_limits
from harness.llm import LLMClient, LLMError
from harness.loop import DEFAULT_MAX_TURNS, run_agent_loop
from harness.trace import EventCallback, Trace

INPUT_LIMITS: dict[str, int] = {
    "product_name": 200,
    "description": 5000,
    "brand": 100,
    "category": 200,
    "materials": 500,
    "dimensions": 500,
    "target_audience": 500,
    "extra_notes": 2000,
    "features": 3000,
    "certifications": 1000,
    "keywords": 1000,
}

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _friendly_llm_error(err: LLMError) -> str:
    messages = {
        "config": "The AI service is not set up yet: ANTHROPIC_API_KEY is missing. Add it to your .env file and try again.",
        "auth": "The AI service rejected the API key. Check ANTHROPIC_API_KEY in your .env file.",
        "rate_limit": "The AI service is busy right now. Please wait a minute and try again.",
        "server": "The AI service is having problems right now. Please try again in a few minutes.",
        "network": "We could not reach the AI service. Check your internet connection and try again.",
        "timeout": "The AI service took too long to respond. Please try again.",
        "bad_request": "The request to the AI service was rejected. Please check your input, or contact support if this keeps happening.",
        "bad_output": "The AI returned an answer we could not read. Please try again.",
    }
    return messages.get(err.kind, "Something went wrong while talking to the AI service. Please try again.")


def _check_input(raw: ListingInput | dict[str, Any]) -> tuple[ListingInput | None, list[str]]:
    try:
        listing_input = raw if isinstance(raw, ListingInput) else ListingInput.model_validate(raw)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "input"
            problems.append(f"{loc}: {err.get('msg')}")
        return None, problems
    fields: dict[str, str | None] = {
        k: ("\n".join(v) if isinstance(v, list) else v)
        for k, v in listing_input.model_dump(exclude={"platform"}).items()
    }
    return listing_input, enforce_limits(fields, INPUT_LIMITS)


def _sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))


def _usage(trace: Trace) -> Usage:
    return Usage(**trace.usage())


def generate_listing(input: ListingInput | dict[str, Any], on_event: EventCallback | None = None,
                     max_turns: int = DEFAULT_MAX_TURNS) -> ListingResult:
    """Generate a compliant English listing. Never raises for model/API failures."""
    trace = Trace(on_event)
    facts: ProductFacts | None = None
    platform: str | None = None

    def finish(**kwargs: Any) -> ListingResult:
        return ListingResult(usage=_usage(trace), elapsed_s=trace.elapsed(), platform=platform, facts=facts, **kwargs)

    # 1. Input check
    trace.emit("Checking your input")
    listing_input, problems = _check_input(input)
    if listing_input is None or problems:
        return finish(status="failed", message="Please fix your input: " + "; ".join(problems))
    platform = listing_input.platform
    rules = get_rules(platform)
    terms = load_compliance_terms()

    try:
        llm = LLMClient()

        # 2. Fact extraction
        trace.emit("Understanding your product")
        facts = extract_facts(llm, listing_input, trace)
        if not facts.is_sufficient:
            trace.emit("Need a bit more information")
            return finish(status="needs_clarification",
                          message="We need a little more information before we can write a listing.",
                          clarifying_questions=facts.clarifying_questions)
        trace.emit(f"Found {len(facts.facts)} product fact" + ("" if len(facts.facts) == 1 else "s"))

        # 3. Agent loop
        ctx = RunContext(platform=platform, rules=rules, terms=terms, facts=facts, llm=llm, trace=trace)
        trace.emit("Writing your listing")
        loop = run_agent_loop(
            llm, registry,
            system=build_writer_system_prompt(rules),
            messages=[{"role": "user", "content": build_writer_user_message(facts, rules.display_name)}],
            ctx=ctx, trace=trace, max_turns=max_turns,
        )

        # 4. Output check
        if loop.stop_reason == "terminal_accepted" and ctx.accepted:
            acc = ctx.accepted
            draft: ListingDraft = acc["draft"]
            if not rules.item_highlights.enabled:
                draft.item_highlights = ""
            issues = _sort_issues(acc["issues"])
            msg = "Your listing is ready." if not issues else f"Your listing is ready. Please review {len(issues)} note(s) below."
            return finish(status="success", message=msg, listing=draft, issues=issues,
                          unverified_claims=acc["claims"], length_report=acc["length_report"],
                          stop_reason=loop.stop_reason, note=rules.note)

        draft = ctx.last_draft
        if draft is None:
            return finish(status="failed", stop_reason=loop.stop_reason,
                          message="The writer could not produce a draft. Please try again, or add more product details.")

        trace.emit("Could not pass every check; collecting the remaining problems")
        report, length_issues, term_issues = code_checks(ctx, draft)
        issues = merge_issues(length_issues + term_issues, ctx.compliance_cache.get(draft.fingerprint(), []))
        claims = run_verify_claims(ctx, draft)
        issues = merge_issues(issues, claims_as_issues(claims))
        return finish(
            status="incomplete", stop_reason=loop.stop_reason, listing=draft, issues=_sort_issues(issues),
            unverified_claims=claims, length_report=report, note=rules.note,
            message=("We could not fix every problem automatically. This is the last draft; "
                     "please fix the issues listed below before you publish."),
        )

    except LLMError as e:
        trace.emit("AI service error", data={"kind": e.kind})
        return finish(status="failed", message=_friendly_llm_error(e))
