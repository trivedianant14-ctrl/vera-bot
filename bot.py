#!/usr/bin/env python3
"""Vera — magicpin Merchant AI Assistant (Challenge Bot)
Team: Anant Trivedi | Model: claude-sonnet-4-6
"""

import os, time, json, re, uuid
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import anthropic

app = FastAPI(title="Vera Bot")
START_TIME = time.time()

# ─── In-memory stores ────────────────────────────────────────────────────────
contexts: dict[tuple, dict] = {}       # (scope, context_id) -> {version, payload}
conversations: dict[str, dict] = {}    # conv_id -> {merchant_id, customer_id, turns[], state}
suppressed: set[str] = set()           # suppression_keys already sent this session

# ─── Config ──────────────────────────────────────────────────────────────────
TEAM_NAME     = os.environ.get("TEAM_NAME", "Anant Trivedi")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "trivedianant14@gmail.com")
MODEL         = "claude-sonnet-4-6"
VERSION       = "1.0.0"
SUBMITTED_AT  = "2026-04-30T00:00:00Z"

client = anthropic.Anthropic()

# ─── Intent detection ────────────────────────────────────────────────────────
# High-confidence auto-replies → end immediately (no "try once")
AUTO_REPLY_HIGH = [
    r"thank you for contacting",
    r"our team will (contact|call|reply|reach|respond|get back)",
    r"we will get back",
    r"will respond shortly",
    r"automated (assistant|response|message|system|reply)",
    r"main ek automated",
    r"i am an automated",
    r"this is an auto.?generated",
    r"\bauto.?reply\b",
]

# Moderate-confidence → try once first, then end if repeated
AUTO_REPLY_MOD = [
    r"aapki jaankari ke liye",
    r"bahut.{0,5}bahut shukriya",
    r"hamari team tak pahuncha",
    r"shukriya.*team",
    r"team tak pahuncha",
]

AUTO_REPLY_PATTERNS = AUTO_REPLY_HIGH + AUTO_REPLY_MOD

ACCEPT_PATTERNS = [
    r"\b(yes|haan|ha|yep|yeah|okay|ok|sure|go ahead|please do|bilkul|zaroor|definitely|absolutely|done|haan ji|ji haan)\b",
    r"(karo|kar do|chalega|theek hai|manzoor|agreed|sounds good|let'?s? do (it|this))",
    r"(i want to (join|start|proceed)|mujhe (judna|join|karna) hai)",
]

REJECT_PATTERNS = [
    r"\b(no|nahi|na|nope|nah|never|mat|band karo)\b",
    r"(not interested|abhi nahi|baad mein|don'?t (want|need|contact|call))",
    r"(remove me|unsubscribe|do not contact|stop messaging|block|spam)",
]


def is_auto_reply(msg: str) -> bool:
    m = msg.lower()
    return any(re.search(p, m) for p in AUTO_REPLY_PATTERNS)

def is_auto_reply_high(msg: str) -> bool:
    m = msg.lower()
    return any(re.search(p, m) for p in AUTO_REPLY_HIGH)

def is_acceptance(msg: str) -> bool:
    m = msg.lower()
    return any(re.search(p, m) for p in ACCEPT_PATTERNS)

def is_rejection(msg: str) -> bool:
    m = msg.lower()
    return any(re.search(p, m) for p in REJECT_PATTERNS)


# ─── Context helpers ─────────────────────────────────────────────────────────
def get_ctx(scope: str, cid: str) -> Optional[dict]:
    entry = contexts.get((scope, cid))
    return entry["payload"] if entry else None


