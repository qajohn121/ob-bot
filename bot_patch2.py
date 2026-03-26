#!/usr/bin/env python3
"""
bot_patch2.py — second patch pass on bot.py.
Adds:
  1. /picks  Telegram command — 5 DTE-profile picks (0DTE/7DTE/21DTE/30DTE/60DTE)
  2. /lessons Telegram command — last 10 autopsy lessons
  3. Lessons Board section in the FastAPI HTML dashboard
  4. Wires run_learning_cycle() into the nightly learning cron
Run: python bot_patch2.py
"""
import re, sys, textwrap
from pathlib import Path

BOT = Path("/home/ubuntu/ob-bot/bot.py")


# ══════════════════════════════════════════════════════════════════════════════
# New /picks command handler
# ══════════════════════════════════════════════════════════════════════════════
PICKS_HANDLER = '''
async def cmd_picks(update, context):
    """Return one best pick per DTE tier."""
    try:
        from scanner import run_scan_dte_profiles
        r = run_scan_dte_profiles()
        regime_info = r.get("regime", {})
        profiles    = r.get("dte_picks", {})
        regime = regime_info.get("regime", "NORMAL")
        vix    = regime_info.get("vix", 0)
        lines  = [f"📊 <b>DTE Profile Picks</b>  |  Regime: <b>{regime}</b>  VIX: {vix:.1f}\\n"]
        emojis = {"0DTE": "⚡", "7DTE": "🎯", "21DTE": "📅", "30DTE": "📆", "60DTE": "🗓"}
        for tier in ["0DTE", "7DTE", "21DTE", "30DTE", "60DTE"]:
            pick = profiles.get(tier)
            em   = emojis.get(tier, "•")
            if not pick:
                lines.append(f"{em} <b>{tier}</b>: No qualifying setup today")
                continue
            direction = pick.get("direction", "CALL")
            dk  = "call" if direction == "CALL" else "put"
            sc  = pick.get(f"{dk}_score", 0)
            entry = pick.get(f"entry_{dk}", {})
            strike  = entry.get("strike", "?")
            expiry  = entry.get("expiry", "?")
            opt_px  = entry.get("est_option_price", "?")
            stop    = entry.get("stop_loss_option", "?")
            tgt     = entry.get("target_option", "?")
            cv  = "🔥HIGH" if sc >= 75 else ("✅MED" if sc >= 55 else "⚠️LOW")
            lines.append(
                f"{em} <b>{tier} — {pick['symbol']} [{direction}]</b> {sc}/100 {cv}\\n"
                f"   📋 ${strike} exp {expiry} (~${opt_px})\\n"
                f"   🛑 Stop ${stop}  🎯 Tgt ${tgt}"
            )
        await update.message.reply_text("\\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ /picks error: {e}")
'''

# ══════════════════════════════════════════════════════════════════════════════
# New /lessons command handler
# ══════════════════════════════════════════════════════════════════════════════
LESSONS_HANDLER = '''
async def cmd_lessons(update, context):
    """Show last 10 autopsy lessons."""
    try:
        from paper_trader import get_lessons, get_signal_accuracy
        from learner import get_ev_by_dte
        lessons = get_lessons(10)
        sig_acc = get_signal_accuracy()
        dte_ev  = get_ev_by_dte()
        lines   = ["📚 <b>Lessons Board</b>\\n"]
        if not lessons:
            lines.append("No closed trades yet — lessons appear after first close.")
        for l in lessons[:8]:
            ico = "✅" if l.get("outcome") == "WIN" else "❌"
            pnl = l.get("pnl_pct", 0) or 0
            lines.append(
                f"{ico} <b>{l['symbol']} {l.get('direction','')} {l.get('dte_profile','')}</b> "
                f"{pnl:+.1f}%\\n   {l.get('lesson','')[:100]}"
            )
        lines.append("\\n📊 <b>EV by DTE tier:</b>")
        for tier in ["0DTE", "7DTE", "21DTE", "30DTE", "60DTE"]:
            s = dte_ev.get(tier, {})
            if s.get("trades", 0) > 0:
                lines.append(f"  {tier}: {s['trades']} trades | WR {s['win_rate']:.0f}% | EV {s['ev']:+.1f}%")
        lines.append("\\n⚡ <b>Signal accuracy:</b>")
        for s in sig_acc[:4]:
            lines.append(f"  {s['signal']:12s}: {s['win_rate']:.0f}% ({s['total']} trades)")
        await update.message.reply_text("\\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ /lessons error: {e}")
'''

