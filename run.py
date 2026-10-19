"""CLI: python run.py examples/sample_input_en.json"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent import generate_listing
from agent.config_loader import get_rules
from agent.models import ListingResult
from harness.trace import TraceEvent

SEVERITY_LABELS = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def print_event(event: TraceEvent) -> None:
    if event.kind == "progress":
        print(f"  [{event.elapsed_s:5.1f}s] {event.message}", flush=True)


def _limit_text(length: int, limit: int | None, unit: str) -> str:
    return f"({length}/{limit} {unit})" if limit else f"({length} {unit})"


def print_result(result: ListingResult) -> None:
    line = "=" * 72
    print(f"\n{line}\nSTATUS: {result.status.upper()}\n{result.message}\n{line}")

    if result.clarifying_questions:
        print("\nPlease answer these questions:")
        for q in result.clarifying_questions:
            print(f"  - {q}")

    draft = result.listing
    if draft and result.platform:
        rules = get_rules(result.platform)
        print(f"\nPlatform: {rules.display_name}")
        print(f"\nTITLE {_limit_text(len(draft.title), rules.title.max_chars, 'chars')}\n  {draft.title}")
        if rules.item_highlights.enabled:
            hl = draft.item_highlights
            print(f"\nITEM HIGHLIGHTS {_limit_text(len(hl), rules.item_highlights.max_chars, 'chars')}\n  {hl or '(empty)'}")
        print("\nBULLETS")
        for i, b in enumerate(draft.bullets, 1):
            print(f"  {i}. {b}  {_limit_text(len(b), rules.bullets.max_chars, 'chars')}")
        d = rules.description
        desc_note = f"({len(draft.description)}/{d.max_chars} chars" + (f", {d.min_chars}+ recommended)" if d.min_chars else ")")
        print(f"\nDESCRIPTION {desc_note}")
        for para in draft.description.split("\n"):
            if para.strip():
                print(f"  {para.strip()}")
        st_bytes = len(draft.search_terms.encode("utf-8"))
        print(f"\nSEARCH TERMS {_limit_text(st_bytes, rules.search_terms.max_bytes, 'bytes')}\n  {draft.search_terms}")

    if result.issues:
        print("\nISSUES")
        for sev in ("high", "medium", "low"):
            group = [i for i in result.issues if i.severity == sev]
            if not group:
                continue
            print(f"  {SEVERITY_LABELS[sev]} ({len(group)})")
            for i in group:
                quote = f' "{i.quote}"' if i.quote else ""
                print(f"    - [{i.field} | {i.source}]{quote}: {i.reason}")
                if i.suggestion:
                    print(f"        Fix: {i.suggestion}")
    elif draft:
        print("\nISSUES\n  None")

    if result.unverified_claims:
        print("\nUNVERIFIED CLAIMS (not found in your facts)")
        for c in result.unverified_claims:
            print(f'  - [{c.field} | {c.kind}] "{c.quote}": {c.reason}')

    if result.note:
        print(f"\nNote: {result.note}")
    u = result.usage
    print(f"\nUsage: {u.input_tokens} input tokens, {u.output_tokens} output tokens, {u.llm_calls} model calls"
          f" | Time: {result.elapsed_s:.1f}s | Stop reason: {result.stop_reason or '-'}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python run.py <input.json>")
        return 2
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    print(f"Generating listing from {argv[1]} ...")
    result = generate_listing(data, on_event=print_event)
    print_result(result)
    return 0 if result.status in ("success", "needs_clarification") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
