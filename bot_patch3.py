#!/usr/bin/env python3
"""
bot_patch3.py — adds autonomous scheduled trading sessions.

Schedule (all times America/New_York = EST/EDT):
  10:00 AM  — Morning scan: top 5 calls + top 5 puts, auto-logs best DTE pick
  12:30 PM  — Midday review: P&L on open trades, hold/adjust recommendation
   3:30 PM  — EOD review: closes trades, runs learning cycle, tomorrow's strategy

Also:
  - Auto-captures TELEGRAM_CHAT_ID from first user message
  - Saves chat_id to data/chat_id.txt for scheduled jobs
  - Adds /status command showing next scheduled run times
"""
import re, sys
from pathlib import Path

BOT = Path("/home/ubuntu/ob-bot/bot.py")

# ════════════════════════════════════════════════════════════════════════════
# Code blocks to inject
# ════════════════════════════════════════════════════════════════════════════

IMPORTS_BLOCK = '''
import datetime, pytz
from zoneinfo import ZoneInfo
EST = ZoneInfo("America/New_York")
_CHAT_ID_FILE = Path("/home/ubuntu/ob-bot/data/chat_id.txt")
'''

CHAT_ID_HELPER = '''
def _get_chat_id():
    """Read saved chat_id for scheduled messages."""
    cid = os.getenv("TELEGRAM_CHAT_ID", "")
    if cid: return int(cid)
    try:
        f = Path("/home/ubuntu/ob-bot/data/chat_id.txt")
        if f.exists(): return int(f.read_text().strip())
    except: pass
    return None

def _save_chat_id(chat_id):
    try:
        Path("/home/ubuntu/ob-bot/data").mkdir(exist_ok=True)
        Path("/home/ubuntu/ob-bot/data/chat_id.txt").write_text(str(chat_id))
    except: pass
'''