# ══════════════════════════════════════════════════════════════════════════════
# Lessons Board HTML section (injected before closing </body> in _build_html)
# ══════════════════════════════════════════════════════════════════════════════
LESSONS_HTML_SECTION = '''
    <!-- ── Lessons Board ─────────────────────────────────── -->
    <div style="margin-top:30px">
    <h2 style="color:#fa0;font-size:16px">📚 LESSONS BOARD</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">

    <!-- Signal Accuracy -->
    <div style="background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:14px">
    <h3 style="color:#7cf;font-size:13px;margin:0 0 10px">⚡ Signal Accuracy</h3>
    <table style="width:100%;border-collapse:collapse;font-size:11px">
    <tr style="color:#888"><th align="left">Signal</th><th>Trades</th><th>Win%</th></tr>
    {signal_rows}
    </table>
    </div>

    <!-- EV by DTE -->
    <div style="background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:14px">
    <h3 style="color:#7cf;font-size:13px;margin:0 0 10px">📊 EV by DTE Tier</h3>
    <table style="width:100%;border-collapse:collapse;font-size:11px">
    <tr style="color:#888"><th align="left">Tier</th><th>Trades</th><th>WR%</th><th>EV</th></tr>
    {dte_ev_rows}
    </table>
    </div>

    </div>

    <!-- Recent Lessons -->
    <div style="margin-top:14px;background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:14px">
    <h3 style="color:#7cf;font-size:13px;margin:0 0 10px">🧠 Recent Trade Autopsies</h3>
    <table style="width:100%;border-collapse:collapse;font-size:11px">
    <tr style="color:#888">
      <th align="left">#</th><th align="left">Symbol</th><th>DTE</th>
      <th>P&amp;L</th><th align="left">Lesson</th><th>Signal</th>
    </tr>
    {lesson_rows}
    </table>
    </div>
    </div>
'''


# ══════════════════════════════════════════════════════════════════════════════
# Dynamic row builders — injected into _build_html alongside other data
# ══════════════════════════════════════════════════════════════════════════════
BUILD_LESSONS_ROWS = '''
    # --- Lessons board data ---
    try:
        from paper_trader import get_lessons, get_signal_accuracy
        from learner import get_ev_by_dte
        _lessons    = get_lessons(15)
        _sig_acc    = get_signal_accuracy()
        _dte_ev     = get_ev_by_dte()
    except Exception:
        _lessons = []; _sig_acc = []; _dte_ev = {}

    signal_rows = ""
    for s in _sig_acc[:6]:
        wr = s.get("win_rate", 0)
        clr = "#0f0" if wr >= 55 else ("#fa0" if wr >= 40 else "#f44")
        signal_rows += (f"<tr><td style=\\'color:#ccc\\'>{s[\\'signal\\']}</td>"
                        f"<td align=\\'center\\'>{s[\\'total\\']}</td>"
                        f"<td align=\\'center\\'style=\\'color:{clr}\\'>{wr:.0f}%</td></tr>")
    if not signal_rows:
        signal_rows = "<tr><td colspan=\\'3\\' style=\\'color:#555\\'>No data yet</td></tr>"

    dte_ev_rows = ""
    for _tier in ["0DTE","7DTE","21DTE","30DTE","60DTE"]:
        _s = _dte_ev.get(_tier, {})
        if not _s.get("trades"): continue
        _ev = _s.get("ev", 0)
        _clr = "#0f0" if _ev > 0 else "#f44"
        dte_ev_rows += (f"<tr><td style=\\'color:#ccc\\'>{_tier}</td>"
                        f"<td align=\\'center\\'>{_s[\\'trades\\']}</td>"
                        f"<td align=\\'center\\'>{_s[\\'win_rate\\']:.0f}%</td>"
                        f"<td align=\\'center\\'style=\\'color:{_clr}\\'>{_ev:+.1f}%</td></tr>")
    if not dte_ev_rows:
        dte_ev_rows = "<tr><td colspan=\\'4\\' style=\\'color:#555\\'>No data yet</td></tr>"

    lesson_rows = ""
    for _i, _l in enumerate(_lessons[:12], 1):
        _ico = "✅" if _l.get("outcome") == "WIN" else "❌"
        _pnl = _l.get("pnl_pct") or 0
        _pnl_clr = "#0f0" if _pnl > 0 else "#f44"
        _txt = (_l.get("lesson") or "")[:80]
        _sig = _l.get("failed_signal") or "—"
        lesson_rows += (f"<tr>"
                        f"<td>{_ico}{_i}</td>"
                        f"<td><b>{_l[\\'symbol\\']}</b> {_l.get(\\'direction\\',\\'\\')}</td>"
                        f"<td style=\\'color:#888\\'>{_l.get(\\'dte_profile\\',\\'\\')}</td>"
                        f"<td style=\\'color:{_pnl_clr}\\'>{_pnl:+.1f}%</td>"
                        f"<td style=\\'font-size:10px;color:#aaa\\'>{_txt}</td>"
                        f"<td style=\\'color:#fa0;font-size:10px\\'>{_sig}</td>"
                        f"</tr>")
    if not lesson_rows:
        lesson_rows = "<tr><td colspan=\\'6\\' style=\\'color:#555\\'>No closed trades yet</td></tr>"
'''


