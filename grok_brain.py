#!/usr/bin/env python3
"""
grok_brain.py — Grok API (free llama-3.3-70b via Groq) handles all basic tasks:
  - Telegram message formatting for scan results
  - Quick market commentary
  - Scan result summaries
  - Basic Q&A responses

Claude (claude_brain.py) is called ONLY for the final trade decision (once per scan).
"""
import json, logging, os, time
import requests

log = logging.getLogger("ob.grok")

GROQ_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

_CALL_COUNT = 0
_LAST_RESET  = time.time()
MAX_CALLS_PER_HOUR = 25  # free tier ~30 req/min but be conservative


def _grok_available():
    return bool(GROQ_KEY and GROQ_KEY not in ("", "your_groq_key_here"))


def _call_grok(system_prompt, user_prompt, max_tokens=400, temperature=0.4):
    """Call Groq API. Returns text response or None."""
    global _CALL_COUNT, _LAST_RESET
    if not _grok_available():
        return None
    # Rate-limit guard
    now = time.time()
    if now - _LAST_RESET > 3600:
        _CALL_COUNT = 0; _LAST_RESET = now
    if _CALL_COUNT >= MAX_CALLS_PER_HOUR:
        log.warning("Grok hourly limit reached — skipping call")
        return None
    try:
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        body = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=15)
        _CALL_COUNT += 1
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            log.warning(f"Grok API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Grok call failed: {e}")
    return None


# ── Telegram message formatters ───────────────────────────────────────────────

def format_scan_result(pick, regime_info, tier, rank=1, market_intel=None):
    """
    Format a single DTE-profile pick into a clean Telegram message.
    Grok adds a one-line trade thesis. Falls back to template if Grok unavailable.
    market_intel: optional dict with market_bias, fear_greed, pc_ratio, wsb_trending.
    """
    if not pick:
        return f"<b>{tier}</b>: No qualifying setup right now."

    direction = pick.get("direction", "CALL")
    dk = "call" if direction == "CALL" else "put"
    score = pick.get(f"{dk}_score", 0)
    entry = pick.get(f"entry_{dk}", {})
    symbol = pick["symbol"]
    price  = pick.get("price", 0)
    iv     = pick.get("iv_pct", 0)
    rsi    = pick.get("rsi", 50)
    move   = pick.get("move_4h", 0)
    vol    = pick.get("rel_volume", 1)
    strike = entry.get("strike", "?")
    expiry = entry.get("expiry", "?")
    opt_px = entry.get("est_option_price", "?")
    bid    = entry.get("bid", 0)
    ask    = entry.get("ask", 0)
    stop   = entry.get("stop_loss_option", "?")
    tgt    = entry.get("target_option", "?")
    dte_val = entry.get("dte", "?")
    regime = regime_info.get("regime", "NORMAL")
    src    = entry.get("price_source", "estimated")
    price_str = f"${opt_px} mid" if src == "market" else f"~${opt_px} est"

    cv_icon = "🔥" if score >= 75 else ("✅" if score >= 55 else "⚠️")
    em = "🚀" if direction == "CALL" else "💥"
    bid_ask = f"  bid/ask: ${bid:.2f}/${ask:.2f}" if bid else ""

    # Ask Grok for a one-line thesis (cheap call, no JSON needed)
    reason = pick.get(f"{dk}_reason", "")
    thesis = None
    if _grok_available() and score >= 50:
        thesis = _call_grok(
            "You are a concise options trader. Write exactly ONE sentence (max 20 words) explaining why this trade makes sense now.",
            f"{symbol} {direction} {tier} | Score {score} | {reason} | Regime {regime} | RSI {rsi:.0f} | 4h move {move:+.1f}%",
            max_tokens=60, temperature=0.3
        )

    hv30      = pick.get("hv30")
    iv_hv     = pick.get("iv_hv_ratio")
    iv_sig    = pick.get("iv_signal", "UNKNOWN")
    iv_sig_icons = {"CHEAP": "✅", "FAIR": "➡️", "RICH": "⚠️", "VERY_RICH": "❌", "UNKNOWN": ""}
    iv_line = (f"IV:{iv:.0f}%  HV:{hv30:.0f}%  Ratio:{iv_hv:.2f}x {iv_sig_icons.get(iv_sig,'')}"
               if hv30 and iv_hv else f"IV:{iv:.0f}%")

    lines = [
        f"{em} <b>{tier} — {symbol} [{direction}]</b>  {cv_icon} {score}/100",
        f"${price:.2f}  4h:{move:+.1f}%  Vol:{vol:.1f}x  RSI:{rsi:.0f}",
        f"{iv_line}",
        f"Regime: <b>{regime}</b>",
    ]

    # Add market intelligence line if available
    if market_intel:
        fg_val  = market_intel.get("fear_greed", {}).get("value", 50)
        fg_label = market_intel.get("fear_greed", {}).get("label", "Neutral")
        pc_val  = market_intel.get("pc_ratio", {}).get("pc_ratio", 0.85)
        wsb_str = ""
        for w in market_intel.get("wsb_trending", [])[:10]:
            if w.get("ticker") == symbol:
                arrow = "↑" if w.get("rank_change_24h", 0) > 0 else ("↓" if w.get("rank_change_24h", 0) < 0 else "")
                wsb_str = f" | WSB: {symbol}#{w['rank']}{arrow}"
                break
        lines.append(f"📊 F&G: {fg_val} ({fg_label}) | P/C: {pc_val:.2f}{wsb_str}")

    lines.extend([
        "─────────────────",
        f"📋 <b>Buy {direction} ${strike}</b> exp {expiry} ({dte_val}DTE)",
        f"   {price_str}{bid_ask}",
        f"🛑 Stop: ${stop}   🎯 Target: ${tgt}",
    ])

    # Add spread structure if available
    sp = pick.get("spread")
    if sp:
        lines.append("─────────────────")
        lines.append(f"📐 <b>{sp['spread_type']}</b>")
        lines.append(f"   Sell ${sp['short_strike']} | Buy ${sp['long_strike']}")
        lines.append(f"   Credit: ${sp['credit']:.2f} | Max Loss: ${sp['max_loss']:.2f}")
        lines.append(f"   Breakeven: ${sp['breakeven']}")
        lines.append(f"   🎯 TP50%: ${sp['take_profit_50']:.2f} | TP100%: ${sp['credit']:.2f}")
        lines.append(f"   🛑 Stop: ${sp['stop_loss_at']:.2f}")
        lines.append(f"   📌 {sp['bias_note']}")

    if thesis:
        lines.append(f"💡 {thesis}")
    return "\n".join(lines)