MORNING_SCAN_JOB = '''
async def job_morning_scan(context):
    """10:00 AM EST — Morning scan: top 5 calls + puts, log best pick."""
    import traceback
    chat_id = _get_chat_id()
    if not chat_id:
        log.warning("Morning scan: no chat_id saved. Send any command to bot first.")
        return
    try:
        await context.bot.send_message(chat_id,
            "☀️ <b>MORNING SCAN — 10:00 AM EST</b>\\n"
            "Running full market scan... (this takes ~2 minutes)",
            parse_mode="HTML")

        from scanner import run_scan_dte_profiles
        from paper_trader import init_db, log_trade
        from grok_brain import generate_ob_commentary, format_scan_result

        r      = run_scan_dte_profiles()
        regime = r["regime"]
        picks  = r["dte_picks"]
        top_c  = r["top_calls"][:5]
        top_p  = r["top_puts"][:5]
        init_db()

        # ── Regime header ───────────────────────────────────────────────────
        commentary = generate_ob_commentary(top_c, top_p, regime)
        vix_bar = "🔴" if regime["vix"]>25 else ("🟡" if regime["vix"]>18 else "🟢")
        header = (
            f"📊 <b>Market Regime: {regime['regime']}</b>  {vix_bar} VIX {regime['vix']:.1f}\\n"
            f"{commentary}\\n"
        )
        await context.bot.send_message(chat_id, header, parse_mode="HTML")

        # ── Top 5 CALLS ─────────────────────────────────────────────────────
        calls_lines = ["🚀 <b>TOP 5 CALLS — Auto-logged for tracking</b>\\n"]
        for i, p in enumerate(top_c, 1):
            e = p.get("entry_call", {})
            src = "📍" if e.get("price_source") == "market" else "~"
            bid_ask = f" (bid ${e.get('bid',0):.2f}/ask ${e.get('ask',0):.2f})" if e.get("bid") else ""
            calls_lines.append(
                f"<b>#{i} {p['symbol']}</b>  score={p['call_score']}/100  ${p['price']:.2f}\\n"
                f"   Strike ${e.get('strike','?')} exp {e.get('expiry','?')}  "
                f"{src}${e.get('est_option_price','?')}/contract{bid_ask}\\n"
                f"   Stop ${e.get('stop_loss_option','?')} → Target ${e.get('target_option','?')}\\n"
                f"   {p.get('call_reason','')[:80]}"
            )
            # Auto-log each of top 5 calls
            if p['call_score'] >= 40:  # lower threshold since top 5 are pre-filtered
                try:
                    log_trade(p, "CALL", session="morning_auto", dte_profile="TOP_5_CALLS",
                              regime=regime.get("regime","NORMAL"), recommendation_source="top_call", recommendation_rank=i)
                    calls_lines[-1] += "\\n   ✍️ <i>logged #" + str(i) + "</i>"
                except Exception as le:
                    log.warning(f"Auto-log top call #{i}: {le}")
        await context.bot.send_message(chat_id, "\\n".join(calls_lines), parse_mode="HTML")

        # ── Top 5 PUTS ──────────────────────────────────────────────────────
        puts_lines = ["💥 <b>TOP 5 PUTS — Auto-logged for tracking</b>\\n"]
        for i, p in enumerate(top_p, 1):
            e = p.get("entry_put", {})
            src = "📍" if e.get("price_source") == "market" else "~"
            bid_ask = f" (bid ${e.get('bid',0):.2f}/ask ${e.get('ask',0):.2f})" if e.get("bid") else ""
            puts_lines.append(
                f"<b>#{i} {p['symbol']}</b>  score={p['put_score']}/100  ${p['price']:.2f}\\n"
                f"   Strike ${e.get('strike','?')} exp {e.get('expiry','?')}  "
                f"{src}${e.get('est_option_price','?')}/contract{bid_ask}\\n"
                f"   Stop ${e.get('stop_loss_option','?')} → Target ${e.get('target_option','?')}\\n"
                f"   {p.get('put_reason','')[:80]}"
            )
            # Auto-log each of top 5 puts
            if p['put_score'] >= 40:  # lower threshold since top 5 are pre-filtered
                try:
                    log_trade(p, "PUT", session="morning_auto", dte_profile="TOP_5_PUTS",
                              regime=regime.get("regime","NORMAL"), recommendation_source="top_put", recommendation_rank=i)
                    puts_lines[-1] += "\\n   ✍️ <i>logged #" + str(i) + "</i>"
                except Exception as le:
                    log.warning(f"Auto-log top put #{i}: {le}")
        await context.bot.send_message(chat_id, "\\n".join(puts_lines), parse_mode="HTML")

        # ── DTE Profile picks (LONG options) ────────────────────────────────
        dte_lines = ["🎯 <b>BEST PICK PER DTE TIER</b>\\n"]
        for tier in ["0DTE","7DTE","21DTE","30DTE","60DTE"]:
            p = picks.get(tier)
            if not p:
                dte_lines.append(f"  {tier}: No qualifying setup today")
                continue
            dk = "call" if p.get("direction","CALL")=="CALL" else "put"
            e  = p.get(f"entry_{dk}", {})
            sc = p.get(f"{dk}_score", 0)
            src = "📍" if e.get("price_source") == "market" else "~"
            dte_lines.append(
                f"  <b>{tier}</b> NAKED {p.get('direction')}: {p['symbol']} {sc}/100\\n"
                f"     Strike ${e.get('strike','?')} exp {e.get('expiry','?')} {src}${e.get('est_option_price','?')}\\n"
            )

            # Auto-log ALL DTE picks as paper trades for performance tracking
            if sc >= 50:
                try:
                    log_trade(p, p.get("direction","CALL"),
                              session="morning_auto",
                              dte_profile=tier, regime=regime.get("regime","NORMAL"))
                    dte_lines[-1] += "     ✍️ <i>logged naked</i>\\n"
                except Exception as le:
                    log.warning(f"Auto-log {tier}: {le}")

            # Also show credit spread as alternative (if score >= 50)
            sp = p.get("spread")
            if sp and sc >= 50:
                sp_credit = sp.get("credit", 0)
                sp_max_loss = sp.get("max_loss", 0)
                dte_lines.append(
                    f"  <b>ALT — {tier} SPREAD:</b> {p['symbol']} {sp.get('spread_type','')}\\n"
                    f"     Sell ${sp.get('short_strike','?')} / Buy ${sp.get('long_strike','?')}\\n"
                    f"     Credit ${sp_credit}/share | Max Loss ${sp_max_loss} | TP50% ${sp.get('take_profit_50','?')}/TP100% ${sp.get('take_profit_100','?')}\\n"
                )
                # Also log the spread alternative for tracking
                try:
                    log_trade(p, p.get("direction","CALL"),
                              session="morning_auto_spread",
                              dte_profile=tier, regime=regime.get("regime","NORMAL"),
                              spread_data=sp)
                    dte_lines[-1] += "     ✍️ <i>logged spread</i>\\n"
                except Exception as le:
                    log.warning(f"Auto-log spread {tier}: {le}")

        await context.bot.send_message(chat_id, "\\n".join(dte_lines), parse_mode="HTML")

    except Exception as e:
        log.error(f"job_morning_scan: {e}\\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ Morning scan error: {e}")
'''

