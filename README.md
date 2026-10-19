# Listing Agent (Amazon US / TikTok Shop US)

Turns a seller's product info (Chinese or English) into a compliant English listing: title, Item Highlights (Amazon), 5 bullets, description, and backend search terms. It also flags compliance risks and claims the seller never provided.

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then put your key after ANTHROPIC_API_KEY=
```

## Run
```bash
.venv/bin/python run.py examples/sample_input_zh.json     # Chinese input, Amazon US
.venv/bin/python run.py examples/sample_input_en.json     # English input, TikTok Shop US
.venv/bin/python run.py examples/sample_input_vague.json  # just "cup" -> clarifying questions
```

## Website
```bash
.venv/bin/uvicorn web.app:app --reload        # then open http://127.0.0.1:8000
```
Sellers fill in a form, watch live progress, and get the listing with character counts, copy buttons, and issues grouped by severity. If the product info is too vague, they answer the clarifying questions and resubmit.

- `POST /api/generate`: JSON body = `ListingInput`; responds with Server-Sent Events (`progress` events, then one `result` event with the `ListingResult`).
- `GET /api/platforms`: field limits from `config/platform_rules.json`.
- Basic abuse protection: per-IP hourly limit (`RATE_LIMIT_PER_HOUR`), concurrent run cap (`MAX_CONCURRENT_RUNS`), 64 KB body limit.

## Use from a web backend
```python
from agent import generate_listing
result = generate_listing(payload_dict, on_event=lambda e: push_to_client(e.to_dict()))
return result.model_dump()
```
`generate_listing` never raises for AI-service errors; it returns `status="failed"` with a friendly message.

## Layout
- `harness/`: generic runtime (LLM client with retries, tool registry, agent loop, trace, input guards). It never imports `agent/`.
- `agent/`: listing logic (models, prompts, fact extraction, checks, tools, pipeline).
- `config/`: platform rules and the banned-terms list (edit the JSON; no code changes needed).

Rules change often. Verify in Seller Central before launch.
