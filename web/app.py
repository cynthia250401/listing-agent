"""Web server for the listing agent.

Run:  .venv/bin/uvicorn web.app:app --reload
Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import generate_listing
from agent.config_loader import load_platform_rules
from harness.trace import TraceEvent

STATIC_DIR = Path(__file__).resolve().parent / "static"
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "4"))
MAX_BODY_BYTES = 64 * 1024

app = FastAPI(title="Listing Agent", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_requests_by_ip: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _requests_by_ip[ip]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(429, "You have reached the hourly limit. Please try again later.")
    window.append(now)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/platforms")
def platforms() -> dict[str, Any]:
    """Platform limits for the form's live character counters."""
    out: dict[str, Any] = {}
    for key, rules in load_platform_rules().items():
        out[key] = {
            "display_name": rules.display_name,
            "title_max": rules.title.max_chars,
            "highlights_enabled": rules.item_highlights.enabled,
            "highlights_max": rules.item_highlights.max_chars,
            "bullet_max": rules.bullets.max_chars,
            "description_max": rules.description.max_chars,
            "description_min": rules.description.min_chars,
            "search_terms_max_bytes": rules.search_terms.max_bytes,
            "note": rules.note,
        }
    return out


@app.post("/api/generate")
async def generate(request: Request) -> StreamingResponse:
    """Run the agent and stream progress as Server-Sent Events, ending with a `result` event."""
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "Your input is too large.")
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid input.")
    _check_rate_limit(_client_ip(request))

    async def stream() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def on_event(event: TraceEvent) -> None:
            if event.kind == "progress":
                loop.call_soon_threadsafe(queue.put_nowait, ("progress", {"message": event.message, "elapsed_s": event.elapsed_s}))

        async def worker() -> None:
            try:
                async with _run_slots:
                    result = await asyncio.to_thread(generate_listing, payload, on_event)
                await queue.put(("result", result.model_dump(mode="json")))
            except Exception:
                await queue.put(("result", {"status": "failed", "message": "Something went wrong on our side. Please try again."}))

        task = asyncio.create_task(worker())
        if _run_slots.locked():
            yield _sse("progress", {"message": "Waiting for a free slot", "elapsed_s": 0})
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(kind, data)
                if kind == "result":
                    break
        finally:
            if not task.done():
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
