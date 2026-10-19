"""Generic agent loop: model -> tools -> results -> model, until a stop condition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.llm import LLMClient
from harness.registry import ToolRegistry
from harness.trace import Trace

DEFAULT_MAX_TURNS = 8


@dataclass
class LoopResult:
    stop_reason: str  # terminal_accepted | end_turn | max_turns | max_tokens | refusal
    turns: int
    final_data: Any = None
    final_text: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    trace: Trace | None = None


def run_agent_loop(
    llm: LLMClient,
    registry: ToolRegistry,
    system: str,
    messages: list[dict[str, Any]],
    ctx: Any,
    trace: Trace,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = 16000,
) -> LoopResult:
    """Run the loop. LLMError propagates to the caller; tool errors are fed back to the model."""
    messages = list(messages)
    tools = registry.schemas()

    for turn in range(1, max_turns + 1):
        result = llm.call(system=system, messages=messages, tools=tools, max_tokens=max_tokens)
        trace.model_call(f"agent turn {turn}", result.input_tokens, result.output_tokens, result.latency_ms, result.attempts)
        # Append the assistant content unchanged (keeps thinking blocks valid; history stays append-only).
        messages.append({"role": "assistant", "content": result.content})

        tool_uses = result.tool_uses()
        if not tool_uses:
            reason = result.stop_reason if result.stop_reason in ("max_tokens", "refusal") else "end_turn"
            return LoopResult(reason, turn, final_text=result.text(), messages=messages, trace=trace)

        tool_results: list[dict[str, Any]] = []
        accepted: Any = None
        accepted_hit = False
        for block in tool_uses:
            res = registry.dispatch(block.name, block.input, ctx)
            trace.tool_call(block.name, res.content, res.is_error)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": res.content, "is_error": res.is_error})
            if res.terminal and res.accepted and not res.is_error and not accepted_hit:
                accepted, accepted_hit = res.data, True
        messages.append({"role": "user", "content": tool_results})

        if accepted_hit:
            return LoopResult("terminal_accepted", turn, final_data=accepted, messages=messages, trace=trace)

    return LoopResult("max_turns", max_turns, messages=messages, trace=trace)
