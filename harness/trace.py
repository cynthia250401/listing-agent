"""Run trace: records model calls and tool calls, and forwards events to an optional callback."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class TraceEvent:
    kind: str  # model_call | tool_call | progress
    message: str
    elapsed_s: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EventCallback = Callable[[TraceEvent], None]


class Trace:
    def __init__(self, on_event: EventCallback | None = None) -> None:
        self.on_event = on_event
        self.events: list[TraceEvent] = []
        self.started = time.monotonic()
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 2)

    def _record(self, event: TraceEvent) -> None:
        self.events.append(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # a broken UI callback must never break a run
                pass

    def model_call(self, label: str, input_tokens: int, output_tokens: int, latency_ms: int, attempts: int = 1) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self._record(TraceEvent(
            kind="model_call",
            message=f"Model call: {label}",
            elapsed_s=self.elapsed(),
            data={"label": label, "input_tokens": input_tokens, "output_tokens": output_tokens,
                  "latency_ms": latency_ms, "attempts": attempts},
        ))

    def tool_call(self, name: str, result: str, is_error: bool) -> None:
        self._record(TraceEvent(
            kind="tool_call",
            message=f"Tool call: {name}" + (" (error)" if is_error else ""),
            elapsed_s=self.elapsed(),
            data={"name": name, "result": result[:500], "is_error": is_error},
        ))

    def emit(self, message: str, kind: str = "progress", data: dict[str, Any] | None = None) -> None:
        self._record(TraceEvent(kind=kind, message=message, elapsed_s=self.elapsed(), data=data or {}))

    def usage(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "llm_calls": self.llm_calls}