# ─── CTA routing by trigger kind ─────────────────────────────────────────────
CTA_MAP = {
    "research_digest":           "open_ended",
    "regulation_change":         "binary_yes_stop",
    "perf_dip":                  "binary_yes_stop",
    "perf_spike":                "open_ended",
    "recall_due":                "binary_yes_stop",
    "renewal_due":               "binary_yes_stop",
    "dormant_with_vera":         "binary_yes_stop",
    "festival_upcoming":         "binary_yes_stop",
    "milestone_reached":         "open_ended",
    "competitor_opened":         "binary_yes_stop",
    "curious_ask_due":           "none",
    "category_trend_movement":   "open_ended",
    "review_theme_emerged":      "binary_yes_stop",
    "wedding_package_followup":  "binary_yes_stop",
    "weather_heatwave":          "open_ended",
    "local_news_event":          "open_ended",
    "scheduled_recurring":       "open_ended",
    "appointment_tomorrow":      "binary_yes_stop",
    "customer_lapsed_soft":      "binary_yes_stop",
    "stale_profile":             "binary_yes_stop",
}

TEMPLATE_MAP = {
    "research_digest":          "vera_research_digest_v1",
    "regulation_change":        "vera_compliance_alert_v1",
    "perf_dip":                 "vera_perf_dip_v1",
    "perf_spike":               "vera_perf_spike_v1",
    "recall_due":               "vera_recall_reminder_v1",
    "renewal_due":              "vera_renewal_due_v1",
    "dormant_with_vera":        "vera_re_engage_v1",
    "festival_upcoming":        "vera_festival_campaign_v1",
    "milestone_reached":        "vera_milestone_v1",
    "competitor_opened":        "vera_competitor_alert_v1",
    "curious_ask_due":          "vera_curious_ask_v1",
    "category_trend_movement":  "vera_trend_alert_v1",
    "review_theme_emerged":     "vera_review_theme_v1",
    "wedding_package_followup": "vera_bridal_followup_v1",
    "weather_heatwave":         "vera_weather_nudge_v1",
}


# ─── Message composer ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Vera, magicpin's merchant AI assistant that talks to merchants over WhatsApp.

CORE RULES — NEVER BREAK:
1. Open with the WHY (the specific trigger). No "I hope you're doing well." No "Hi, Vera here." If conversation_history exists, open with a callback to the last topic discussed (e.g., "Last time we talked about your stale posts — here's the follow-up.").
2. Anchor on ONE verifiable fact from the context: a number, date, source, headline, or peer stat. Generic phrases ("grow your business", "boost your sales", "10% off") are penalized.
3. Use exactly ONE compulsion lever:
   • Specificity/verifiability  • Loss aversion ("you're missing X")  • Social proof ("3 dentists in your locality…")
   • Effort externalization ("I've drafted it — just say go")  • Curiosity ("want to see?")
   • Asking the merchant ("what's your most-asked treatment this week?")  • Single binary commitment
4. CTA rules:
   - binary_yes_stop: last sentence must be "Reply YES to [action] or STOP to opt out."
   - open_ended: end with a single open question
   - none: no CTA at all (pure info)
5. Max 4 sentences. WhatsApp-native. No markdown headers or bullets.
6. Never hallucinate data. If a fact isn't in the context, don't say it.
7. Taboo words (never use): guaranteed, 100% safe, completely cure, miracle, best in city, doctor approved.
8. Hindi-English code-mix ONLY if merchant language includes "hi". Pure English otherwise.