MIDDAY_REVIEW_JOB = '''
async def job_midday_review(context):
    """12:30 PM EST — Real-time P&L guidance: distance to stops/targets, recommendations."""
    import traceback
    chat_id = _get_chat_id()
    if not chat_id: return
    try:
        await context.bot.send_message(chat_id,
            "🕧 <b>MIDDAY REVIEW — 12:30 PM EST</b>\\nReal-time P&L guidance & trade management", parse_mode="HTML")

        from paper_trader import get_open_trades_with_pnl, check_open_trades, get_todays_trades
        from scanner import get_market_regime
        import requests, os, yfinance as yf
        from datetime import datetime

        regime = get_market_regime()
        closed = check_open_trades(regime.get("regime","NORMAL"))
        all_today = get_todays_trades()  # All trades from today regardless of status
        trades = get_open_trades_with_pnl()  # Only OPEN trades for live tracking

        if not all_today:
            await context.bot.send_message(chat_id,
                "📭 No trades from this morning yet. Wait for /ob morning scan.")
            return

        # ═══ Group today's trades by recommendation source ═══
        by_source = {}
        for t in all_today:
            source = t.get("recommendation_source", "manual")
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(t)

        lines = []

        # Show closed trades first
        if closed:
            lines.append(f"🔒 <b>Closed {len(closed)} trade(s) at midday:</b>")
            for c in closed:
                ico = "✅" if c["outcome"]=="WIN" else "❌"
                source = c.get("recommendation_source", "")
                source_str = f" ({source})" if source else ""
                lines.append(f"  {ico} {c['symbol']} {c['direction']} {c.get('dte_profile','')} {c['pnl_pct']:+.1f}% — {c['reason']}{source_str}")

        # ═══ Show all OPEN trades from today grouped by recommendation source ═══
        open_only = [t for t in all_today if t.get("status") == "OPEN"]
        if open_only:
            lines.append(f"\\n📊 <b>TODAY'S TRADES IN PLAY ({len(open_only)}) — Real-time P&L Guidance</b>\\n")

            # Sort by recommendation source for better organization
            source_order = ["top_call", "top_put", "dte_pick", "dte_spread", "manual"]
            for src in source_order:
                src_trades = [t for t in open_only if t.get("recommendation_source") == src]
                if not src_trades:
                    continue

                # Category header
                src_names = {"top_call": "🚀 Top 5 Calls", "top_put": "💥 Top 5 Puts",
                            "dte_pick": "🎯 DTE Picks", "dte_spread": "📐 Spreads", "manual": "📌 Manual"}
                lines.append(f"\\n<b>{src_names.get(src, src)}</b>")

                for t in src_trades:
                    symbol = t['symbol']
                    direction = t['direction']
                    entry_price = t['entry_price']
                    current_price = t.get('current_price', entry_price)
                    option_pnl_pct = t.get('option_pnl_pct', 0) or 0
                    option_pnl_dollar = t.get('option_pnl_dollar', 0) or 0
                    rank = t.get('recommendation_rank', 0)
                    rank_str = f" #{rank}" if rank else ""

                    # Get entry targets
                    entry_opt = t.get('entry_option_price') or 0
                    stop_opt = t.get('stop_loss_option') or 0
                    target_opt = t.get('target_option') or 0

                    # Current option price estimate
                    try:
                        tk = yf.Ticker(symbol)
                        hist = tk.history(period="1d", interval="5m")
                        if not hist.empty:
                            cur_price = float(hist["Close"].iloc[-1])
                    except:
                        cur_price = current_price

                    # ═══ Traffic light system ═══
                    if option_pnl_pct >= 70:
                        traffic = "🟢"  # Safe, consider taking profit
                        action = "✅ STRONG PROFIT — Consider taking 50%"
                    elif option_pnl_pct >= 30:
                        traffic = "🟢"  # Safe
                        action = "✅ HOLD — On track to target"
                    elif option_pnl_pct >= 0:
                        traffic = "🟡"  # Caution
                        action = "⏸️ HOLD — Wait for 30%+ profit"
                    elif option_pnl_pct >= -30:
                        traffic = "🟡"  # Caution, getting close to stop
                        action = "⚠️ CAUTION — Approaching stop loss"
                    else:
                        traffic = "🔴"  # Danger, cut loss
                        action = "🛑 CUT LOSS — Below -30%"

                    # ═══ Distance calculations ═══
                    if entry_opt > 0 and stop_opt > 0:
                        to_target = round((target_opt - entry_opt) / entry_opt * 100, 1)
                        to_stop = round((stop_opt - entry_opt) / entry_opt * 100, 1)
                        dist_remaining = round(option_pnl_pct - to_stop, 1)  # How far from stop
                    else:
                        to_target = round((cur_price * 1.05 - entry_price) / entry_price * 100, 1)
                        to_stop = round((entry_price * 0.93 - entry_price) / entry_price * 100, 1)
                        dist_remaining = round(option_pnl_pct - to_stop, 1)

                    lines.append(
                        f"{traffic} <b>{symbol}{rank_str}</b> {direction} {t.get('dte_profile', '')}\\n"
                        f"   Entry ${entry_price:.2f} → ${current_price:.2f} | Option P&L: {option_pnl_pct:+.1f}% (${option_pnl_dollar:+.2f})\\n"
                        f"   Target: ${target_opt} ({to_target:+.0f}%) | Stop: ${stop_opt} ({to_stop:+.0f}%) | Buffer: {dist_remaining:+.0f}%\\n"
                        f"   → {action}"
                    )

        await context.bot.send_message(chat_id, "\\n".join(lines), parse_mode="HTML")

        # ═══ Per-trade management guide ═══
        guide_lines = [
            "\\n📋 <b>TRADE MANAGEMENT GUIDE</b>\\n",
            "🟢 <b>Green (P&L +30% to +70%):</b> Stock moving right. Hold until +70% or stop hit.\\n",
            "🟡 <b>Yellow (-30% to +0%):</b> Trending against you. Exit if stops below 5% buffer.\\n",
            "🔴 <b>Red (P&L below -30%):</b> Theta + move against you. Cut immediately to prevent wipeout.\\n"
        ]
        await context.bot.send_message(chat_id, "".join(guide_lines), parse_mode="HTML")

        # Ask Grok for specific trade management
        groq_key = os.getenv("GROQ_API_KEY","")
        if groq_key and trades:
            trade_summary = "; ".join(
                f"{t['symbol']} {t['direction']} P&L {t.get('option_pnl_pct',0):+.0f}% (SL at {t.get('stop_loss_option',0)})"
                for t in trades[:5]
            )
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model":"llama-3.3-70b-versatile",
                          "messages":[
                              {"role":"system","content":"You are a risk-focused options trader. Max 3 sentences. Focus on PROTECTION first, profit second. Be specific about which positions to exit."},
                              {"role":"user","content":f"Midday risk check. Regime: {regime.get('regime')} VIX {regime.get('vix',0):.1f}.\\nOpen trades: {trade_summary}\\nWhich should I close for safety? Which can hold for target?"}
                          ],
                          "max_tokens":120},
                    timeout=15
                )
                if resp.status_code == 200:
                    advice = resp.json()["choices"][0]["message"]["content"].strip()
                    await context.bot.send_message(chat_id,
                        f"🤖 <b>AI Trade Management:</b>\\n{advice}", parse_mode="HTML")
            except: pass

    except Exception as e:
        log.error(f"job_midday_review: {e}\\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ Midday review error: {e}")
'''

