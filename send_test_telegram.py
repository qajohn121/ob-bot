#!/usr/bin/env python3
"""Send test Telegram message showing current portfolio"""

import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta
import os

EST = timezone(timedelta(hours=-5))

# Try to import trade_alerts, fallback if not available
try:
    from trade_alerts import send_trade_alert
    HAS_TRADE_ALERTS = True
except ImportError:
    HAS_TRADE_ALERTS = False
    print("⚠️  trade_alerts not available, will display message only")

def get_db():
    db = sqlite3.connect("data/trades.db")
    # Use Row factory to access columns by name
    db.row_factory = sqlite3.Row
    return db

def row_to_dict(row):
    """Convert sqlite3.Row to dict"""
    return dict(row) if row else {}

def format_portfolio_message():
    """Format current portfolio as Telegram message"""
    db = get_db()
    trades = db.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC").fetchall()

    lines = []
    lines.append("📊 <b>PORTFOLIO SNAPSHOT</b>")
    lines.append(f"🕐 {datetime.now(EST).strftime('%m/%d %H:%M:%S EST')}")
    lines.append("")

    total_pnl = 0.0
    wins = 0
    losses = 0

    for trade in trades:
        t = row_to_dict(trade)
        symbol = t.get("symbol", "?")
        direction = t.get("direction", "?")
        strike = f"${t.get('strike', 0):.0f}" if t.get('strike') else "—"
        entry_time = str(t.get("created_at", ""))[:10]  # YYYY-MM-DD
        dte = t.get("dte_profile") or "30DTE"
        pnl_pct = float(t.get("pnl_pct", 0) or 0)
        pnl_usd = float(t.get("pnl_dollar", 0) or 0)

        total_pnl += pnl_usd
        if pnl_pct > 0:
            wins += 1
        elif pnl_pct < 0:
            losses += 1

        # Format with emoji based on performance
        if pnl_pct > 10:
            emoji = "🟢"
        elif pnl_pct > 0:
            emoji = "🟡"
        else:
            emoji = "🔴"

        line = f"{emoji} <b>{symbol}</b> {direction:6} ${strike:>6} ({dte}) | <code>{pnl_pct:+6.2f}%</code> ${pnl_usd:+7.2f}"
        lines.append(line)

    lines.append("")
    lines.append("<b>SUMMARY</b>")
    lines.append(f"📈 Open: {len(trades)} | 🟢 Winning: {wins} | 🔴 Losing: {losses}")
    lines.append(f"💰 Total P&L: ${total_pnl:+.2f}")
    lines.append("")
    lines.append("🔗 Dashboard: <code>http://170.9.254.97:3003</code>")
    lines.append("⏰ Updates every 3 seconds")

    db.close()
    return "\n".join(lines)

async def send_message():
    """Send the message via Telegram"""
    message = format_portfolio_message()
    print("=" * 70)
    print(message.replace("<b>", "**").replace("</b>", "**").replace("<code>", "`").replace("</code>", "`"))
    print("=" * 70)

    if HAS_TRADE_ALERTS:
        try:
            print("\n📤 Sending to Telegram...")
            await send_trade_alert(
                alert_type="PNL_UPDATE",
                symbol="",
                direction="",
                entry_price=0,
                strike=0,
                expiry="",
                custom_message=message
            )
            print("✅ Telegram message sent!")
        except Exception as e:
            print(f"❌ Telegram error: {e}")
    else:
        print("⏭️  Skipping Telegram (module not available)")

if __name__ == "__main__":
    asyncio.run(send_message())