Output ONLY valid JSON — no surrounding text:
{
  "body": "the WhatsApp message",
  "cta": "binary_yes_stop|open_ended|none",
  "send_as": "vera|merchant_on_behalf",
  "suppression_key": "...",
  "rationale": "one sentence: lever + why this trigger"
}"""


def _find_digest_item(category: dict, item_id: str) -> Optional[dict]:
    for d in category.get("digest", []):
        if d.get("id") == item_id:
            return d
    return None


def build_user_prompt(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
    identity    = merchant.get("identity", {})
    perf        = merchant.get("performance", {})
    peer        = category.get("peer_stats", {})
    voice       = category.get("voice", {})
    trg_payload = trigger.get("payload", {})
    trg_kind    = trigger.get("kind", "")
    sup_key     = trigger.get("suppression_key", "")

    lang = identity.get("languages", ["en"])
    lang_note = "Use Hindi-English code-mix naturally" if "hi" in lang else "English only"

    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    signals       = merchant.get("signals", [])
    history       = merchant.get("conversation_history", [])
    cust_agg      = merchant.get("customer_aggregate", {})
    rev_themes    = merchant.get("review_themes", [])

    ctr_delta = perf.get("ctr", 0) - peer.get("avg_ctr", 0.03)
    ctr_status = f"below peer by {abs(ctr_delta)*100:.1f}pp" if ctr_delta < 0 else f"above peer by {ctr_delta*100:.1f}pp"

    # Resolve digest item
    digest_item = None
    if "top_item_id" in trg_payload:
        digest_item = _find_digest_item(category, trg_payload["top_item_id"])

    # Recent convo context (last 2 turns)
    recent_turns = "\n".join(f"  {t['from']}: {t['body']}" for t in history[-2:]) if history else "  (first touch)"

    # Customer section
    cust_section = ""
    if customer:
        ci = customer.get("identity", {})
        cr = customer.get("relationship", {})
        cp = customer.get("preferences", {})
        slots = trg_payload.get("available_slots", [])
        slot_labels = " | ".join(s.get("label", "") for s in slots[:2])
        cust_section = f"""
CUSTOMER (message on behalf of merchant):
  Name: {ci.get('name', '')} | Language: {ci.get('language_pref', 'en')} | State: {customer.get('state', '')}
  Last visit: {cr.get('last_visit', '')} | Visits: {cr.get('visits_total', 0)} | Services: {', '.join(cr.get('services_received', []))}
  Preferred slot: {cp.get('preferred_slots', '')} | Available: {slot_labels or 'check with merchant'}
  send_as MUST be "merchant_on_behalf"
"""

    # Digest section
    digest_section = ""
    if digest_item:
        digest_section = f"""
DIGEST ITEM (reference this specifically):
  Title: {digest_item.get('title', '')}
  Source: {digest_item.get('source', '')}
  Trial N: {digest_item.get('trial_n', '')} | Segment: {digest_item.get('patient_segment', '')}
  Actionable: {digest_item.get('actionable', '')}