# ══════════════════════════════════════════════════════════════════════════════
# Patch helpers
# ══════════════════════════════════════════════════════════════════════════════
def _insert_after(src, anchor, new_code):
    idx = src.find(anchor)
    if idx == -1:
        return src, False
    insert_at = idx + len(anchor)
    return src[:insert_at] + "\n" + new_code + src[insert_at:], True


def _insert_before(src, anchor, new_code):
    idx = src.find(anchor)
    if idx == -1:
        return src, False
    return src[:idx] + new_code + "\n" + src[idx:], True


def patch():
    if not BOT.exists():
        print(f"ERROR: {BOT} not found"); sys.exit(1)
    src = BOT.read_text()

    # ── 1. Add /picks handler ─────────────────────────────────────────────────
    # Insert before the main() / application builder section
    if "async def cmd_picks" not in src:
        anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, PICKS_HANDLER)
        print("✅ Added cmd_picks()" if ok else "⚠️  Could not inject cmd_picks — add manually")
    else:
        print("ℹ️  cmd_picks already present")

    # ── 2. Add /lessons handler ───────────────────────────────────────────────
    if "async def cmd_lessons" not in src:
        anchor = "\nasync def cmd_ob("
        src, ok = _insert_before(src, anchor, LESSONS_HANDLER)
        print("✅ Added cmd_lessons()" if ok else "⚠️  Could not inject cmd_lessons — add manually")
    else:
        print("ℹ️  cmd_lessons already present")

    # ── 3. Register handlers with the application ─────────────────────────────
    # Try several anchors that might appear in bot.py
    if 'CommandHandler("picks"' not in src:
        HANDLER_ANCHORS = [
            'CommandHandler("ob"',
            'CommandHandler("start"',
            'app.add_handler(',
            'application.add_handler(',
        ]
        inserted = False
        for anchor in HANDLER_ANCHORS:
            idx = src.find(anchor)
            if idx != -1:
                line_end = src.find("\n", idx)
                insert_pt = line_end + 1
                new_handlers = (
                    '    app.add_handler(CommandHandler("picks",   cmd_picks))\n'
                    '    app.add_handler(CommandHandler("lessons", cmd_lessons))\n'
                )
                src = src[:insert_pt] + new_handlers + src[insert_pt:]
                print(f"✅ Registered /picks and /lessons handlers (after '{anchor}')")
                inserted = True
                break
        if not inserted:
            print("⚠️  Could not auto-register handlers. Add manually to bot.py:")
            print('   app.add_handler(CommandHandler("picks",   cmd_picks))')
            print('   app.add_handler(CommandHandler("lessons", cmd_lessons))')
    else:
        print("ℹ️  /picks handler already registered")

    # ── 4. Inject lessons data builders into _build_html ─────────────────────
    if "Lessons board data" not in src:
        # Find the return statement inside _build_html and inject before it
        anchor = '    return f"""<!DOCTYPE'
        src, ok = _insert_before(src, anchor, BUILD_LESSONS_ROWS)
        print("✅ Injected lessons row builders into _build_html()" if ok else "⚠️  Could not inject lessons builders")
    else:
        print("ℹ️  Lessons builders already in _build_html")

    # ── 5. Inject Lessons Board HTML section ─────────────────────────────────
    if "LESSONS BOARD" not in src:
        # Insert before the closing </body> tag in the HTML template
        anchor = "</body>"
        src, ok = _insert_before(src, anchor, LESSONS_HTML_SECTION)
        print("✅ Injected Lessons Board HTML section" if ok else "⚠️  Could not find </body> tag")
    else:
        print("ℹ️  Lessons Board HTML already present")

    # ── 6. Wire learning cycle into nightly cron / scheduler ─────────────────
    LEARN_CALL = "run_learning_cycle()"
    if LEARN_CALL not in src:
        # Try to find the nightly learning function
        anchor = "def _run_learning_cycle"
        idx = src.find(anchor)
        if idx != -1:
            # Find the body of that function and add the import+call
            body_start = src.find("\n", idx) + 1
            src = (src[:body_start] +
                   "    from learner import run_learning_cycle as _rlc\n"
                   "    _rlc()\n" +
                   src[body_start:])
            print("✅ Wired run_learning_cycle() into nightly cron")
        else:
            print("ℹ️  Could not find _run_learning_cycle — wire manually or it runs standalone")
    else:
        print("ℹ️  run_learning_cycle already wired")

    BOT.write_text(src)
    print("\n✅ bot.py patched (patch2) successfully.")


if __name__ == "__main__":
    patch()