EOD_REVIEW_JOB = '''
async def job_eod_review(context):
    """3:30 PM EST — Close trades, run learning cycle, detailed performance analysis."""
    import traceback
    chat_id = _get_chat_id()
    if not chat_id: return
    try:
        await context.bot.send_message(chat_id,
            "🌆 <b>EOD REVIEW — 3:30 PM EST</b>\\nRunning comprehensive end-of-day analysis...",
            parse_mode="HTML")

        from paper_trader import check_open_trades, get_performance_stats, get_lessons
        from learner import run_learning_cycle, get_ev_by_dte, get_signal_summary
        from scanner import get_market_regime
        import requests, os

        regime = get_market_regime()

        # Close remaining open trades at market close
        closed = check_open_trades(regime.get("regime","NORMAL"))

        # Run full learning cycle
        learning = run_learning_cycle()
        stats    = get_performance_stats()
        lessons  = get_lessons(10)
        dte_ev   = get_ev_by_dte()
        sig_acc  = get_signal_summary()

        # ────────────────────────────────────────────────────────────────────────
        # ── TODAY'S TRADE RESULTS (detailed) ──────────────────────────────────
        result_lines = ["📊 <b>TODAY'S TRADE RESULTS</b>\\n"]
        if closed:
            result_lines.append(f"Closed {len(closed)} trade(s) at end of day:\\n")
            for c in closed:
                ico = "✅" if c["outcome"]=="WIN" else "❌"
                result_lines.append(
                    f"  {ico} <b>{c['symbol']}</b> {c['direction']} {c['dte_profile']}\\n"
                    f"     {c['pnl_pct']:+.1f}% P&L ({c.get('reason','')}) | Days held: {c.get('days_held',0)}"
                )
        else:
            result_lines.append("  No trades closed today.")

        await context.bot.send_message(chat_id, "\\n".join(result_lines), parse_mode="HTML")

        # ────────────────────────────────────────────────────────────────────────
        # ── PERFORMANCE BY DTE TIER ──────────────────────────────────────────
        dte_lines = ["📈 <b>PERFORMANCE BY DTE TIER (All-Time)</b>\\n"]
        for tier in ["0DTE", "7DTE", "21DTE", "30DTE", "60DTE"]:
            s = dte_ev.get(tier, {})
            if s.get("trades", 0) > 0:
                dte_lines.append(
                    f"  <b>{tier}</b>: {s['trades']} trades | "
                    f"WR {s['win_rate']:.0f}% | Avg P&L {s['avg_pnl']:+.1f}% | EV {s['ev']:+.1f}%"
                )
        if len(dte_lines) == 1:
            dte_lines.append("  Not enough data yet.")

        await context.bot.send_message(chat_id, "\\n".join(dte_lines), parse_mode="HTML")

        # ────────────────────────────────────────────────────────────────────────
        # ── SIGNAL ACCURACY (what's working/failing) ──────────────────────────
        sig_lines = ["⚡ <b>SIGNAL ACCURACY (Which signals work best)</b>\\n"]
        for s in sig_acc[:5]:
            if s['total'] >= 3:
                trend = "📈" if s['win_rate'] > 60 else ("📉" if s['win_rate'] < 40 else "➡️")
                sig_lines.append(
                    f"  {trend} <b>{s['signal']}</b>: {s['win_rate']:.0f}% WR ({s['total']} trades)"
                )
        if len(sig_lines) == 1:
            sig_lines.append("  Not enough data yet (need 3+ trades per signal).")

        await context.bot.send_message(chat_id, "\\n".join(sig_lines), parse_mode="HTML")

        # ────────────────────────────────────────────────────────────────────────
        # ── LEARNING CYCLE ADJUSTMENTS ───────────────────────────────────────
        learn_lines = ["🧠 <b>LEARNING CYCLE — Weight & Threshold Adjustments</b>\\n"]
        if learning.get("adjustments"):
            for adj in learning["adjustments"]:
                learn_lines.append(f"  🔄 {adj}")
        else:
            learn_lines.append("  No significant adjustments this cycle.")

        await context.bot.send_message(chat_id, "\\n".join(learn_lines), parse_mode="HTML")

        # ────────────────────────────────────────────────────────────────────────
        # ── KEY LESSONS LEARNED ──────────────────────────────────────────────
        if lessons:
            less_lines = ["📚 <b>KEY LESSONS FROM CLOSED TRADES</b>\\n"]
            for l in lessons[:5]:
                ico = "✅" if l.get("outcome")=="WIN" else "❌"
                less_lines.append(
                    f"  {ico} <b>{l['symbol']}</b> ({l.get('dte_profile','')}): {(l.get('lesson') or '')[:100]}"
                )
            await context.bot.send_message(chat_id, "\\n".join(less_lines), parse_mode="HTML")

        # ────────────────────────────────────────────────────────────────────────
        # ── TOMORROW'S STRATEGY via Grok ─────────────────────────────────────
        groq_key = os.getenv("GROQ_API_KEY","")
        if groq_key:
            ev_summary = "; ".join(
                f"{k}: WR {v['win_rate']:.0f}% EV {v['ev']:+.1f}%"
                for k,v in dte_ev.items() if v.get("trades",0)>0
            ) or "no data yet"
            best_sig = sig_acc[0]['signal'] if sig_acc and sig_acc[0]['total'] >= 3 else "none"
            worst_sig = sig_acc[-1]['signal'] if sig_acc and sig_acc[-1]['total'] >= 3 else "none"

            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model":"llama-3.3-70b-versatile",
                          "messages":[
                              {"role":"system","content":"You are Marcus Reed, a seasoned options trader. Write a crisp 3-4 sentence strategy note for tomorrow. Be specific: which sectors, which DTE to focus on, what signals are working, what to avoid."},
                              {"role":"user","content":
                                  f"EOD analysis complete.\\n"
                                  f"TODAY: Closed {len(closed)} trades, Win rate: {stats['win_rate']:.0f}%\\n"
                                  f"DTE performance: {ev_summary}\\n"
                                  f"Best signal: {best_sig} | Worst: {worst_sig}\\n"
                                  f"Regime tomorrow: {regime.get('regime')} VIX {regime.get('vix',0):.1f}\\n"
                                  f"Write tomorrow's strategy focusing on best-performing DTE and signals."}
                          ],
                          "max_tokens":220},
                    timeout=15
                )
                if resp.status_code == 200:
                    strategy = resp.json()["choices"][0]["message"]["content"].strip()
                    await context.bot.send_message(chat_id,
                        f"🎯 <b>TOMORROW'S STRATEGY — Marcus Reed</b>\\n\\n{strategy}",
                        parse_mode="HTML")
            except: pass

        # Summary
        await context.bot.send_message(chat_id,
            f"✅ EOD review complete.\\n"
            f"📊 All-time: {stats['total_trades']} trades | {stats['win_rate']:.0f}% WR | "
            f"${stats['total_pnl_dollar']:+.2f} total P&L\\n"
            f"🔔 Bot will resume scanning tomorrow at 10:00 AM EST.",
            parse_mode="HTML")

    except Exception as e:
        log.error(f"job_eod_review: {e}\\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ EOD review error: {e}")
'''

