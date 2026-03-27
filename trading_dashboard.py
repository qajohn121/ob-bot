#!/usr/bin/env python3
"""
Real-time Options Spreads Dashboard — Paper trading with live market data
Shows Iron Condors & Credit Spreads with real bid/ask from option chains
EST timezone throughout
"""

from flask import Flask, render_template_string, jsonify
import sqlite3
import yfinance as yf
from datetime import datetime, timezone, timedelta
import json
import logging
import threading
import time

app = Flask(__name__)
log = logging.getLogger("dashboard")

# EST timezone
EST = timezone(timedelta(hours=-5))

# Cache for option chains (refresh every 5 minutes)
_OPTION_CHAIN_CACHE = {}
_CACHE_TTL = 300  # 5 minutes

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

def get_option_chain_cached(symbol, expiry):
    """Get option chain with 5-minute caching"""
    cache_key = f"{symbol}_{expiry}"
    now = time.time()

    if cache_key in _OPTION_CHAIN_CACHE:
        cached_data, cached_time = _OPTION_CHAIN_CACHE[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_data

    try:
        tk = yf.Ticker(symbol)
        chain = tk.option_chain(expiry)
        data = {"calls": chain.calls, "puts": chain.puts}
        _OPTION_CHAIN_CACHE[cache_key] = (data, now)
        return data
    except Exception as e:
        log.debug(f"Option chain for {symbol} {expiry}: {e}")
        return None

def get_option_price(symbol, expiry, strike, opt_type):
    """Get current bid/ask for specific option"""
    try:
        chain_data = get_option_chain_cached(symbol, expiry)
        if not chain_data:
            return None

        chain = chain_data["calls"] if opt_type == "CALL" else chain_data["puts"]
        option = chain[chain["strike"] == strike].iloc[0] if len(chain[chain["strike"] == strike]) > 0 else None

        if option is not None:
            bid = float(option.get("bid", 0)) if option.get("bid", 0) > 0 else None
            ask = float(option.get("ask", 0)) if option.get("ask", 0) > 0 else None
            mid = (bid + ask) / 2 if bid and ask else None
            return {"bid": bid, "ask": ask, "mid": mid, "volume": int(option.get("volume", 0))}
    except Exception as e:
        log.debug(f"Option price {symbol} {strike} {opt_type}: {e}")

    return None

def build_spread_display(db, spread_id):
    """Build a spread display from component trades"""
    # Get all trades that are part of this spread
    trades = db.execute(
        "SELECT * FROM trades WHERE id=? OR (symbol=(SELECT symbol FROM trades WHERE id=?) AND created_at=(SELECT created_at FROM trades WHERE id=?))",
        (spread_id, spread_id, spread_id)
    ).fetchall()

    if not trades:
        return None

    main_trade = trades[0]
    symbol = main_trade["symbol"]
    expiry = main_trade["expiry"]

    # If single trade, treat as simple option (not a spread)
    if len(trades) == 1 and main_trade["spread_type"] != "IRON CONDOR":
        return None

    # Get current option prices
    spread_data = {
        "id": spread_id,
        "symbol": symbol,
        "expiry": expiry,
        "entry_time": est_time(main_trade["created_at"]),
        "days_held": (datetime.now(EST) - datetime.fromisoformat(main_trade["created_at"]).astimezone(EST)).days,
        "legs": [],
        "total_credit": 0,
        "current_credit": 0,
        "max_loss": 0,
        "max_profit": 0,
        "status": main_trade["status"],
        "reason": main_trade["reason"][:80] if main_trade["reason"] else ""
    }

    # For now, handle as IRON CONDOR if spread_type is set
    if main_trade["spread_type"] == "IRON CONDOR":
        # Get IC details
        short_call_strike = main_trade["short_strike"]
        long_call_strike = main_trade.get("long_strike")
        entry_credit = main_trade["credit_received"]

        # Fetch real prices for each leg
        call_price = get_option_price(symbol, expiry, short_call_strike, "CALL")
        put_price = get_option_price(symbol, expiry, short_call_strike, "PUT")

        spread_data["legs"] = [
            {"type": "SHORT CALL", "strike": short_call_strike, "bid": call_price.get("bid") if call_price else None},
            {"type": "LONG CALL", "strike": long_call_strike, "ask": call_price.get("ask") if call_price else None},
            {"type": "SHORT PUT", "strike": short_call_strike - 50, "bid": put_price.get("bid") if put_price else None},
            {"type": "LONG PUT", "strike": short_call_strike - 100, "ask": put_price.get("ask") if put_price else None},
        ]
        spread_data["entry_credit"] = entry_credit
        spread_data["max_loss"] = 50 - entry_credit  # Max loss is width minus credit
        spread_data["max_profit"] = entry_credit

    return spread_data

@app.route("/api/trades")
def get_trades():
    """API endpoint for all trades"""
    db = get_db()
    trades = db.execute("SELECT * FROM trades ORDER BY created_at DESC").fetchall()

    result = []
    processed_ids = set()

    for t in trades:
        if t["id"] in processed_ids:
            continue

        # Check if this is a spread
        if t["spread_type"] == "IRON CONDOR" or t["is_spread"]:
            spread = build_spread_display(db, t["id"])
            if spread:
                result.append({
                    "type": "SPREAD",
                    "spread_type": t["spread_type"],
                    "data": spread
                })
                processed_ids.add(t["id"])
                continue

        # Single option trade
        try:
            current_price = float(yf.Ticker(t["symbol"]).info.get("currentPrice", 0)) if t["symbol"] else 0

            if t["status"] == "OPEN":
                entry = t["entry_price"]
                if t["direction"] == "CALL":
                    pnl_pct = ((current_price - entry) / entry) * 100 if entry else 0
                else:
                    pnl_pct = ((entry - current_price) / entry) * 100 if entry else 0
                pnl_dollar = (pnl_pct / 100) * (t["entry_option_price"] or entry * 0.1) * 100
            else:
                pnl_pct = t.get("pnl_pct", 0)
                pnl_dollar = t.get("pnl_dollar", 0)

            result.append({
                "type": "OPTION",
                "data": {
                    "id": t["id"],
                    "symbol": t["symbol"],
                    "direction": t["direction"],
                    "status": t["status"],
                    "entry_price": t["entry_price"],
                    "entry_time": est_time(t["created_at"]),
                    "exit_price": t["exit_price"],
                    "exit_time": est_time(t["closed_at"]) if t["closed_at"] else None,
                    "dte_profile": t["dte_profile"],
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_dollar": round(pnl_dollar, 2),
                    "reason": t["reason"][:50] if t["reason"] else "",
                    "days_held": (datetime.now(EST) - datetime.fromisoformat(t["created_at"]).astimezone(EST)).days
                }
            })
        except Exception as e:
            log.debug(f"Trade {t['id']}: {e}")
            continue

    db.close()
    return jsonify({
        "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST"),
        "total_trades": len(trades),
        "open_count": len([t for t in result if t.get("data", {}).get("status") == "OPEN"]),
        "trades": result
    })

