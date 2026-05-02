# Vera Bot — magicpin AI Challenge

**Team:** Anant Trivedi | **Model:** claude-sonnet-4-6 | **Version:** 1.0.0

## Approach

**Dispatch-by-trigger-kind + compulsion-lever injection.**

Every incoming trigger is routed to one of two prompts:
1. **Compose prompt** — builds the opening message from all 4 context layers.
2. **Reply prompt** — handles multi-turn conversation with pattern-based intent detection.

### Compose strategy
- Maps each `trigger.kind` → preferred CTA type (`binary_yes_stop`, `open_ended`, `none`).
- Injects a single compulsion lever directive into the prompt (specificity, loss aversion, social proof, curiosity, effort externalization, or asking the merchant).
- Anchors on verifiable facts: specific numbers from `performance`, named sources from `digest`, exact prices from `offer_catalog`.
- Language detection: Hindi-English code-mix when merchant `languages` includes `"hi"`.

### Reply strategy
- Pattern-based detection for auto-replies, acceptances, and rejections — fast path before LLM.
- Auto-reply loop: tries once to reach the real person, then exits gracefully on second detection.
- Acceptance: switches immediately to action mode without asking another qualifying question.

### Key decisions
- **In-memory store with version tracking** — idempotent by `(scope, context_id, version)`, newer version atomically replaces older.
- **Suppression set** — prevents re-sending the same trigger within a session.
- **Temperature=0** — deterministic outputs for reproducibility.
- **< 30s per call** — single LLM call per compose/reply; no chained calls.

## What additional context would have helped
- Real merchant `phone_number` or WhatsApp session status — to know whether a 24h window is open.
- Historical suppression state across sessions — so the bot knows what it already sent before this test run.
- Category-specific slot availability format — varies widely across dentists, salons, restaurants.
- Merchant's preferred language per conversation (some code-switch mid-thread).

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
export TEAM_NAME="Anant Trivedi"
export CONTACT_EMAIL="trivedianant14@gmail.com"
uvicorn bot:app --port 8080

# Smoke test
curl http://localhost:8080/v1/healthz
curl http://localhost:8080/v1/metadata
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/healthz` | Liveness probe |
| GET | `/v1/metadata` | Bot identity |
| POST | `/v1/context` | Receive context push (idempotent) |
| POST | `/v1/tick` | Periodic wake-up; returns proactive actions |
| POST | `/v1/reply` | Handle merchant/customer reply; returns next move |
| POST | `/v1/teardown` | Wipe in-memory state |