STATUS_CMD = '''
async def cmd_status(update, context):
    """Show bot status, next scheduled runs, and open position count."""
    _save_chat_id(update.effective_chat.id)
    from paper_trader import get_performance_stats
    from scanner import get_market_regime
    import datetime
    from zoneinfo import ZoneInfo
    EST = ZoneInfo("America/New_York")
    now_est = datetime.datetime.now(EST)
    stats  = get_performance_stats()
    regime = get_market_regime()
    # Next scheduled times
    def next_run(hour, minute):
        t = now_est.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= now_est: t += datetime.timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M EST")
    lines = [
        "🤖 <b>OB Bot Status</b>\\n",
        f"🕐 Now: {now_est.strftime('%H:%M EST')}",
        f"📊 Regime: {regime.get('regime')} (VIX {regime.get('vix',0):.1f})",
        f"",
        f"⏰ <b>Next scheduled runs:</b>",
        f"  🌅 Morning scan:   {next_run(10, 0)}",
        f"  🕧 Midday review:  {next_run(12, 30)}",
        f"  🌆 EOD review:     {next_run(15, 30)}",
        f"",
        f"📈 <b>Performance:</b>",
        f"  Trades: {stats['total_trades']}  |  Win rate: {stats['win_rate']:.0f}%",
        f"  EV: {stats['expectancy']:+.1f}%  |  Open: {stats['open_trades']}",
        f"  Total P&L: ${stats['total_pnl_dollar']:+.2f}",
    ]
    await update.message.reply_text("\\n".join(lines), parse_mode="HTML")
'''