def format_lessons_message(lessons, sig_acc, dte_ev):
    """Format lessons board for Telegram /lessons command."""
    lines = ["📚 <b>Lessons Board</b>\n"]
    if not lessons:
        lines.append("No closed trades yet — lessons appear after first close.")
    for l in lessons[:6]:
        ico = "✅" if l.get("outcome") == "WIN" else "❌"
        pnl = l.get("pnl_pct", 0) or 0
        txt = (l.get("lesson") or "")[:90]
        lines.append(f"{ico} <b>{l['symbol']} {l.get('direction','')} {l.get('dte_profile','')}</b> {pnl:+.1f}%\n   {txt}")
    lines.append("\n📊 <b>EV by DTE:</b>")
    for tier in ["0DTE","7DTE","21DTE","30DTE","60DTE"]:
        s = dte_ev.get(tier, {})
        if s.get("trades", 0) > 0:
            lines.append(f"  {tier}: {s['trades']} trades | WR {s['win_rate']:.0f}% | EV {s['ev']:+.1f}%")
    if sig_acc:
        lines.append("\n⚡ <b>Signal accuracy:</b>")
        for s in sig_acc[:3]:
            lines.append(f"  {s['signal']}: {s['win_rate']:.0f}% WR ({s['total']} trades)")
    return "\n".join(lines)


def generate_ob_commentary(top_calls, top_puts, regime_info):
    """
    Generate the /ob scan summary commentary via Grok.
    Used by the existing cmd_ob command.
    Falls back to a plain summary if Grok unavailable.
    """
    regime = regime_info.get("regime", "NORMAL")
    vix    = regime_info.get("vix", 0)
    note   = regime_info.get("note", "")

    calls_txt = ", ".join(
        f"{p['symbol']} score={p.get('call_score',0)} move={p.get('move_4h',0):+.1f}%"
        for p in top_calls[:3]
    ) or "none"
    puts_txt = ", ".join(
        f"{p['symbol']} score={p.get('put_score',0)} move={p.get('move_4h',0):+.1f}%"
        for p in top_puts[:3]
    ) or "none"

    fallback = f"Regime: {regime} | VIX {vix:.1f} | {note}"

    if not _grok_available():
        return fallback

    response = _call_grok(
        "You are Marcus Reed, a sharp options market analyst. Write 2 punchy sentences about current market conditions relevant to options traders. Be specific and direct.",
        f"Regime: {regime} (VIX {vix:.1f})\nTop calls: {calls_txt}\nTop puts: {puts_txt}\n{note}",
        max_tokens=100, temperature=0.6
    )
    return response or fallback


def summarise_scan_for_log(scan_result):
    """
    One-paragraph scan summary for bot log (not sent to user).
    Uses Grok to compress scan into key insight.
    """
    regime = scan_result.get("regime", {}).get("regime", "?")
    picks  = scan_result.get("dte_picks", {})
    summary_lines = []
    for tier, p in picks.items():
        if p:
            dk = "call" if p.get("direction","CALL") == "CALL" else "put"
            summary_lines.append(f"{tier}:{p['symbol']} {p.get('direction')} sc={p.get(f'{dk}_score',0)}")

    if not _grok_available() or not summary_lines:
        return f"Scan done. Regime={regime}. Picks: {'; '.join(summary_lines)}"

    response = _call_grok(
        "You are a quant summarising a scan. One sentence, max 30 words. Key insight only.",
        f"Regime={regime} | Picks: {', '.join(summary_lines)}",
        max_tokens=60, temperature=0.2
    )
    return response or f"Regime={regime}. Picks: {'; '.join(summary_lines)}"


def grok_status():
    return {
        "available": _grok_available(),
        "model": GROQ_MODEL,
        "calls_this_hour": _CALL_COUNT,
        "limit": MAX_CALLS_PER_HOUR,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Grok status:", grok_status())
    # Test with dummy data
    fake_pick = {
        "symbol": "NVDA", "direction": "CALL", "call_score": 78,
        "price": 875.0, "move_4h": 2.3, "rsi": 58, "iv_pct": 45, "rel_volume": 2.1,
        "call_reason": "SMA alignment + momentum breakout",
        "entry_call": {
            "strike": 890, "expiry": "2026-04-17", "est_option_price": 12.50,
            "bid": 12.20, "ask": 12.80, "stop_loss_option": 6.25,
            "target_option": 22.50, "dte": 23, "price_source": "market",
        },
    }
    regime = {"regime": "BULL", "vix": 14.2, "bias": "CALLS", "note": "Low fear"}
    msg = format_scan_result(fake_pick, regime, "21DTE")
    print("\n=== Telegram message preview ===")
    print(msg)