@app.route("/api/summary")
def get_summary():
    """Performance summary"""
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    wins = db.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
    losses = db.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
    open_count = db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]

    # Calculate P&L from closed trades + estimated from open
    closed_pnl = db.execute("SELECT COALESCE(SUM(pnl_dollar), 0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]

    # Estimate open trades P&L
    open_trades = db.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    open_pnl = 0
    for t in open_trades:
        try:
            current = float(yf.Ticker(t["symbol"]).info.get("currentPrice", 0)) if t["symbol"] else 0
            if t["direction"] == "CALL":
                pnl_pct = ((current - t["entry_price"]) / t["entry_price"]) * 100 if t["entry_price"] else 0
            else:
                pnl_pct = ((t["entry_price"] - current) / t["entry_price"]) * 100 if t["entry_price"] else 0
            pnl_dollar = (pnl_pct / 100) * (t["entry_option_price"] or t["entry_price"] * 0.1) * 100
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
        "closed_pnl": f"${closed_pnl:+.2f}",
        "open_pnl": f"${open_pnl:+.2f}",
        "total_pnl": f"${total_pnl:+.2f}",
        "timestamp": datetime.now(EST).strftime("%m/%d %H:%M:%S EST")
    })

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>OB Bot Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0a0e27; color: #00ff41; line-height: 1.4; font-size: 13px; }
        header { background: #1a1f3a; padding: 15px 20px; border-bottom: 2px solid #00ff41; }
        h1 { color: #00ff41; margin-bottom: 5px; font-size: 18px; }
        .summary { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 15px; }
        .stat { background: #1a1f3a; padding: 10px; border: 1px solid #00ff41; text-align: center; }
        .stat-label { color: #888; font-size: 0.85em; }
        .stat-value { font-size: 1.5em; font-weight: bold; color: #00ff41; margin-top: 3px; }
        .spread-row { background: #1a1f3a; margin: 10px; padding: 12px; border: 1px solid #00ff41; border-radius: 4px; }
        .spread-header { display: grid; grid-template-columns: 80px 80px 100px 60px 80px 80px; gap: 10px; font-weight: bold; color: #ffff00; margin-bottom: 8px; }
        .spread-leg { display: grid; grid-template-columns: 80px 80px 100px 60px 80px 80px; gap: 10px; padding: 6px 0; border-top: 1px solid #333; }
        .leg-type { color: #00ff41; min-width: 80px; }
        .strike { color: #ffff00; }
        .bid-ask { color: #88ff88; }
        .pnl-pos { color: #00ff41; }
        .pnl-neg { color: #ff4444; }
        table { width: 100%; margin: 15px; border-collapse: collapse; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #1a1f3a; color: #00ff41; font-weight: bold; }
        tr:hover { background: #1a1f3a; }
        .win { color: #00ff41; }
        .loss { color: #ff4444; }
        .open { color: #ffff00; }
        footer { text-align: center; padding: 15px; color: #888; font-size: 0.85em; }
        .refresh { text-align: right; padding: 10px 20px; color: #888; font-size: 0.85em; }
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
                        <div class="stat-label">Closed P&L</div>
                        <div class="stat-value">${data.closed_pnl}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-label">TOTAL P&L</div>
                        <div class="stat-value" style="color: ${data.total_pnl.includes('-') ? '#ff4444' : '#00ff41'}">${data.total_pnl}</div>
                    </div>
                `);
            });

            fetch('/api/trades').then(r => r.json()).then(data => {
                let html = '';

                data.trades.forEach(t => {
                    if (t.type === 'SPREAD') {
                        // Display as spread
                        const sp = t.data;
                        const pnl_color = sp.max_profit > 0 ? 'pnl-pos' : 'pnl-neg';
                        html += `
                            <div class="spread-row">
                                <div style="margin-bottom: 8px; color: #ffff00; font-weight: bold;">
                                    ${sp.symbol} ${sp.spread_type} | ${sp.expiry} | ${sp.entry_time} (${sp.days_held}d)
                                </div>
                                <div class="spread-header">
                                    <div>Leg Type</div>
                                    <div>Strike</div>
                                    <div>Bid/Ask</div>
                                    <div>Vol</div>
                                    <div>P&L</div>
                                    <div>Status</div>
                                </div>
                                ${sp.legs.map(leg => `
                                    <div class="spread-leg">
                                        <div class="leg-type">${leg.type}</div>
                                        <div class="strike">${leg.strike}</div>
                                        <div class="bid-ask">${leg.bid ? leg.bid.toFixed(2) : '-'} / ${leg.ask ? leg.ask.toFixed(2) : '-'}</div>
                                        <div>--</div>
                                        <div class="${pnl_color}">--</div>
                                        <div class="open">${sp.status}</div>
                                    </div>
                                `).join('')}
                                <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #444; color: #88ff88;">
                                    Credit: $${sp.entry_credit || 0} | Max Profit: $${sp.max_profit || 0} | Max Loss: $${sp.max_loss || 0}
                                </div>
                            </div>
                        `;
                    } else if (t.type === 'OPTION') {
                        // Display as single option
                        const tr = t.data;
                        const status_class = tr.status === 'OPEN' ? 'open' : (tr.status === 'WIN' ? 'win' : 'loss');
                        const pnl_class = tr.pnl_pct >= 0 ? 'win' : 'loss';
                        html += `
                            <tr>
                                <td><b>${tr.symbol}</b></td>
                                <td>${tr.direction}</td>
                                <td>$${tr.entry_price.toFixed(2)}</td>
                                <td>${tr.entry_time}</td>
                                <td>${tr.days_held}</td>
                                <td class="${pnl_class}">${tr.pnl_pct.toFixed(2)}%</td>
                                <td class="${pnl_class}">$${tr.pnl_dollar.toFixed(2)}</td>
                                <td class="${status_class}">${tr.status}</td>
                                <td>${tr.reason}</td>
                            </tr>
                        `;
                    }
                });

                if (html === '') {
                    html = '<tr><td colspan="9" style="text-align: center; color: #888;">No trades yet</td></tr>';
                }

                document.getElementById('trades').innerHTML = html;
                document.getElementById('refresh-time').textContent = data.timestamp;
            });
        }, 3000);
    </script>
</head>
<body>
    <header>
        <h1>🤖 OB Bot Trading Dashboard</h1>
        <p>Real-time option spreads with live market data (EST timezone)</p>
    </header>

    <div id="summary" class="summary" style="opacity: 0.8;">
        <div class="stat"><div class="stat-label">Loading...</div></div>
    </div>

    <div class="refresh">
        Last updated: <span id="refresh-time">--:-- EST</span> (refreshing every 3s)
    </div>

    <div id="trades" style="margin: 15px;">
        <div style="text-align: center; color: #888;">Loading trades...</div>
    </div>

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
    app.run(host="0.0.0.0", port=3003, debug=False, threaded=True)