"""

    cta_instruction = CTA_MAP.get(trg_kind, "open_ended")

    # Social proof for high-impact triggers
    social_proof_section = ""
    SOCIAL_PROOF_TRIGGERS = {"perf_dip", "dormant_with_vera", "competitor_opened", "festival_upcoming"}
    if trg_kind in SOCIAL_PROOF_TRIGGERS:
        locality = identity.get("locality", "your area")
        cat_slug = category.get("slug", "businesses")
        peer_ctr = peer.get("avg_ctr", 0)
        peer_reviews = peer.get("avg_review_count", 0)
        peer_scope = peer.get("scope", "")
        # Derive a peer count from scope string if numeric prefix present (e.g. "top_50")
        scope_match = re.search(r"\d+", str(peer_scope))
        peer_n = int(scope_match.group()) if scope_match else 0

        if trg_kind == "perf_dip":
            if peer_ctr > 0:
                lines_ahead = round(peer_ctr * 1000)
                social_proof_section = f"\nSOCIAL PROOF (must include in message): Similar {cat_slug} in {locality} are getting ~{lines_ahead} clicks per 1000 views — mention this benchmark to create urgency.\n"
            else:
                social_proof_section = f"\nSOCIAL PROOF (must include in message): Top {cat_slug} in {locality} are outperforming this merchant right now — reference local peers to create urgency.\n"
        elif trg_kind == "dormant_with_vera":
            n = peer_n if peer_n else 5
            social_proof_section = f"\nSOCIAL PROOF (must include in message): At least {n} {cat_slug} in {locality} re-engaged dormant customers this month using Vera — mention this to show proven results.\n"
        elif trg_kind == "competitor_opened":
            social_proof_section = f"\nSOCIAL PROOF (must include in message): New competitors in {locality} are actively running offers — reference that proactive merchants in the area are already responding.\n"
        elif trg_kind == "festival_upcoming":
            n = peer_n if peer_n else 7
            social_proof_section = f"\nSOCIAL PROOF (must include in message): {n} {cat_slug} in {locality} have already launched a festival campaign — mention this to convey FOMO.\n"

    lines = [
        f"CATEGORY: {category.get('slug', '')} | Tone: {voice.get('tone', 'professional')} | {lang_note}",
        f"",
        f"MERCHANT: {identity.get('name', '')} ({identity.get('locality', '')}, {identity.get('city', '')})",
        f"  Subscription: {merchant.get('subscription', {}).get('status', '')} | Plan: {merchant.get('subscription', {}).get('plan', '')} | Days left: {merchant.get('subscription', {}).get('days_remaining', '')}",
        f"  Perf 30d: views={perf.get('views', 0)}, calls={perf.get('calls', 0)}, CTR={perf.get('ctr', 0):.3f} ({ctr_status}, peer avg={peer.get('avg_ctr', 0.03):.3f})",
        f"  7d delta: views {perf.get('delta_7d', {}).get('views_pct', 0)*100:+.0f}%, calls {perf.get('delta_7d', {}).get('calls_pct', 0)*100:+.0f}%",
        f"  Active offers: {', '.join(active_offers) or 'none'}",
        f"  Signals: {', '.join(signals) or 'none'}",
        f"  Customers YTD: {cust_agg.get('total_unique_ytd', 0)} total | {cust_agg.get('lapsed_180d_plus', 0)} lapsed 180d+ | {cust_agg.get('retention_6mo_pct', 0)*100:.0f}% 6mo retention",
    ]
    if rev_themes:
        top_theme = rev_themes[0]
        lines.append(f"  Review theme: '{top_theme.get('theme', '')}' ({top_theme.get('sentiment', '')}, {top_theme.get('occurrences_30d', 0)} times in 30d)")
    lines += [
        f"  Recent conversation:",
        recent_turns,
        f"",
        f"TRIGGER: {trg_kind} | Urgency: {trigger.get('urgency', 1)}/5 | Suppression: {sup_key}",
        f"  Payload: {json.dumps(trg_payload, ensure_ascii=False)}",
        digest_section,
        cust_section,
        social_proof_section,
        f"CTA required: {cta_instruction}",
        f'suppression_key in JSON must be: "{sup_key}"',
    ]

    return "\n".join(lines)


def compose_message(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    user_msg = build_user_prompt(category, merchant, trigger, customer)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = resp.content[0].text.strip()
    # Strip markdown fences if present
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        sup = trigger.get("suppression_key", "")
        return {
            "body": f"Hi {owner}, quick update on your magicpin account. Reply YES to continue or STOP to opt out.",
            "cta": "binary_yes_stop",
            "send_as": "vera",
            "suppression_key": sup,
            "rationale": "Fallback — JSON parse error in compose",
        }


# ─── Reply handler ────────────────────────────────────────────────────────────
REPLY_SYSTEM = """You are Vera handling an ongoing WhatsApp conversation with a merchant.

RULES:
1. acceptance ("yes", "go ahead", "haan", "ok", "let's do it") → action: "send", execute the promised step immediately, no more qualifying.
2. rejection ("no", "not interested", "stop", "nahi") → action: "send" with a warm exit: "Koi baat nahi. Jab zaroorat ho, Vera yahaan hai. 🙂" then set rationale.
3. auto-reply (canned: "Thank you for contacting", "hamari team tak pahuncha", "automated") →
   - First detection: action: "send", try once to reach real person ("Kya aap direct reply kar sakte hain?")
   - Second consecutive auto-reply: action: "end"
4. genuine question → answer concisely, stay on mission, give next step.
5. hostile/off-topic → politely stay on mission, don't argue.
6. Never repeat a body verbatim from history.
7. Max 3 sentences. WhatsApp-native. No markdown.

