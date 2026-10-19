"""Tool registration and dispatch.

Tools are registered with a decorator and a pydantic parameter model; the JSON schema
is generated from the model. Dispatch never raises: bad arguments, unknown tools and
handler exceptions become is_error results the model can read and act on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from harness.llm import LLMError


@dataclass
class ToolOutcome:
    """Optional rich return value from a handler. `accepted=False` lets a terminal tool reject."""

    content: Any
    accepted: bool = True
    data: Any = None


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    terminal: bool = False
    accepted: bool = False
    data: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[BaseModel, Any], Any]
    terminal: bool = False

    def schema(self) -> dict[str, Any]:
        input_schema = self.params.model_json_schema()
        input_schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": input_schema}


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _format_validation_error(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        lines.append(f"- {loc}: {e.get('msg')}")
    return "\n".join(lines)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def tool(self, name: str, description: str, params: type[BaseModel], terminal: bool = False) -> Callable[[Callable[[Any, Any], Any]], Callable[[Any, Any], Any]]:
        def decorator(fn: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
            if name in self._tools:
                raise ValueError(f"Tool '{name}' is already registered")
            self._tools[name] = ToolSpec(name, description, params, fn, terminal)
            return fn
        return decorator

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._tools.values()]

    def is_terminal(self, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec and spec.terminal)

    def dispatch(self, name: str, raw_input: Any, ctx: Any) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(f"Unknown tool '{name}'. Available tools: {', '.join(self._tools)}.", is_error=True)
        try:
            args = spec.params.model_validate(raw_input if raw_input is not None else {})
        except ValidationError as e:
            return ToolResult(
                f"Invalid arguments for '{name}':\n{_format_validation_error(e)}\nFix these fields and call the tool again.",
                is_error=True, terminal=spec.terminal,
            )
        try:
            out = spec.handler(args, ctx)
        except LLMError:
            raise  # a failed model call inside a tool must stop the run, not be retried by the model
        except Exception as e:
            return ToolResult(f"Tool '{name}' failed: {type(e).__name__}: {e}. Adjust your input and try again.",
                              is_error=True, terminal=spec.terminal)
        if isinstance(out, ToolOutcome):
            return ToolResult(_to_text(out.content), is_error=False, terminal=spec.terminal,
                              accepted=out.accepted, data=out.data)
        return ToolResult(_to_text(out), is_error=False, terminal=spec.terminal, accepted=True, data=out)