CHAT_ID_CAPTURE = '''
async def _auto_save_chat_id(update, context):
    """Middleware: save chat_id whenever user sends a command."""
    if update and update.effective_chat:
        _save_chat_id(update.effective_chat.id)
'''

SCHEDULER_SETUP = '''
    # ── Scheduled jobs (EST timezone) ──────────────────────────────────────
    import datetime
    from zoneinfo import ZoneInfo
    EST = ZoneInfo("America/New_York")
    jq = app.job_queue
    if jq:
        # Monday-Friday only (weekday=0-4)
        jq.run_daily(job_morning_scan,  datetime.time(10,  0, tzinfo=EST), days=(0,1,2,3,4))
        jq.run_daily(job_midday_review, datetime.time(12, 30, tzinfo=EST), days=(0,1,2,3,4))
        jq.run_daily(job_eod_review,    datetime.time(15, 30, tzinfo=EST), days=(0,1,2,3,4))
        log.info("Scheduled jobs registered: 10:00, 12:30, 15:30 EST (Mon-Fri)")
    else:
        log.warning("JobQueue not available — install python-telegram-bot[job-queue]")
'''

# ════════════════════════════════════════════════════════════════════════════
# Patch helpers
# ════════════════════════════════════════════════════════════════════════════
def _insert_before(src, anchor, new_code):
    idx = src.find(anchor)
    if idx == -1: return src, False
    return src[:idx] + new_code + "\n" + src[idx:], True

