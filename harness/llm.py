"""Single entry point for calling the model.

- Model name comes from AI_MODEL (default claude-haiku-5-5); the API key from .env.
- SDK retries are disabled; this module retries network errors, timeouts, 429 and 5xx
  with exponential backoff and fails fast on other 4xx.
- Every call returns token usage and latency.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_RETRIES = 3
BASE_DELAY_S = 1.0
MAX_DELAY_S = 20.0
REQUEST_TIMEOUT_S = 120.0

# Models that return 400 for a forced tool_choice ("any" / "tool").
_NO_FORCED_TOOL_PREFIXES = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1")

_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def _scrub(text: str) -> str:
    """Remove anything that looks like an API key from a message."""
    return _KEY_PATTERN.sub("sk-ant-***", text)


class LLMError(Exception):
    """Raised when a model call fails for good (non-retryable, or retries exhausted)."""

    def __init__(self, kind: str, message: str, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(_scrub(message))
        self.kind = kind  # auth | bad_request | rate_limit | server | network | timeout | config | other
        self.message = _scrub(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class LLMResult:
    content: list[Any]
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    attempts: int
    model: str
    raw: Any = field(default=None, repr=False)

    def tool_uses(self) -> list[Any]:
        return [b for b in self.content if getattr(b, "type", None) == "tool_use"]

    def text(self) -> str:
        return "".join(getattr(b, "text", "") for b in self.content if getattr(b, "type", None) == "text")


def _retry_after_seconds(err: anthropic.APIStatusError) -> float | None:
    try:
        value = err.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, ValueError, TypeError):
        return None


class LLMClient:
    """Thin wrapper around anthropic.Anthropic with our own retry policy."""

    def __init__(self, model: str | None = None, max_retries: int = MAX_RETRIES, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self.model = model or os.getenv("AI_MODEL") or DEFAULT_MODEL
        self.max_retries = max_retries
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError("config", "ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout_s)

    @property
    def supports_forced_tool(self) -> bool:
        return not self.model.startswith(_NO_FORCED_TOOL_PREFIXES)

    def call(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        force_tool: str | None = None,
        max_tokens: int = 16000,
    ) -> LLMResult:
        """Call the model once (with retries). `force_tool` asks for a specific tool call."""
        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if force_tool:
            if self.supports_forced_tool:
                kwargs["tool_choice"] = {"type": "tool", "name": force_tool}
            else:
                kwargs["tool_choice"] = {"type": "auto"}
                kwargs["system"] = f"{system}\n\nYou must respond by calling the `{force_tool}` tool exactly once."

        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                response = self._client.messages.create(**kwargs)
            except anthropic.AuthenticationError as e:
                raise LLMError("auth", f"Authentication failed: {e.message}", 401) from None
            except anthropic.BadRequestError as e:
                raise LLMError("bad_request", f"Bad request: {e.message}", 400) from None
            except anthropic.RateLimitError as e:
                self._backoff_or_raise(attempt, LLMError("rate_limit", f"Rate limited: {e.message}", 429, True), _retry_after_seconds(e))
                continue
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    self._backoff_or_raise(attempt, LLMError("server", f"Server error {e.status_code}: {e.message}", e.status_code, True), _retry_after_seconds(e))
                    continue
                raise LLMError("other", f"API error {e.status_code}: {e.message}", e.status_code) from None
            except anthropic.APITimeoutError:
                self._backoff_or_raise(attempt, LLMError("timeout", "The request timed out.", None, True), None)
                continue
            except anthropic.APIConnectionError as e:
                self._backoff_or_raise(attempt, LLMError("network", f"Network error: {e}", None, True), None)
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            usage = response.usage
            return LLMResult(
                content=list(response.content),
                stop_reason=response.stop_reason,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                latency_ms=latency_ms,
                attempts=attempt,
                model=self.model,
                raw=response,
            )

    def structured_call(
        self,
        system: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        schema_model: type[BaseModel],
        max_tokens: int = 8000,
    ) -> tuple[BaseModel, list[LLMResult]]:
        """Get structured output by forcing a single tool call whose input is `schema_model`.

        If the model returns no tool call or invalid arguments, retries once with the
        validation error. Returns the parsed model and all LLMResults (for usage tracking).
        """
        input_schema = schema_model.model_json_schema()
        input_schema.pop("title", None)
        tool = {"name": tool_name, "description": tool_description, "input_schema": input_schema}
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        results: list[LLMResult] = []
        for _ in range(2):
            result = self.call(system, messages, tools=[tool], force_tool=tool_name, max_tokens=max_tokens)
            results.append(result)
            uses = [b for b in result.tool_uses() if b.name == tool_name]
            if not uses:
                problem = f"You did not call the `{tool_name}` tool. Call it now."
                messages = messages + [{"role": "assistant", "content": result.content},
                                       {"role": "user", "content": problem}]
                continue
            try:
                return schema_model.model_validate(uses[0].input), results
            except ValidationError as e:
                messages = messages + [
                    {"role": "assistant", "content": result.content},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uses[0].id, "is_error": True,
                                                  "content": f"Invalid arguments: {e}. Call `{tool_name}` again with valid arguments."}]},
                ]
        raise LLMError("bad_output", f"The model did not return valid `{tool_name}` output.")

    def _backoff_or_raise(self, attempt: int, error: LLMError, retry_after: float | None) -> None:
        """Sleep before the next attempt, or raise if retries are used up."""
        if attempt > self.max_retries:
            error.message = f"{error.message} (gave up after {attempt} attempts)"
            raise error
        delay = min(MAX_DELAY_S, BASE_DELAY_S * (2 ** (attempt - 1)))
        delay += random.uniform(0, delay * 0.25)
        if retry_after is not None:
            delay = min(MAX_DELAY_S, max(delay, retry_after))
        time.sleep(delay)
