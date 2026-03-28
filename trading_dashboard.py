#!/usr/bin/env python3
"""
Real-time Trading Dashboard — Paper trading with live market data
Shows all option trades (calls/puts and spreads) with real-time P&L
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
    """Get current price from market with fallback"""
    try:
        info = yf.Ticker(symbol).info
        if "currentPrice" in info:
            return float(info.get("currentPrice", 0))
        return None
    except:
        return None

def calculate_pnl(trade):
    """Calculate real P&L using live market data"""
    if trade.get("status") != "OPEN":
        return float(trade.get("pnl_pct", 0) or 0), float(trade.get("pnl_dollar", 0) or 0)

    try:
        current_price = get_live_price(trade.get("symbol"))
        if not current_price:
            return 0, 0

        entry = float(trade.get("entry_price", 0))
        if not entry:
            return 0, 0

        # Simple P&L calculation for calls/puts
        if trade.get("direction") == "CALL":
            pnl_pct = ((current_price - entry) / entry) * 100
        else:
            pnl_pct = ((entry - current_price) / entry) * 100

        option_price = float(trade.get("entry_option_price") or (entry * 0.1))
        pnl_dollar = (pnl_pct / 100) * option_price * 100
        return round(pnl_pct, 2), round(pnl_dollar, 2)
    except Exception as e:
        log.debug(f"P&L calc error: {e}")
        return 0, 0

@app.route("/api/trades")
def get_trades():
    """API endpoint for all trades"""
    try:
        db = get_db()
        trades = db.execute("SELECT * FROM trades ORDER BY created_at DESC").fetchall()

        result = []
        for t in trades:
            try:
                t_dict = dict(t)
                pnl_pct, pnl_dollar = calculate_pnl(t_dict)

                # Format spread indicator
                spread_type = t_dict.get("spread_type") or ""
                is_spread = " [SPREAD]" if spread_type else ""

                result.append({
                    "id": t_dict["id"],
                    "symbol": t_dict["symbol"],
                    "type": f"{t_dict['direction']}{is_spread}",
                    "spread_type": spread_type,
                    "status": t_dict["status"],
                    "entry_price": float(t_dict.get("entry_price", 0)),
                    "entry_time": est_time(t_dict["created_at"]),
                    "exit_price": t_dict.get("exit_price"),
                    "exit_time": est_time(t_dict["closed_at"]) if t_dict.get("closed_at") else None,
                    "dte_profile": t_dict.get("dte_profile"),
                    "pnl_pct": pnl_pct,
                    "pnl_dollar": pnl_dollar,
                    "reason": (t_dict.get("reason") or "")[:60],
                    "days_held": (datetime.now(EST) - datetime.fromisoformat(t_dict["created_at"]).astimezone(EST)).days,
                    "credit": float(t_dict.get("credit_received", 0) or 0),
                })
            except Exception as e:
                log.debug(f"Trade row error: {e}")
                continue

        db.close()
        return jsonify({
            "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST"),
            "total_trades": len(trades),
            "open_count": len([t for t in result if t["status"] == "OPEN"]),
            "trades": result
        })
    except Exception as e:
        log.error(f"Trades endpoint error: {e}")
        return jsonify({"error": str(e), "trades": []}), 500

@app.route("/api/summary")
def get_summary():
    """Performance summary with real-time P&L"""
    try:
        db = get_db()

        total = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
        losses = db.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
        open_count = db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]

        # Calculate closed P&L
        closed_pnl = db.execute("SELECT COALESCE(SUM(pnl_dollar), 0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]

        # Calculate open trades P&L in real-time
        open_trades = db.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        open_pnl = 0.0

        for t in open_trades:
            try:
                pnl_pct, pnl_dollar = calculate_pnl(dict(t))
                open_pnl += pnl_dollar
            except Exception as e:
                log.debug(f"Open trade P&L error: {e}")

        total_pnl = closed_pnl + open_pnl

        db.close()

        return jsonify({
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "open_count": open_count,
            "win_rate": f"{(wins/total*100):.1f}%" if total > 0 else "0%",
            "closed_pnl": f"${closed_pnl:+.2f}",
            "open_pnl": f"${open_pnl:+.2f}",
            "total_pnl": f"${total_pnl:+.2f}",
            "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST")
        })
    except Exception as e:
        log.error(f"Summary endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>OB Bot Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0a0e27; color: #00ff41; line-height: 1.5; font-size: 12px; }
        header { background: #1a1f3a; padding: 15px 20px; border-bottom: 2px solid #00ff41; }
        h1 { color: #00ff41; margin-bottom: 5px; font-size: 16px; }
        .summary { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 15px; }
        .stat { background: #1a1f3a; padding: 12px; border: 1px solid #00ff41; text-align: center; border-radius: 3px; }
        .stat-label { color: #888; font-size: 0.9em; margin-bottom: 5px; }
        .stat-value { font-size: 1.6em; font-weight: bold; color: #00ff41; }
        .stat-value.negative { color: #ff4444; }
        table { width: 100%; margin: 15px; border-collapse: collapse; font-size: 11px; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #1a1f3a; color: #ffff00; font-weight: bold; }
        tr:hover { background: #1a1f3a; }
        .win { color: #00ff41; }
        .loss { color: #ff4444; }
        .open { color: #ffff00; }
        footer { text-align: center; padding: 15px; color: #888; font-size: 0.85em; border-top: 1px solid #333; }
        .refresh { text-align: right; padding: 10px 20px; color: #888; font-size: 0.85em; }
        .error { color: #ff4444; padding: 20px; text-align: center; }
    </style>
</head>
<body>
    <header>
        <h1>OB Bot Trading Dashboard</h1>
        <p>Real-time trading with live market data (EST timezone)</p>
    </header>

    <div id="summary" class="summary" style="opacity: 0.8;">
        <div class="stat"><div class="stat-label">Loading...</div></div>
    </div>

    <div class="refresh">
        Last updated: <span id="refresh-time">--:-- EST</span> (refreshing every 3s)
    </div>

    <table id="trades">
        <tr><td colspan="10" style="text-align: center; color: #888;">Loading trades...</td></tr>
    </table>

    <footer>
        All times in EST | Market hours: 9:30am - 4:00pm EST, Mon-Fri | Real market data from yfinance
    </footer>

    <script>
    function updateDashboard() {
        // Fetch summary
        fetch('/api/summary')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('summary').innerHTML = '<div class="error">Error loading summary: ' + data.error + '</div>';
                    return;
                }

                let pnl_color = '';
                if (data.total_pnl.includes('-')) {
                    pnl_color = 'negative';
                }

                document.getElementById('summary').innerHTML =
                    '<div class="stat"><div class="stat-label">Total Trades</div><div class="stat-value">' + data.total_trades + '</div></div>' +
                    '<div class="stat"><div class="stat-label">Win Rate</div><div class="stat-value">' + data.win_rate + '</div></div>' +
                    '<div class="stat"><div class="stat-label">Open</div><div class="stat-value">' + data.open_count + '</div></div>' +
                    '<div class="stat"><div class="stat-label">Closed P&L</div><div class="stat-value">' + data.closed_pnl + '</div></div>' +
                    '<div class="stat"><div class="stat-label">TOTAL P&L</div><div class="stat-value ' + pnl_color + '">' + data.total_pnl + '</div></div>';
            })
            .catch(err => {
                document.getElementById('summary').innerHTML = '<div class="error">Error: ' + err.message + '</div>';
            });

        // Fetch trades
        fetch('/api/trades')
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('trades').innerHTML = '<tr><td colspan="10" class="error">Error: ' + data.error + '</td></tr>';
                    return;
                }

                let html = '<tr><th>Symbol</th><th>Type</th><th>Entry</th><th>Entry Time</th><th>Days</th><th>P&L %</th><th>P&L $</th><th>Status</th><th>Credit</th><th>Reason</th></tr>';

                if (data.trades && data.trades.length > 0) {
                    data.trades.forEach(t => {
                        let status_class = 'open';
                        if (t.status === 'WIN') status_class = 'win';
                        if (t.status === 'LOSS') status_class = 'loss';

                        let pnl_class = 'win';
                        if (t.pnl_pct < 0) pnl_class = 'loss';

                        let credit_display = t.credit > 0 ? '$' + t.credit.toFixed(2) : '&mdash;';

                        html += '<tr>' +
                            '<td><b>' + t.symbol + '</b></td>' +
                            '<td>' + t.type + '</td>' +
                            '<td>$' + t.entry_price.toFixed(2) + '</td>' +
                            '<td>' + t.entry_time + '</td>' +
                            '<td>' + t.days_held + '</td>' +
                            '<td class="' + pnl_class + '">' + t.pnl_pct.toFixed(2) + '%</td>' +
                            '<td class="' + pnl_class + '">$' + t.pnl_dollar.toFixed(2) + '</td>' +
                            '<td class="' + status_class + '">' + t.status + '</td>' +
                            '<td>' + credit_display + '</td>' +
                            '<td>' + t.reason + '</td>' +
                            '</tr>';
                    });
                }

                document.getElementById('trades').innerHTML = html;
                document.getElementById('refresh-time').textContent = data.timestamp;
            })
            .catch(err => {
                document.getElementById('trades').innerHTML = '<tr><td colspan="10" class="error">Error loading trades: ' + err.message + '</td></tr>';
            });
    }

    // Initial load
    updateDashboard();

    // Refresh every 3 seconds
    setInterval(updateDashboard, 3000);
    </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    """Main dashboard page"""
    return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3003, debug=False, threaded=True)