def _insert_after_first_line(src, anchor, new_code):
    idx = src.find(anchor)
    if idx == -1: return src, False
    end = src.find("\n", idx) + 1
    return src[:end] + new_code + "\n" + src[end:], True

def patch():
    if not BOT.exists():
        print(f"ERROR: {BOT} not found"); import sys; sys.exit(1)
    src = BOT.read_text()

    # 1. Add imports
    if 'ZoneInfo' not in src:
        src, ok = _insert_after_first_line(src, "import os", IMPORTS_BLOCK)
        print("✅ Added EST timezone imports" if ok else "⚠️  Add EST imports manually")
    else:
        print("ℹ️  Timezone imports already present")

    # 2. Add chat_id helpers
    if '_get_chat_id' not in src:
        anchor = "\nasync def cmd_ob("
        if anchor not in src: anchor = "\nasync def cmd_start("
        if anchor not in src: anchor = "\nasync def cmd_picks("
        src, ok = _insert_before(src, anchor, CHAT_ID_HELPER)
        print("✅ Added chat_id helpers" if ok else "⚠️  Add chat_id helpers manually")
    else:
        print("ℹ️  chat_id helpers already present")

    # 3. Add morning scan job
    if 'job_morning_scan' not in src:
        anchor = "\nasync def cmd_picks("
        if anchor not in src: anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, MORNING_SCAN_JOB)
        print("✅ Added job_morning_scan()" if ok else "⚠️  Add morning scan manually")
    else:
        print("ℹ️  job_morning_scan already present")

    # 4. Add midday review job
    if 'job_midday_review' not in src:
        anchor = "\nasync def job_morning_scan"
        src, ok = _insert_before(src, anchor, "")  # find position
        anchor = "\nasync def cmd_picks("
        if anchor not in src: anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, MIDDAY_REVIEW_JOB)
        print("✅ Added job_midday_review()" if ok else "⚠️  Add midday review manually")
    else:
        print("ℹ️  job_midday_review already present")

    # 5. Add EOD review job
    if 'job_eod_review' not in src:
        anchor = "\nasync def cmd_picks("
        if anchor not in src: anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, EOD_REVIEW_JOB)
        print("✅ Added job_eod_review()" if ok else "⚠️  Add EOD review manually")
    else:
        print("ℹ️  job_eod_review already present")

    # 6. Add /status command
    if 'cmd_status' not in src:
        anchor = "\nasync def cmd_picks("
        if anchor not in src: anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, STATUS_CMD)
        print("✅ Added cmd_status()" if ok else "⚠️  Add cmd_status manually")
    else:
        print("ℹ️  cmd_status already present")

    # 7. Register /status handler
    if 'CommandHandler("status"' not in src:
        for anchor in ['CommandHandler("picks"', 'CommandHandler("ob"', 'app.add_handler(']:
            idx = src.find(anchor)
            if idx != -1:
                end = src.find("\n", idx) + 1
                src = src[:end] + '    app.add_handler(CommandHandler("status", cmd_status))\n' + src[end:]
                print("✅ Registered /status handler")
                break

    # 8. Add auto chat_id capture to existing handlers (patch cmd_ob or cmd_start)
    if '_save_chat_id' not in src:
        # Add save call at start of cmd_ob
        for fn in ['async def cmd_ob(update, context):', 'async def cmd_start(update, context):']:
            idx = src.find(fn)
            if idx != -1:
                body_start = src.find('\n', idx) + 1
                src = src[:body_start] + '    _save_chat_id(update.effective_chat.id)\n' + src[body_start:]
                print(f"✅ Auto-save chat_id wired into {fn.split('(')[0].split()[-1]}")
                break

    # 9. Inject scheduler setup before app.run_polling()
    if 'job_morning_scan' in src and 'run_daily(job_morning_scan' not in src:
        for anchor in ['app.run_polling(', 'application.run_polling(']:
            if anchor in src:
                src, ok = _insert_before(src, anchor, SCHEDULER_SETUP)
                print("✅ Wired scheduled jobs into main()" if ok else "⚠️  Wire scheduler manually")
                break
        else:
            print("⚠️  Could not find run_polling() — add scheduler setup before it manually")
    else:
        print("ℹ️  Scheduler already wired" if 'run_daily(job_morning_scan' in src else "ℹ️  Scheduler not wired (job code missing)")

    BOT.write_text(src)
    print("\n✅ bot.py patched (patch3) successfully.")

if __name__ == "__main__":
    patch()
