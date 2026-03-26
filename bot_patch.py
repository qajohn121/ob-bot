#!/usr/bin/env python3
"""
bot_patch.py — apply this patch to bot.py on the VPS.
Run: python bot_patch.py
It rewrites only the _fmt() function and the pr() helper inside _build_html().
"""
import re, sys
from pathlib import Path

BOT = Path("/home/ubuntu/ob-bot/bot.py")

NEW_FMT = '''def _fmt(item,direction,rank):
    score=item.get("call_score" if direction=="CALL" else "put_score",0)
    reason=item.get("call_reason" if direction=="CALL" else "put_reason","")
    greeks=item.get("call_greeks" if direction=="CALL" else "put_greeks",{})
    entry=item.get("entry_call" if direction=="CALL" else "entry_put",{})
    em="🚀" if direction=="CALL" else "💥"
    cv="🔥HIGH" if score>=75 else ("✅MED" if score>=55 else "⚠️LOW")
    dte=item.get("days_to_earnings",999)
    # Entry block
    strike=entry.get("strike","?"); expiry=entry.get("expiry","?")
    opt_px=entry.get("est_option_price","?"); stop=entry.get("stop_loss_option","?")
    tgt=entry.get("target_option","?"); contracts=entry.get("max_contracts",1)
    dte_opt=entry.get("dte","?"); cost=entry.get("contract_cost","?")
    return (f"{em} <b>#{rank} {item['symbol']} [{direction}]</b> {score}/100 {cv}\\n"
            f"${item['price']:.2f} 4h:{item['move_4h']:+.1f}% Vol:{item['rel_volume']:.1f}x RSI:{item['rsi']:.0f} IV:{item['iv_pct']:.0f}%\\n"
            f"Earn:{'N/A' if dte==999 else str(dte)+'d'} 52Wk:{item['pct_from_52w_high']:+.1f}%\\n"
            f"Delta:{greeks.get('delta',0):.2f} Gamma:{greeks.get('gamma',0):.4f}\\n"
            f"{'🎯WAR ' if item.get('is_war_sector') else ''}{'⚠️DISTRESSED ' if item.get('is_distressed') else ''}{reason}\\n"
            f"─────────────────\\n"
            f"📋 BUY {direction} ${strike} exp {expiry} (~${opt_px}/contract, {dte_opt}DTE)\\n"
            f"🛑 Stop: ${stop} | 🎯 Target: ${tgt}\\n"
            f"📦 Max {contracts} contract(s) (${cost} cost)")'''

NEW_PR = '''    def pr(items,dk):
        out=""
        for i,p in enumerate(items,1):
            sc=p.get(f"{dk}_score",0); rs=p.get(f"{dk}_reason","")
            entry=p.get(f"entry_{dk}",{})
            w_=("🎯" if p.get("is_war_sector") else "")+("⚠️" if p.get("is_distressed") else "")
            mv=p.get("move_4h",0); mc="#0f0" if mv>0 else "#f44"
            strike=entry.get("strike","?"); expiry=entry.get("expiry","?")
            opt_px=entry.get("est_option_price","?"); stop=entry.get("stop_loss_option","?")
            tgt=entry.get("target_option","?"); dte_opt=entry.get("dte","?")
            out+=(f"<tr><td>#{i}</td><td><b>{p['symbol']}</b>{w_}</td><td><b>{sc}</b></td>"
                  f"<td>${p['price']:.2f}</td><td style='color:{mc}'>{mv:+.1f}%</td>"
                  f"<td>{p.get('rel_volume',0):.1f}x</td><td>{p.get('iv_pct',0):.0f}%</td>"
                  f"<td><b>${strike}</b></td><td>{expiry}<br><small style='color:#888'>{dte_opt}DTE</small></td>"
                  f"<td style='color:#fa0'>${opt_px}</td>"
                  f"<td style='color:#f44'>${stop}</td><td style='color:#0f0'>${tgt}</td>"
                  f"<td style='font-size:10px'>{rs[:50]}</td></tr>")
        return out or "<tr><td colspan='13' style='color:#555'>No scan yet — send /ob in Telegram</td></tr>"'''

def patch():
    if not BOT.exists():
        print(f"ERROR: {BOT} not found"); sys.exit(1)
    src = BOT.read_text()

    # Replace _fmt function
    old_fmt_pattern = r'def _fmt\(item,direction,rank\):.*?(?=\ndef |\n@|\nif __name__)'
    new_fmt_match = re.search(old_fmt_pattern, src, re.DOTALL)
    if new_fmt_match:
        src = src[:new_fmt_match.start()] + NEW_FMT + "\n\n" + src[new_fmt_match.end():]
        print("✅ Patched _fmt()")
    else:
        print("⚠️  Could not find _fmt() — patching manually required")

    # Replace pr() inside _build_html
    old_pr_pattern = r'    def pr\(items,dk\):.*?(?=\n    rows=|\n    wr_color=)'
    new_pr_match = re.search(old_pr_pattern, src, re.DOTALL)
    if new_pr_match:
        src = src[:new_pr_match.start()] + NEW_PR + "\n" + src[new_pr_match.end():]
        print("✅ Patched pr() in _build_html()")
    else:
        print("⚠️  Could not find pr() — patching manually required")

    # Update table headers for scan tables
    old_th = "<th>#</th><th>Symbol</th><th>Score</th><th>Price</th><th>4h</th><th>Vol</th><th>IV</th><th>Reason</th>"
    new_th = "<th>#</th><th>Symbol</th><th>Score</th><th>Price</th><th>4h</th><th>Vol</th><th>IV</th><th>Strike</th><th>Expiry</th><th>~Price</th><th>Stop</th><th>Target</th><th>Reason</th>"
    src = src.replace(old_th, new_th)
    print("✅ Updated table headers")

    BOT.write_text(src)
    print(f"\n✅ bot.py patched successfully.")

if __name__ == "__main__":
    patch()
