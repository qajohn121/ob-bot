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
        calls_lines = ["🚀 <b>TOP 5 CALLS</b>\\n"]
        for i, p in enumerate(top_c, 1):
            e = p.get("entry_call", {})
            src = "📍" if e.get("price_source") == "market" else "~"
            bid_ask = f" (bid ${e.get('bid',0):.2f}/ask ${e.get('ask',0):.2f})" if e.get("bid") else ""
            calls_lines.append(
                f"<b>#{i} {p['symbol']}</b>  score={p['call_score']}  ${p['price']:.2f}\\n"
                f"   Strike ${e.get('strike','?')} exp {e.get('expiry','?')}  "
                f"{src}${e.get('est_option_price','?')}/contract{bid_ask}\\n"
                f"   Stop ${e.get('stop_loss_option','?')} → Target ${e.get('target_option','?')}\\n"
                f"   {p.get('call_reason','')[:80]}"
            )
        await context.bot.send_message(chat_id, "\\n".join(calls_lines), parse_mode="HTML")

        # ── Top 5 PUTS ──────────────────────────────────────────────────────
        puts_lines = ["💥 <b>TOP 5 PUTS</b>\\n"]
        for i, p in enumerate(top_p, 1):
            e = p.get("entry_put", {})
            src = "📍" if e.get("price_source") == "market" else "~"
            bid_ask = f" (bid ${e.get('bid',0):.2f}/ask ${e.get('ask',0):.2f})" if e.get("bid") else ""
            puts_lines.append(
                f"<b>#{i} {p['symbol']}</b>  score={p['put_score']}  ${p['price']:.2f}\\n"
                f"   Strike ${e.get('strike','?')} exp {e.get('expiry','?')}  "
                f"{src}${e.get('est_option_price','?')}/contract{bid_ask}\\n"
                f"   Stop ${e.get('stop_loss_option','?')} → Target ${e.get('target_option','?')}\\n"
                f"   {p.get('put_reason','')[:80]}"
            )
        await context.bot.send_message(chat_id, "\\n".join(puts_lines), parse_mode="HTML")

        # ── DTE Profile picks ────────────────────────────────────────────────
        dte_lines = ["🎯 <b>BEST PICK PER DTE TIER</b>\\n"]
        logged = 0
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
                f"  <b>{tier}</b>: {p['symbol']} [{p.get('direction')}] {sc}/100\\n"
                f"     ${e.get('strike','?')} exp {e.get('expiry','?')} {src}${e.get('est_option_price','?')}"
            )
            # Auto-log top DTE pick as paper trade (21DTE or 30DTE preferred)
            if tier in ("21DTE","30DTE") and logged == 0 and sc >= 50:
                try:
                    log_trade(p, p.get("direction","CALL"),
                              session="morning_auto",
                              dte_profile=tier, regime=regime.get("regime","NORMAL"))
                    dte_lines[-1] += "  ✍️ <i>logged</i>"
                    logged += 1
                except Exception as le:
                    log.warning(f"Auto-log failed: {le}")

        await context.bot.send_message(chat_id, "\\n".join(dte_lines), parse_mode="HTML")

    except Exception as e:
        log.error(f"job_morning_scan: {e}\\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ Morning scan error: {e}")
'''

MIDDAY_REVIEW_JOB = '''
async def job_midday_review(context):
    """12:30 PM EST — Review open trades, P&L update, hold/adjust call."""
    import traceback
    chat_id = _get_chat_id()
    if not chat_id: return
    try:
        await context.bot.send_message(chat_id,
            "🕧 <b>MIDDAY REVIEW — 12:30 PM EST</b>", parse_mode="HTML")

        from paper_trader import get_open_trades_with_pnl, check_open_trades
        from scanner import get_market_regime
        import requests, os

        regime = get_market_regime()
        closed = check_open_trades(regime.get("regime","NORMAL"))
        trades = get_open_trades_with_pnl()

        if not trades and not closed:
            await context.bot.send_message(chat_id,
                "📭 No open positions to review. Use /ob to run a scan.")
            return

        lines = []
        if closed:
            lines.append(f"🔒 <b>Closed {len(closed)} trade(s) at midday:</b>")
            for c in closed:
                ico = "✅" if c["outcome"]=="WIN" else "❌"
                lines.append(f"  {ico} {c['symbol']} {c['direction']} {c['pnl_pct']:+.1f}% — {c['reason']}")

        if trades:
            lines.append(f"\\n📂 <b>Open positions ({len(trades)}):</b>")
            for t in trades:
                pnl = t.get("option_pnl_pct", 0) or 0
                ico = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➡️")
                lines.append(
                    f"  {ico} <b>{t['symbol']}</b> {t['direction']} {t.get('dte_profile','')}\\n"
                    f"     Entry ${t['entry_price']:.2f} → Now ${t.get('current_price', t['entry_price']):.2f} "
                    f"| Option P&L: {pnl:+.1f}%"
                )

        await context.bot.send_message(chat_id, "\\n".join(lines), parse_mode="HTML")

        # Ask Grok for hold/adjust recommendation
        groq_key = os.getenv("GROQ_API_KEY","")
        if groq_key and trades:
            trade_summary = "; ".join(
                f"{t['symbol']} {t['direction']} pnl={t.get('option_pnl_pct',0):+.0f}%"
                for t in trades[:5]
            )
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model":"llama-3.3-70b-versatile",
                          "messages":[
                              {"role":"system","content":"You are a decisive options trader. Max 3 sentences. Be specific."},
                              {"role":"user","content":f"Midday check. Regime: {regime.get('regime')} VIX {regime.get('vix',0):.1f}. Open trades: {trade_summary}. Should I hold, take partial profits, or cut losses? Give specific guidance."}
                          ],
                          "max_tokens":120},
                    timeout=15
                )
                if resp.status_code == 200:
                    advice = resp.json()["choices"][0]["message"]["content"].strip()
                    await context.bot.send_message(chat_id,
                        f"🤖 <b>AI Midday Guidance:</b>\\n{advice}", parse_mode="HTML")
            except: pass

    except Exception as e:
        log.error(f"job_midday_review: {e}\\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ Midday review error: {e}")
