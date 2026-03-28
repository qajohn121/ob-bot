#!/usr/bin/env python3
"""
claude_brain.py — AI trade decision engine.
Uses Grok (free llama-3.3-70b via Groq) for ALL decisions.
No paid API needed — Groq free tier handles everything.
"""
import json, logging, os, time
import requests

log = logging.getLogger("ob.claude_brain")

GROQ_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

_last_call = 0
MIN_INTERVAL = 2  # seconds between calls


def _ai_available():
    return bool(GROQ_KEY and GROQ_KEY not in ("", "your_groq_key_here"))


def _call_ai(system_prompt, user_prompt, max_tokens=512):
    """Call Groq API. Returns response text or None."""
    global _last_call
    if not _ai_available():
        return None
    # Gentle rate limiting
    wait = MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0: time.sleep(wait)
    try:
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        body = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=15)
        _last_call = time.time()
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        log.warning(f"Groq error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"AI call failed: {e}")
    return None


# ── Core decision functions ───────────────────────────────────────────────────

def pick_best_trade(dte_picks, regime_info, capital=980):
    """
    Given DTE-profile picks and regime, ask Claude to select the single best trade.
    Returns dict with: chosen_tier, symbol, direction, reason, confidence, sizing_note
    Falls back to rule-based selection if Claude unavailable.
    """
    # Build a compact summary of picks for Claude
    picks_summary = []
    for tier, pick in dte_picks.items():
        if not pick:
            picks_summary.append(f"{tier}: No qualifying setup")
            continue
        dk = "call" if pick.get("direction","CALL") == "CALL" else "put"
        entry = pick.get(f"entry_{dk}", {})
        picks_summary.append(
            f"{tier}: {pick['symbol']} {pick.get('direction','CALL')} "
            f"score={pick.get(f'{dk}_score',0)} "
            f"strike=${entry.get('strike','?')} exp={entry.get('expiry','?')} "
            f"~${entry.get('est_option_price','?')}/contract "
            f"4h_move={pick.get('move_4h',0):+.1f}% "
            f"RSI={pick.get('rsi',50):.0f} vol={pick.get('rel_volume',1):.1f}x"
        )

    regime = regime_info.get("regime", "NORMAL")
    vix    = regime_info.get("vix", 0)
    bias   = regime_info.get("bias", "BOTH")

    system = """You are an expert options trader with deep knowledge of sentiment analysis, market regimes, and risk management.

Your role: SELECT THE SINGLE BEST TRADE from the provided DTE options profiles.

SENTIMENT-AWARE TRADING RULES:
1. Sentiment Score > +30 (bullish):
   - STRONGLY favor CALLS, especially when RSI > 60 or StockTwits consensus > 70% bull
   - Avoid PUTS unless IV is extreme or war catalyst indicates downside risk

2. Sentiment Score < -30 (bearish):
   - STRONGLY favor PUTS, especially with WSB backing or bankruptcy risk flagged
   - CALLS only if IV rank < 20 or unusual positive catalyst present

3. Sentiment Score near 0 (neutral ±15):
   - Prefer IRON CONDORS or tight SPREADS over naked directional trades
   - Require strong technical confluence (RSI extremes + MACD divergence)

4. Unusual Sentiment Momentum (velocity > 0.5 or < -0.5):
   - Flag as HIGH-CONFIDENCE setup when all signals aligned
   - Boost trade conviction score by +5-10% if direction matches regime

5. Social Consensus Strong (StockTwits > 75% bull/bear):
   - Amplify position size recommendation
   - Trade even with moderate technical setup if consensus is overwhelming

6. War Catalyst Present:
   - CALLS: Boost conviction if catalyst bullish, otherwise reduce by 20%
   - PUTS: Increase conviction if geopolitical risk hedging warranted

7. Regime Alignment:
   - FEAR regime (VIX > 25): Prefer PUTS and wide spreads
   - GREED regime (VIX < 15): Favor naked CALLS and ratio spreads
   - NORMAL regime: Balanced approach, follow technical setup

DECISION PRIORITY:
1. Technical score + regime match = foundation
2. Sentiment alignment = conviction boost
3. Social consensus = confidence multiplier
4. Catalysts = risk/reward shaper
5. Momentum = early entry timing signal

Be concise and decisive. Always respond in JSON."""
    user = f"""Market regime: {regime} (VIX {vix:.1f}) — bias: {bias}
Available capital: ${capital}

Scanner picks by DTE tier:
{chr(10).join(picks_summary)}

Pick the SINGLE best trade. Consider:
1. Regime bias ({bias}) — prefer that direction
2. Higher conviction score = better
3. 0DTE requires explosive momentum; 7DTE needs earnings catalyst; longer DTE for trend plays
4. Avoid very high-IV entries unless there's a strong catalyst

Respond ONLY with this JSON:
{{
  "chosen_tier": "21DTE",
  "symbol": "NVDA",
  "direction": "CALL",
  "confidence": "HIGH",
  "reason": "one sentence why this is the best trade today",
  "sizing_note": "one sentence on position sizing given capital and regime",
  "pass_on_trading_today": false,
  "pass_reason": ""
}}"""

    response = _call_ai(system, user, max_tokens=300)
    if response:
        try:
            start = response.find("{"); end = response.rfind("}") + 1
            data = json.loads(response[start:end])
            data["source"] = "groq"
            return data
        except Exception as e:
            log.warning(f"AI pick parse error: {e} — raw: {response[:200]}")

    # ── Fallback: rule-based pick ─────────────────────────────────────────────
    return _rule_based_pick(dte_picks, regime_info)


def _rule_based_pick(dte_picks, regime_info):
    """Fallback when Claude is unavailable — picks best by score."""
    bias = regime_info.get("bias", "BOTH")
    best_tier = None; best_score = -1; best_pick = None
    priority = ["21DTE", "30DTE", "7DTE", "60DTE", "0DTE"]
    for tier in priority:
        pick = dte_picks.get(tier)
        if not pick: continue
        dk = "call" if pick.get("direction","CALL") == "CALL" else "put"
        score = pick.get(f"{dk}_score", 0)
        # Penalise if direction conflicts with regime bias
        if bias == "CALLS" and pick.get("direction") == "PUT": score -= 15
        if bias == "PUTS"  and pick.get("direction") == "CALL": score -= 15
        if score > best_score:
            best_score = score; best_tier = tier; best_pick = pick
    if not best_pick:
        return {"chosen_tier": None, "symbol": None, "direction": None,
                "confidence": "LOW", "reason": "No qualifying setups today",
                "sizing_note": "Stay flat", "pass_on_trading_today": True,
                "pass_reason": "No picks met quality threshold", "source": "rule"}
    dk = "call" if best_pick.get("direction","CALL") == "CALL" else "put"
    return {
        "chosen_tier": best_tier,
        "symbol": best_pick["symbol"],
        "direction": best_pick.get("direction","CALL"),
        "confidence": "HIGH" if best_score >= 75 else ("MEDIUM" if best_score >= 55 else "LOW"),
        "reason": best_pick.get(f"{dk}_reason", "Strong scanner signal"),
        "sizing_note": "Use 1 contract, risk max $100",
        "pass_on_trading_today": False,
        "pass_reason": "",
        "source": "rule",
    }


def validate_trade_thesis(pick, regime_info):
    """
    Ask Claude to validate a single pick's thesis before entry.
    Returns: {valid: bool, concerns: list, recommendation: str}
    """
    if not pick:
        return {"valid": False, "concerns": ["No pick provided"], "recommendation": "Pass"}

    dk = "call" if pick.get("direction","CALL") == "CALL" else "put"
    entry = pick.get(f"entry_{dk}", {})
    score = pick.get(f"{dk}_score", 0)
    reason = pick.get(f"{dk}_reason", "")
    regime = regime_info.get("regime", "NORMAL")

    system = (
        "You are a risk-conscious options trader. Your job is to spot flaws in proposed trades "
        "before capital is risked. Be concise. Respond in JSON only."
    )
    user = f"""Trade to validate:
Symbol: {pick['symbol']} | Direction: {pick.get('direction')} | DTE: {entry.get('dte','?')}
Score: {score}/100 | Regime: {regime}
Strike: ${entry.get('strike','?')} | Expiry: {entry.get('expiry','?')} | Est Price: ${entry.get('est_option_price','?')}
RSI: {pick.get('rsi',50):.0f} | 4h move: {pick.get('move_4h',0):+.1f}% | IV: {pick.get('iv_pct',30):.0f}%
Scanner reason: {reason}

Is this trade valid to enter? Key checks:
- RSI overbought/oversold risk
- IV too high (premium seller's environment)
- Score vs regime alignment
- Any obvious red flags

Respond ONLY with JSON:
{{
  "valid": true,
  "concerns": ["concern1 if any"],
  "recommendation": "ENTER / PASS / REDUCE_SIZE"
}}"""

    response = _call_ai(system, user, max_tokens=200)
    if response:
        try:
            start = response.find("{"); end = response.rfind("}") + 1
            data = json.loads(response[start:end])
            data["source"] = "groq"
            return data
        except: pass

    # Fallback
    concerns = []
    if score < 55: concerns.append(f"Low score ({score}) — below recommended threshold")
    if pick.get("iv_pct", 30) > 80: concerns.append("Very high IV — consider waiting for IV drop")
    if regime == "VOLATILE" and pick.get("direction") == "CALL": concerns.append("Volatile regime favors puts")
    return {
        "valid": score >= 55 and len(concerns) == 0,
        "concerns": concerns,
        "recommendation": "ENTER" if score >= 60 else "PASS",
        "source": "rule",
    }


def get_market_commentary(regime_info, top_picks):
    """
    Ask Claude for a short market commentary (used in dashboard / /ob command).
    Grok handles the Marcus Reed personality; Claude handles the factual analysis.
    """
    regime = regime_info.get("regime", "NORMAL")
    vix    = regime_info.get("vix", 0)
    bias   = regime_info.get("bias", "BOTH")
    note   = regime_info.get("note", "")

    picks_txt = []
    for i, p in enumerate(top_picks[:3], 1):
        dk = "call" if p.get("direction","CALL") == "CALL" else "put"
        picks_txt.append(f"#{i} {p['symbol']} {p.get('direction')} score={p.get(f'{dk}_score',0)}")

    system = "You are a sharp, no-nonsense options market analyst. Write 2-3 sentences max. Be specific."
    user = f"""Market: {regime} (VIX {vix:.1f}) — {note}
Top scanner picks: {', '.join(picks_txt) if picks_txt else 'No picks yet'}
Write 2-3 sentences of market context relevant to options traders right now."""

    response = _call_ai(system, user, max_tokens=150)
    return response or f"Market regime: {regime}. VIX at {vix:.1f}. {note}"


# ── Public convenience wrapper ────────────────────────────────────────────────

def claude_status():
    """Return dict showing if AI brain is available."""
    return {
        "available": _ai_available(),
        "model": GROQ_MODEL,
        "backend": "groq_free",
        "key_set": bool(GROQ_KEY),
    }


if __name__ == "__main__":
    import logging as _log
    _log.basicConfig(level=logging.DEBUG)

    # Quick test without API key
    fake_picks = {
        "0DTE": None,
        "7DTE": None,
        "21DTE": {
            "symbol": "NVDA", "direction": "CALL", "call_score": 78,
            "move_4h": 2.3, "rsi": 58, "iv_pct": 45, "rel_volume": 2.1,
            "call_reason": "SMA alignment + momentum breakout",
            "entry_call": {"strike": 890, "expiry": "2026-04-17", "est_option_price": 12.50,
                           "stop_loss_option": 6.25, "target_option": 22.50, "dte": 23},
        },
        "30DTE": None, "60DTE": None,
    }
    regime = {"regime": "BULL", "vix": 14.2, "bias": "CALLS", "note": "Low fear — calls favored"}

    print("=== Claude status:", claude_status())
    result = pick_best_trade(fake_picks, regime)
    print("=== Best trade pick:")
    print(json.dumps(result, indent=2))
    val = validate_trade_thesis(fake_picks["21DTE"], regime)
    print("=== Thesis validation:")
    print(json.dumps(val, indent=2))