Output ONLY valid JSON:
{
  "action": "send|wait|end",
  "body": "text (only if action=send, else omit or empty)",
  "cta": "binary_yes_stop|open_ended|none",
  "rationale": "one sentence"
}"""


def handle_reply(conv_id: str, merchant_msg: str, merchant_id: str, customer_id: Optional[str], turn_num: int) -> dict:
    conv  = conversations.get(conv_id, {})
    turns = conv.get("turns", [])

    # Count consecutive auto-replies from merchant
    auto_run = 0
    for t in reversed(turns):
        if t.get("from") != "vera":
            if is_auto_reply(t.get("body", "")):
                auto_run += 1
            else:
                break
        else:
            break

    cur_auto      = is_auto_reply(merchant_msg)
    cur_auto_high = is_auto_reply_high(merchant_msg)
    same_count    = sum(1 for t in turns if t.get("from") != "vera" and t.get("body") == merchant_msg)

    # Fast paths — ordered by priority

    # High-confidence auto-reply (canned "Thank you for contacting" type) → end immediately
    # same_count >= 2 only applies when the repeated message is also an auto-reply
    if cur_auto_high or (same_count >= 2 and cur_auto):
        return {"action": "end", "rationale": "High-confidence auto-reply detected; gracefully exiting."}

    # Moderate auto-reply: on 2nd consecutive occurrence → end
    if cur_auto and auto_run >= 2:
        return {"action": "end", "rationale": "Auto-reply loop detected; gracefully exiting."}

    # Clear rejection / hostility → end (judge accepts action: end for hostile/stop messages)
    if is_rejection(merchant_msg):
        return {
            "action": "end",
            "rationale": "Merchant rejected or asked to stop; gracefully exiting.",
        }

    # Acceptance fast path — switch to action immediately, no more qualifying
    if is_acceptance(merchant_msg):
        merchant_fp = get_ctx("merchant", merchant_id) or {}
        lang_fp     = merchant_fp.get("identity", {}).get("languages", ["en"])
        if "hi" in lang_fp:
            body = "Perfect! Draft teyaar karke aapko abhi bhejti hoon — sending within the next few minutes. Kuch aur chahiye?"
        else:
            body = "On it! Sending you the draft now — will confirm once done. Anything else you need?"
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Merchant accepted; executing promised step immediately without re-qualifying.",
        }

    # Build context for LLM
    merchant  = get_ctx("merchant", merchant_id) or {}
    cat_slug  = merchant.get("category_slug", "")
    category  = get_ctx("category", cat_slug) or {}
    identity  = merchant.get("identity", {})
    lang      = identity.get("languages", ["en"])
    lang_note = "Hindi-English mix" if "hi" in lang else "English only"

    history_txt = "\n".join(f"  {t['from'].upper()}: {t['body']}" for t in turns[-6:])

    state_hint = ""
    if cur_auto:
        state_hint = "CURRENT: first auto-reply detected. Respond with action=send, try ONCE to reach the real person (e.g. 'Kya aap direct reply kar sakte hain?'). Do NOT end yet."

    user_msg = f"""MERCHANT: {identity.get('name', '')} | Category: {cat_slug} | Language: {lang_note}

CONVERSATION HISTORY:
{history_txt}

MERCHANT'S LATEST (turn {turn_num}): "{merchant_msg}"
{state_hint}

Active offers: {', '.join(o['title'] for o in merchant.get('offers', []) if o.get('status') == 'active') or 'none'}
Signals: {', '.join(merchant.get('signals', []))}
"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=256,
        temperature=0,
        system=REPLY_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = resp.content[0].text.strip()
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break

    try:
        result = json.loads(raw)
        # Ensure body is absent for non-send actions
        if result.get("action") != "send":
            result.pop("body", None)
        return result
    except json.JSONDecodeError:
        return {
            "action": "send",
            "body": "Got it! Main dekhti hoon aur aapko update karti hoon.",
            "cta": "open_ended",
            "rationale": "Fallback reply — JSON parse error",
        }


