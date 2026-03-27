#!/usr/bin/env python3
"""
Real-time Trading Dashboard — Paper trading with live market data
Shows all trades with P&L, entry/exit points, and performance metrics
EST timezone throughout
"""

from flask import Flask, render_template_string, jsonify
import sqlite3
import yfinance as yf
from datetime import datetime, timezone, timedelta
import json
import logging

app = Flask(__name__)
log = logging.getLogger("dashboard")

# EST timezone
EST = timezone(timedelta(hours=-5))

def get_db():
    db = sqlite3.connect("/home/ubuntu/ob-bot/data/trades.db")
    db.row_factory = sqlite3.Row
    return db

def est_time(iso_str):
    """Convert ISO string to EST readable format"""
    try:
        dt = datetime.fromisoformat(iso_str)
        dt_est = dt.astimezone(EST)
        return dt_est.strftime("%m/%d %H:%M EST")
    except:
        return iso_str

def get_live_price(symbol):
    """Get current price from market"""
    try:
        return float(yf.Ticker(symbol).info.get("currentPrice", 0))
    except:
        return 0

def calculate_pnl(trade):
    """Calculate real P&L using live market data"""
    if trade["status"] != "OPEN":
        return trade.get("pnl_pct", 0), trade.get("pnl_dollar", 0)

    try:
        current_price = get_live_price(trade["symbol"])
        if not current_price:
            return 0, 0

        entry = trade["entry_price"]
        # Simple P&L calculation for calls/puts
        if trade["direction"] == "CALL":
            pnl_pct = ((current_price - entry) / entry) * 100
        else:
            pnl_pct = ((entry - current_price) / entry) * 100

        pnl_dollar = (pnl_pct / 100) * trade.get("entry_option_price", entry * 0.1) * 100
        return round(pnl_pct, 2), round(pnl_dollar, 2)
    except:
        return 0, 0

@app.route("/api/trades")
def get_trades():
    """API endpoint for all trades"""
    db = get_db()
    trades = db.execute("SELECT * FROM trades ORDER BY created_at DESC").fetchall()

    result = []
    for t in trades:
        pnl_pct, pnl_dollar = calculate_pnl(dict(t))
        result.append({
            "id": t["id"],
            "symbol": t["symbol"],
            "direction": t["direction"],
            "status": t["status"],
            "entry_price": t["entry_price"],
            "entry_time": est_time(t["created_at"]),
            "exit_price": t["exit_price"],
            "exit_time": est_time(t["closed_at"]) if t["closed_at"] else None,
            "dte_profile": t["dte_profile"],
            "pnl_pct": pnl_pct,
            "pnl_dollar": pnl_dollar,
            "reason": t["reason"][:50] if t["reason"] else "",
            "days_held": (datetime.now(EST) - datetime.fromisoformat(t["created_at"]).astimezone(EST)).days
        })

    db.close()
    return jsonify({
        "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST"),
        "total_trades": len(trades),
        "open_count": len([t for t in result if t["status"] == "OPEN"]),
        "trades": result
    })

@app.route("/api/summary")
def get_summary():
    """Performance summary"""
    db = get_db()

    # Overall stats
    total = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    wins = db.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
    losses = db.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
    open_count = db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]

    # P&L
    closed_pnl = db.execute("SELECT COALESCE(SUM(pnl_dollar), 0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]

    db.close()

    return jsonify({
        "total_trades": total,
        "win_count": wins,
        "loss_count": losses,
        "open_count": open_count,
        "win_rate": f"{(wins/total*100):.1f}%" if total > 0 else "0%",
        "total_pnl": f"${closed_pnl:+.2f}",
        "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST")
    })

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>OB Bot Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Courier New; background: #0a0e27; color: #00ff41; line-height: 1.6; }
        header { background: #1a1f3a; padding: 20px; border-bottom: 2px solid #00ff41; }
        h1 { color: #00ff41; margin-bottom: 10px; }
        .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px; }
        .stat { background: #1a1f3a; padding: 15px; border: 1px solid #00ff41; text-align: center; }
        .stat-label { color: #888; font-size: 0.9em; }
        .stat-value { font-size: 1.8em; font-weight: bold; color: #00ff41; margin-top: 5px; }
        table { width: 100%; margin: 20px; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #1a1f3a; color: #00ff41; font-weight: bold; }
        tr:hover { background: #1a1f3a; }
        .win { color: #00ff41; }
        .loss { color: #ff4444; }
        .open { color: #ffff00; }
        .closed { color: #888; }
        footer { text-align: center; padding: 20px; color: #888; font-size: 0.9em; }
        .refresh { text-align: right; padding: 20px; color: #888; }
    </style>
    <script>
        setInterval(() => {
            fetch('/api/summary').then(r => r.json()).then(data => {
                document.getElementById('summary').innerHTML = `
                    <div class="stat">
                        <div class="stat-label">Total Trades</div>
                        <div class="stat-value">${data.total_trades}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Win Rate</div>
                        <div class="stat-value">${data.win_rate}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Open</div>
                        <div class="stat-value">${data.open_count}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">Total P&L</div>
                        <div class="stat-value">${data.total_pnl}</div>
                    </div>
                `;
            });

            fetch('/api/trades').then(r => r.json()).then(data => {
                let html = `<tr><th>Symbol</th><th>Type</th><th>Entry</th><th>Entry Time</th><th>Days</th><th>P&L %</th><th>P&L $</th><th>Status</th><th>Reason</th></tr>`;
                data.trades.forEach(t => {
                    let status_class = t.status === 'OPEN' ? 'open' : (t.status === 'WIN' ? 'win' : 'loss');
                    let pnl_class = t.pnl_pct >= 0 ? 'win' : 'loss';
                    html += `
                        <tr>
                            <td><b>${t.symbol}</b></td>
                            <td>${t.direction}</td>
                            <td>$${t.entry_price.toFixed(2)}</td>
                            <td>${t.entry_time}</td>
                            <td>${t.days_held}</td>
                            <td class="${pnl_class}">${t.pnl_pct.toFixed(2)}%</td>
                            <td class="${pnl_class}">$${t.pnl_dollar.toFixed(2)}</td>
                            <td class="${status_class}">${t.status}</td>
                            <td>${t.reason}</td>
                        </tr>
                    `;
                });
                document.getElementById('trades').innerHTML = html;
                document.getElementById('refresh-time').textContent = data.timestamp;
            });
        }, 3000);
    </script>
</head>
<body>
    <header>
        <h1>🤖 OB Bot Trading Dashboard</h1>
        <p>Real-time paper trading with live market data (EST timezone)</p>
    </header>

    <div id="summary" class="summary" style="opacity: 0.8;">
        <div class="stat"><div class="stat-label">Loading...</div></div>
    </div>

    <div class="refresh">
        Last updated: <span id="refresh-time">--:-- EST</span> (refreshing every 3s)
    </div>

    <table id="trades">
        <tr><td colspan="9" style="text-align: center; color: #888;">Loading trades...</td></tr>
    </table>

    <footer>
        All times in EST | Market hours: 9:30am - 4:00pm EST, Mon-Fri | Real market data from yfinance
    </footer>
</body>
</html>
"""

@app.route("/")
def dashboard():
    """Main dashboard page"""
    return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3003, debug=False)
