"""Fact extraction: one structured LLM call."""

from __future__ import annotations

import json

from agent.models import ListingInput, ProductFacts
from agent.prompts import EXTRACTION_SYSTEM
from harness.guards import wrap_untrusted
from harness.llm import LLMClient
from harness.trace import Trace


def _input_to_text(listing_input: ListingInput) -> str:
    data = listing_input.model_dump(exclude={"platform"}, exclude_none=True)
    data = {k: v for k, v in data.items() if v not in ("", [], None)}
    return json.dumps(data, ensure_ascii=False, indent=2)


def extract_facts(llm: LLMClient, listing_input: ListingInput, trace: Trace) -> ProductFacts:
    user = (
        "Extract the product facts from the seller's input below.\n\n"
        f"{wrap_untrusted(_input_to_text(listing_input))}"
    )
    facts, results = llm.structured_call(
        system=EXTRACTION_SYSTEM,
        user_content=user,
        tool_name="record_facts",
        tool_description="Record the product type, the explicit facts, and whether there is enough to write a listing.",
        schema_model=ProductFacts,
    )
    for r in results:
        trace.model_call("fact extraction", r.input_tokens, r.output_tokens, r.latency_ms, r.attempts)
    assert isinstance(facts, ProductFacts)
    if not facts.product_type.strip() or not facts.facts:
        facts.is_sufficient = False
        if not facts.clarifying_questions:
            facts.clarifying_questions = [
                "What exactly is the product, and what is it used for?",
                "What is it made of, and what size or capacity is it?",
                "What are 2-3 features that make it useful or different?",
            ]
    return facts