'''

EOD_REVIEW_JOB = '''
async def job_eod_review(context):
    """3:30 PM EST — Close trades, run learning, send tomorrow's strategy."""
    import traceback
    chat_id = _get_chat_id()
    if not chat_id: return
    try:
        await context.bot.send_message(chat_id,
            "🌆 <b>EOD REVIEW — 3:30 PM EST</b>\\nRunning end-of-day analysis...",
            parse_mode="HTML")

        from paper_trader import check_open_trades, get_performance_stats, get_lessons
        from learner import run_learning_cycle, get_ev_by_dte
        from scanner import get_market_regime
        import requests, os

        regime = get_market_regime()

        # Close remaining open trades at market close
        closed = check_open_trades(regime.get("regime","NORMAL"))

        # Run full learning cycle
        learning = run_learning_cycle()
        stats    = get_performance_stats()
        lessons  = get_lessons(5)

        # ── Today's results ─────────────────────────────────────────────────
        result_lines = ["📊 <b>TODAY'S RESULTS</b>\\n"]
        if closed:
            for c in closed:
                ico = "✅" if c["outcome"]=="WIN" else "❌"
                result_lines.append(f"  {ico} {c['symbol']} {c['direction']} {c['dte_profile']} {c['pnl_pct']:+.1f}%")
        else:
            result_lines.append("  No trades closed today.")

        result_lines.append(
            f"\\n📈 <b>Overall stats:</b>\\n"
            f"  Win rate: {stats['win_rate']:.0f}%  |  "
            f"EV: {stats['expectancy']:+.1f}%  |  "
            f"Total P&L: ${stats['total_pnl_dollar']:+.2f}"
        )

        # ── Learning update ─────────────────────────────────────────────────
        if learning.get("adjustments"):
            result_lines.append(f"\\n🧠 <b>Learning updates:</b>")
            for adj in learning["adjustments"][:3]:
                result_lines.append(f"  • {adj}")

        # ── Recent lessons ──────────────────────────────────────────────────
        if lessons:
            result_lines.append(f"\\n📚 <b>Key lessons:</b>")
            for l in lessons[:3]:
                ico = "✅" if l.get("outcome")=="WIN" else "❌"
                result_lines.append(f"  {ico} {l['symbol']}: {(l.get('lesson') or '')[:80]}")

        await context.bot.send_message(chat_id, "\\n".join(result_lines), parse_mode="HTML")

        # ── Tomorrow's strategy via Grok ────────────────────────────────────
        groq_key = os.getenv("GROQ_API_KEY","")
        if groq_key:
            dte_ev = get_ev_by_dte()
            ev_summary = "; ".join(
                f"{k}: WR={v['win_rate']:.0f}% EV={v['ev']:+.1f}%"
                for k,v in dte_ev.items() if v.get("trades",0)>0
            ) or "no data yet"
            lessons_txt = " | ".join(
                (l.get("lesson") or "")[:60] for l in lessons[:3]
            ) or "no lessons yet"
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={"model":"llama-3.3-70b-versatile",
                          "messages":[
                              {"role":"system","content":"You are Marcus Reed, a seasoned options trader. Write a crisp 3-4 sentence strategy note for tomorrow's session. Be specific: which sectors, which DTE, what to watch for."},
                              {"role":"user","content":
                                  f"EOD debrief. Regime today: {regime.get('regime')} VIX {regime.get('vix',0):.1f}.\\n"
                                  f"Win rate: {stats['win_rate']:.0f}% | EV: {stats['expectancy']:+.1f}%\\n"
                                  f"DTE performance: {ev_summary}\\n"
                                  f"Key lessons: {lessons_txt}\\n"
                                  f"Write tomorrow's trading strategy."}
                          ],
                          "max_tokens":200},
                    timeout=15
                )
                if resp.status_code == 200:
                    strategy = resp.json()["choices"][0]["message"]["content"].strip()
                    await context.bot.send_message(chat_id,
                        f"🎯 <b>TOMORROW'S STRATEGY — Marcus Reed</b>\\n\\n{strategy}",
                        parse_mode="HTML")
            except: pass

        await context.bot.send_message(chat_id,
            "✅ EOD review complete. Bot will scan again tomorrow at 10:00 AM EST.",
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