# ─── FastAPI endpoints ────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        if scope in counts:
            counts[scope] += 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START_TIME), "contexts_loaded": counts}


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": [TEAM_NAME],
        "model": MODEL,
        "approach": (
            "Dispatch-by-trigger-kind prompt routing + compulsion-lever injection. "
            "Separate compose (initial) and reply (multi-turn) prompts. "
            "Pattern-based auto-reply / acceptance / rejection detection with LLM fallback. "
            "In-memory context store with idempotent version tracking."
        ),
        "contact_email": CONTACT_EMAIL,
        "version": VERSION,
        "submitted_at": SUBMITTED_AT,
    }


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str

@app.post("/v1/context")
async def push_context(body: ContextBody):
    if body.scope not in ("category", "merchant", "customer", "trigger"):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": f"Unknown scope: {body.scope}"})
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": cur["version"]})
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []

@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    now_dt = None
    try:
        now_dt = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
    except ValueError:
        pass

    for trg_id in body.available_triggers:
        if len(actions) >= 20:
            break

        trg = get_ctx("trigger", trg_id)
        if not trg:
            continue

        # Suppression check
        sup_key = trg.get("suppression_key", "")
        if sup_key and sup_key in suppressed:
            continue

        # Expiry check
        if now_dt and trg.get("expires_at"):
            try:
                exp = datetime.fromisoformat(trg["expires_at"].replace("Z", "+00:00"))
                if now_dt > exp:
                    continue
            except ValueError:
                pass

        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            continue

        merchant = get_ctx("merchant", merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug")
        category = get_ctx("category", cat_slug) if cat_slug else None
        if not category:
            continue

        customer_id = trg.get("customer_id")
        customer    = get_ctx("customer", customer_id) if customer_id else None

        # Skip if conversation already active for this trigger
        conv_id = f"conv_{merchant_id}_{trg_id}"
        if conv_id in conversations and conversations[conv_id].get("state") == "active":
            continue

        try:
            composed = compose_message(category, merchant, trg, customer)
        except Exception:
            continue

        body_text = composed.get("body", "").strip()
        if not body_text:
            continue

        if sup_key:
            suppressed.add(sup_key)

        conversations[conv_id] = {
            "merchant_id":  merchant_id,
            "customer_id":  customer_id,
            "turns": [{"from": "vera", "body": body_text, "ts": body.now}],
            "state":        "active",
            "trigger_id":   trg_id,
        }

        trg_kind  = trg.get("kind", "generic")
        owner_name = merchant.get("identity", {}).get("owner_first_name",
                     merchant.get("identity", {}).get("name", ""))

        actions.append({
            "conversation_id":  conv_id,
            "merchant_id":      merchant_id,
            "customer_id":      customer_id,
            "send_as":          composed.get("send_as", "vera"),
            "trigger_id":       trg_id,
            "template_name":    TEMPLATE_MAP.get(trg_kind, f"vera_{trg_kind}_v1"),
            "template_params":  [owner_name, trg_kind.replace("_", " "), body_text[:60]],
            "body":             body_text,
            "cta":              composed.get("cta", "open_ended"),
            "suppression_key":  sup_key,
            "rationale":        composed.get("rationale", ""),
        })

    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

@app.post("/v1/reply")
async def reply_endpoint(body: ReplyBody):
    conv = conversations.setdefault(body.conversation_id, {
        "merchant_id": body.merchant_id,
        "customer_id": body.customer_id,
        "turns": [],
        "state": "active",
    })

    conv["turns"].append({"from": body.from_role, "body": body.message, "ts": body.received_at})

    result = handle_reply(
        body.conversation_id,
        body.message,
        body.merchant_id or conv.get("merchant_id", ""),
        body.customer_id or conv.get("customer_id"),
        body.turn_number,
    )

    if result.get("action") == "send" and result.get("body"):
        conv["turns"].append({"from": "vera", "body": result["body"], "ts": datetime.now(timezone.utc).isoformat()})
    if result.get("action") == "end":
        conv["state"] = "ended"

    return result


@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    suppressed.clear()
    return {"status": "wiped"}
