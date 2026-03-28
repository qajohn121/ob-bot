#!/usr/bin/env python3
"""
Options Trading Dashboard — Robinhood-style layout
Shows option spreads and single options with real-time P&L
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

EST = timezone(timedelta(hours=-5))

def get_db():
    db = sqlite3.connect("/home/ubuntu/ob-bot/data/trades.db")
    db.row_factory = sqlite3.Row
    return db

def est_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        dt_est = dt.astimezone(EST)
        return dt_est.strftime("%m/%d %H:%M")
    except:
        return iso_str

def get_live_price(symbol):
    try:
        info = yf.Ticker(symbol).info
        if "currentPrice" in info:
            return float(info.get("currentPrice", 0))
        return None
    except:
        return None

def calculate_pnl(trade):
    if trade.get("status") != "OPEN":
        return float(trade.get("pnl_pct", 0) or 0), float(trade.get("pnl_dollar", 0) or 0)

    try:
        current_price = get_live_price(trade.get("symbol"))
        if not current_price:
            return 0, 0

        entry = float(trade.get("entry_price", 0))
        if not entry:
            return 0, 0

        if trade.get("direction") == "CALL":
            pnl_pct = ((current_price - entry) / entry) * 100
        else:
            pnl_pct = ((entry - current_price) / entry) * 100

        option_price = float(trade.get("entry_option_price") or (entry * 0.1))
        pnl_dollar = (pnl_pct / 100) * option_price * 100
        return round(pnl_pct, 2), round(pnl_dollar, 2)
    except:
        return 0, 0

@app.route("/api/positions")
def get_positions():
    """Get all positions formatted for Robinhood-style display"""
    try:
        db = get_db()
        trades = db.execute("SELECT * FROM trades ORDER BY created_at DESC").fetchall()

        positions = []
        spread_groups = {}  # Group spreads by symbol+expiry

        for t in trades:
            try:
                t_dict = dict(t)

                # Group iron condors
                if t_dict.get("spread_type") == "IRON CONDOR":
                    key = f"{t_dict['symbol']}_{t_dict['expiry']}"
                    if key not in spread_groups:
                        spread_groups[key] = {
                            "symbol": t_dict["symbol"],
                            "expiry": t_dict["expiry"],
                            "entry_time": est_time(t_dict["created_at"]),
                            "created_at": t_dict["created_at"],
                            "credit": float(t_dict.get("credit_received", 0) or 0),
                            "legs": [],
                            "status": t_dict["status"],
                            "reason": (t_dict.get("reason") or "")[:100]
                        }

                    spread_groups[key]["legs"].append({
                        "type": t_dict["direction"],
                        "strike": float(t_dict.get("short_strike") or t_dict.get("strike", 0)),
                        "price": float(t_dict.get("entry_price", 0))
                    })
                else:
                    # Single option
                    pnl_pct, pnl_dollar = calculate_pnl(t_dict)

                    positions.append({
                        "type": "OPTION",
                        "id": t_dict["id"],
                        "symbol": t_dict["symbol"],
                        "direction": t_dict["direction"],
                        "strike": float(t_dict.get("strike", 0)),
                        "expiry": t_dict.get("expiry", "N/A"),
                        "entry_price": float(t_dict.get("entry_price", 0)),
                        "entry_time": est_time(t_dict["created_at"]),
                        "days_held": (datetime.now(EST) - datetime.fromisoformat(t_dict["created_at"]).astimezone(EST)).days,
                        "pnl_pct": pnl_pct,
                        "pnl_dollar": pnl_dollar,
                        "status": t_dict["status"],
                        "reason": (t_dict.get("reason") or "")[:100]
                    })
            except Exception as e:
                log.debug(f"Position error: {e}")
                continue

        # Add spreads to positions
        for key, spread in spread_groups.items():
            # Calculate spread P&L
            total_pnl = 0
            try:
                pnl_pct, pnl_dollar = calculate_pnl({
                    "status": spread["status"],
                    "symbol": spread["symbol"],
                    "entry_price": spread.get("credit", 0),
                    "direction": "CALL",
                    "entry_option_price": spread.get("credit", 0) or 1
                })
                total_pnl = pnl_dollar
            except:
                pass

            positions.append({
                "type": "SPREAD",
                "spread_type": "IRON CONDOR",
                "symbol": spread["symbol"],
                "expiry": spread["expiry"],
                "entry_time": spread["entry_time"],
                "days_held": (datetime.now(EST) - datetime.fromisoformat(spread["created_at"]).astimezone(EST)).days,
                "credit_received": spread["credit"],
                "max_profit": spread["credit"],
                "max_loss": round(abs(spread["legs"][0]["strike"] - spread["legs"][-1]["strike"] - spread["credit"]), 2) if len(spread["legs"]) > 0 else 0,
                "pnl_dollar": total_pnl,
                "pnl_pct": round((total_pnl / spread["credit"] * 100), 2) if spread["credit"] > 0 else 0,
                "status": spread["status"],
                "legs": spread["legs"]
            })

        db.close()

        # Calculate totals
        total_pnl = sum([p.get("pnl_dollar", 0) for p in positions])
        open_count = len([p for p in positions if p.get("status") == "OPEN"])

        return jsonify({
            "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST"),
            "positions": positions,
            "summary": {
                "total_positions": len(positions),
                "open_count": open_count,
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": 0
            }
        })
    except Exception as e:
        log.error(f"Positions error: {e}")
        return jsonify({"error": str(e), "positions": []}), 500

@app.route("/api/summary")
def get_summary():
    """Get summary stats"""
    try:
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
        losses = db.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
        open_count = db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]

        closed_pnl = db.execute("SELECT COALESCE(SUM(pnl_dollar), 0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]

        open_trades = db.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        open_pnl = 0.0
        for t in open_trades:
            try:
                pnl_pct, pnl_dollar = calculate_pnl(dict(t))
                open_pnl += pnl_dollar
            except:
                pass

        total_pnl = closed_pnl + open_pnl
        db.close()

        return jsonify({
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "open_count": open_count,
            "win_rate": f"{(wins/total*100):.1f}%" if total > 0 else "0%",
            "closed_pnl": round(closed_pnl, 2),
            "open_pnl": round(open_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST")
        })
    except Exception as e:
        log.error(f"Summary error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/")
def dashboard():
    return render_template_string(HTML)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Options Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { width: 100%; height: 100%; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            color: #1a1a1a;
            padding: 20px;
        }

        .container { max-width: 1400px; margin: 0 auto; }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }

        h1 { font-size: 24px; color: #1a1a1a; }
        .timestamp { font-size: 12px; color: #666; }

        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }

        .summary-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            text-align: center;
        }

        .summary-card label {
            display: block;
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .summary-card .value {
            font-size: 22px;
            font-weight: bold;
            color: #1a1a1a;
        }

        .summary-card .value.positive { color: #05a854; }
        .summary-card .value.negative { color: #e74c3c; }

        .positions-container {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 15px;
        }

        .position-card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
            border-left: 4px solid #007aff;
        }

        .position-card.spread { border-left-color: #ff9500; }
        .position-card.winning { border-left-color: #05a854; }
        .position-card.losing { border-left-color: #e74c3c; }

        .position-header {
            padding: 16px;
            background: #f9f9f9;
            border-bottom: 1px solid #eee;
        }

        .position-symbol {
            font-size: 18px;
            font-weight: bold;
            color: #1a1a1a;
        }

        .position-meta {
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }

        .position-body {
            padding: 16px;
        }

        .position-type {
            display: inline-block;
            font-size: 11px;
            background: #f0f0f0;
            color: #666;
            padding: 4px 8px;
            border-radius: 4px;
            margin-bottom: 12px;
            font-weight: 600;
        }

        .position-legs {
            margin-bottom: 12px;
        }

        .leg {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
        }

        .leg:last-child { border-bottom: none; }
        .leg-type { font-weight: 600; }
        .leg-price { color: #666; }

        .position-footer {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            padding-top: 12px;
            border-top: 1px solid #f0f0f0;
        }

        .footer-item {
            text-align: center;
        }

        .footer-label {
            font-size: 11px;
            color: #666;
            text-transform: uppercase;
            margin-bottom: 4px;
        }

        .footer-value {
            font-size: 16px;
            font-weight: bold;
        }

        .pnl {
            font-weight: bold;
            font-size: 14px;
        }

        .pnl.positive { color: #05a854; }
        .pnl.negative { color: #e74c3c; }

        .status-badge {
            display: inline-block;
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 3px;
            font-weight: 600;
            background: #f0f0f0;
            color: #666;
        }

        .status-badge.open { background: #fff3cd; color: #856404; }
        .status-badge.win { background: #d4edda; color: #155724; }
        .status-badge.loss { background: #f8d7da; color: #721c24; }

        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #666;
        }

        .loading { text-align: center; color: #666; padding: 40px; }
        .error { background: #f8d7da; color: #721c24; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Trading Positions</h1>
                <div class="timestamp" id="timestamp">Loading...</div>
            </div>
        </header>

        <div id="error-container"></div>

        <div class="summary-grid" id="summary">
            <div class="loading">Loading summary...</div>
        </div>

        <div class="positions-container" id="positions">
            <div class="loading">Loading positions...</div>
        </div>
    </div>

    <script>
    function formatPnl(value) {
        return value >= 0 ? '+$' + value.toFixed(2) : '-$' + Math.abs(value).toFixed(2);
    }

    function loadData() {
        // Load summary
        fetch('/api/summary')
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showError('Error loading summary: ' + data.error);
                    return;
                }

                let html = '';
                html += '<div class="summary-card"><label>Total P&L</label><div class="value ' + (data.total_pnl >= 0 ? 'positive' : 'negative') + '">' + formatPnl(data.total_pnl) + '</div></div>';
                html += '<div class="summary-card"><label>Open Positions</label><div class="value">' + data.open_count + '</div></div>';
                html += '<div class="summary-card"><label>Wins / Losses</label><div class="value">' + data.win_count + ' / ' + data.loss_count + '</div></div>';
                html += '<div class="summary-card"><label>Win Rate</label><div class="value">' + data.win_rate + '</div></div>';

                document.getElementById('summary').innerHTML = html;
                document.getElementById('timestamp').textContent = data.timestamp;
            })
            .catch(err => showError('Error: ' + err.message));

        // Load positions
        fetch('/api/positions')
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showError('Error loading positions: ' + data.error);
                    return;
                }

                let html = '';

                if (!data.positions || data.positions.length === 0) {
                    html = '<div class="empty-state">No open positions</div>';
                } else {
                    data.positions.forEach(pos => {
                        let pnlClass = pos.pnl_dollar >= 0 ? 'positive' : 'losing';
                        let cardClass = 'position-card';

                        if (pos.type === 'SPREAD') {
                            cardClass += ' spread';
                        }

                        if (pos.status === 'OPEN') {
                            if (pos.pnl_dollar >= 0) cardClass += ' winning';
                            else cardClass += ' losing';
                        }

                        html += '<div class="' + cardClass + '">';

                        // Header
                        html += '<div class="position-header">';
                        html += '<div class="position-symbol">' + pos.symbol + '</div>';
                        html += '<div class="position-meta">' + pos.expiry + ' • ' + pos.entry_time + ' • ' + pos.days_held + 'd</div>';
                        html += '</div>';

                        // Body
                        html += '<div class="position-body">';

                        if (pos.type === 'SPREAD') {
                            html += '<div class="position-type">IRON CONDOR</div>';
                            html += '<div class="position-legs">';

                            if (pos.legs && pos.legs.length > 0) {
                                pos.legs.forEach(leg => {
                                    html += '<div class="leg">';
                                    html += '<span class="leg-type">' + leg.type + ' $' + leg.strike.toFixed(0) + '</span>';
                                    html += '<span class="leg-price">$' + leg.price.toFixed(2) + '</span>';
                                    html += '</div>';
                                });
                            }

                            html += '</div>';
                            html += '<div class="status-badge ' + pos.status.toLowerCase() + '">' + pos.status + '</div>';
                        } else {
                            html += '<div class="position-type">' + pos.direction + ' $' + pos.strike.toFixed(0) + '</div>';
                            html += '<div style="font-size: 13px; color: #666; margin-bottom: 12px;">Entry: $' + pos.entry_price.toFixed(2) + '</div>';
                            html += '<div class="status-badge ' + pos.status.toLowerCase() + '">' + pos.status + '</div>';
                        }

                        html += '</div>';

                        // Footer
                        html += '<div class="position-footer">';
                        if (pos.type === 'SPREAD') {
                            html += '<div class="footer-item"><div class="footer-label">Credit</div><div class="footer-value">$' + pos.credit_received.toFixed(2) + '</div></div>';
                        } else {
                            html += '<div class="footer-item"><div class="footer-label">Entry</div><div class="footer-value">$' + pos.entry_price.toFixed(2) + '</div></div>';
                        }
                        html += '<div class="footer-item"><div class="footer-label">P&L</div><div class="pnl ' + pnlClass + '">' + formatPnl(pos.pnl_dollar) + ' (' + (pos.pnl_pct >= 0 ? '+' : '') + pos.pnl_pct.toFixed(1) + '%)</div></div>';
                        html += '</div>';

                        html += '</div>';
                    });
                }

                document.getElementById('positions').innerHTML = html;
            })
            .catch(err => showError('Error: ' + err.message));
    }

    function showError(msg) {
        document.getElementById('error-container').innerHTML = '<div class="error">' + msg + '</div>';
    }

    // Initial load
    loadData();

    // Refresh every 3 seconds
    setInterval(loadData, 3000);
    </script>
</body>
</html>
"""
