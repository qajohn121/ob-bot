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
from html import escape
import time

app = Flask(__name__)
log = logging.getLogger("dashboard")

EST = timezone(timedelta(hours=-5))

# Price cache with 10-second TTL to prevent N+1 API calls
_PRICE_CACHE = {}
_CACHE_TTL = 10  # seconds

def _init_db_indexes():
    """Initialize database indexes for fast queries (idempotent)"""
    try:
        db = get_db()
        # Index on status for fast WHERE status='OPEN' queries
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        # Index on created_at DESC for fast ORDER BY queries
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at DESC)")
        # Composite index for common WHERE + ORDER BY patterns
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_status_created ON trades(status, created_at DESC)")
        db.commit()
        db.close()
        log.info("Database indexes initialized")
    except Exception as e:
        log.warning(f"Could not initialize indexes: {e}")

def get_db():
    db = sqlite3.connect("/home/ubuntu/ob-bot/data/trades.db")
    db.row_factory = sqlite3.Row
    return db

def est_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        dt_est = dt.astimezone(EST)
        return dt_est.strftime("%m/%d %H:%M")
    except Exception as e:
        log.debug(f"Could not parse datetime {iso_str}: {e}")
        return iso_str

def get_live_price(symbol):
    """Get live price with caching to prevent N+1 API calls"""
    now = time.time()

    # Check cache
    if symbol in _PRICE_CACHE:
        cached_price, cached_time = _PRICE_CACHE[symbol]
        if now - cached_time < _CACHE_TTL:
            return cached_price

    # Fetch fresh price
    try:
        info = yf.Ticker(symbol).info
        price = info.get("currentPrice")
        if price is not None:
            _PRICE_CACHE[symbol] = (float(price), now)
            return float(price)
        return None
    except Exception as e:
        log.debug(f"Error fetching price for {symbol}: {e}")
        return None

def get_live_prices_batch(symbols):
    """Fetch multiple prices efficiently (reduces API calls)"""
    prices = {}
    missing = []
    now = time.time()

    # Check cache for each symbol
    for symbol in symbols:
        if symbol in _PRICE_CACHE:
            cached_price, cached_time = _PRICE_CACHE[symbol]
            if now - cached_time < _CACHE_TTL:
                prices[symbol] = cached_price
            else:
                missing.append(symbol)
        else:
            missing.append(symbol)

    # Fetch only missing prices
    if missing:
        try:
            tickers = yf.Tickers(" ".join(missing))
            for symbol in missing:
                try:
                    price = tickers.tickers[symbol].info.get("currentPrice")
                    if price is not None:
                        price = float(price)
                        _PRICE_CACHE[symbol] = (price, now)
                        prices[symbol] = price
                except Exception as e:
                    log.debug(f"Error fetching price for {symbol}: {e}")
        except Exception as e:
            log.debug(f"Batch price fetch error: {e}")
            # Fall back to individual fetches
            for symbol in missing:
                price = get_live_price(symbol)
                if price is not None:
                    prices[symbol] = price

    return prices

def calculate_pnl(trade, current_price=None):
    """Calculate P&L for a trade. If current_price not provided, fetch it."""
    status = trade.get("status")
    if status != "OPEN":
        try:
            return float(trade.get("pnl_pct", 0) or 0), float(trade.get("pnl_dollar", 0) or 0)
        except (ValueError, TypeError) as e:
            log.debug(f"Error converting closed trade P&L: {e}")
            return 0.0, 0.0

    try:
        if current_price is None:
            current_price = get_live_price(trade.get("symbol"))

        if current_price is None or current_price <= 0:
            return 0.0, 0.0

        entry = float(trade.get("entry_price", 0) or 0)
        if entry <= 0:  # Fixed: check > 0 instead of `if not entry`
            return 0.0, 0.0

        direction = trade.get("direction", "").upper()
        if direction == "CALL":
            pnl_pct = ((current_price - entry) / entry) * 100
        elif direction == "PUT":
            pnl_pct = ((entry - current_price) / entry) * 100
        else:
            return 0.0, 0.0

        # Safer option price conversion
        try:
            option_price = float(trade.get("entry_option_price", 0) or 0)
        except (ValueError, TypeError):
            option_price = entry * 0.1

        if option_price <= 0:
            option_price = entry * 0.1

        pnl_dollar = (pnl_pct / 100) * option_price * 100
        return round(pnl_pct, 2), round(pnl_dollar, 2)
    except Exception as e:
        log.debug(f"Error calculating P&L: {e}")
        return 0.0, 0.0

@app.route("/api/positions")
def get_positions():
    """Get all positions formatted for Robinhood-style display"""
    try:
        db = get_db()
        # Fixed: SELECT specific columns instead of *
        cols = "id, symbol, direction, strike, expiry, entry_price, created_at, status, spread_type, credit_received, short_strike, entry_option_price, reason"
        trades = db.execute(f"SELECT {cols} FROM trades WHERE status IN ('OPEN', 'WIN', 'LOSS') ORDER BY created_at DESC").fetchall()

        # Pre-compute EST now once for all datetime calculations (fix: datetime redundancy)
        now_est = datetime.now(EST)

        # Collect all symbols to batch-fetch prices
        symbols = set(t["symbol"] for t in trades)
        prices = get_live_prices_batch(list(symbols))

        positions = []
        spread_groups = {}  # Group spreads by symbol+expiry

        for t in trades:
            try:
                t_dict = dict(t)
                symbol = t_dict.get("symbol")
                current_price = prices.get(symbol)

                # Group iron condors
                if t_dict.get("spread_type") == "IRON CONDOR":
                    key = f"{symbol}_{t_dict['expiry']}"
                    if key not in spread_groups:
                        spread_groups[key] = {
                            "symbol": symbol,
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
                    # Single option - pass pre-fetched price
                    pnl_pct, pnl_dollar = calculate_pnl(t_dict, current_price)

                    try:
                        entry_time = datetime.fromisoformat(t_dict["created_at"]).astimezone(EST)
                        days_held = (now_est - entry_time).days
                    except (ValueError, TypeError) as e:
                        log.debug(f"Error parsing created_at: {e}")
                        days_held = 0

                    positions.append({
                        "type": "OPTION",
                        "id": t_dict["id"],
                        "symbol": symbol,
                        "direction": t_dict.get("direction", ""),
                        "strike": float(t_dict.get("strike", 0) or 0),
                        "expiry": t_dict.get("expiry", "N/A"),
                        "entry_price": float(t_dict.get("entry_price", 0) or 0),
                        "entry_time": est_time(t_dict["created_at"]),
                        "days_held": days_held,
                        "pnl_pct": pnl_pct,
                        "pnl_dollar": pnl_dollar,
                        "status": t_dict.get("status", ""),
                        "reason": escape((t_dict.get("reason") or "")[:100])  # Fixed: HTML escape
                    })
            except Exception as e:
                log.exception(f"Position parsing error: {e}")
                continue

        # Add spreads to positions
        for key, spread in spread_groups.items():
            # Calculate spread P&L
            total_pnl = 0.0
            try:
                pnl_pct, pnl_dollar = calculate_pnl({
                    "status": spread["status"],
                    "symbol": spread["symbol"],
                    "entry_price": spread.get("credit", 0),
                    "direction": "CALL",
                    "entry_option_price": spread.get("credit", 0) or 1
                })
                total_pnl = pnl_dollar
            except Exception as e:
                log.debug(f"Spread P&L error: {e}")

            try:
                created_time = datetime.fromisoformat(spread["created_at"]).astimezone(EST)
                days_held = (now_est - created_time).days
            except (ValueError, TypeError) as e:
                log.debug(f"Error parsing spread created_at: {e}")
                days_held = 0

            credit_received = float(spread.get("credit", 0) or 0)
            max_loss = 0.0
            if spread["legs"] and len(spread["legs"]) > 1:
                try:
                    strike_diff = abs(float(spread["legs"][0]["strike"]) - float(spread["legs"][-1]["strike"]))
                    max_loss = round(max(0, strike_diff - credit_received), 2)
                except (ValueError, IndexError, TypeError) as e:
                    log.debug(f"Error calculating max loss: {e}")

            positions.append({
                "type": "SPREAD",
                "spread_type": "IRON CONDOR",
                "symbol": spread["symbol"],
                "expiry": spread["expiry"],
                "entry_time": spread["entry_time"],
                "days_held": days_held,
                "credit_received": credit_received,
                "max_profit": credit_received,
                "max_loss": max_loss,
                "pnl_dollar": total_pnl,
                "pnl_pct": round((total_pnl / credit_received * 100), 2) if credit_received > 0 else 0.0,
                "status": spread["status"],
                "legs": spread["legs"],
                "reason": escape(spread.get("reason", "")[:100])  # Fixed: HTML escape
            })

        db.close()

        # Calculate totals
        total_pnl = sum(p.get("pnl_dollar", 0) for p in positions)
        open_count = sum(1 for p in positions if p.get("status") == "OPEN")

        return jsonify({
            "timestamp": now_est.strftime("%m/%d %H:%M:%S EST"),
            "positions": positions,
            "summary": {
                "total_positions": len(positions),
                "open_count": open_count,
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": 0.0
            }
        })
    except Exception as e:
        log.exception(f"Positions error: {e}")
        return jsonify({"error": escape(str(e)), "positions": []}), 500

@app.route("/api/summary")
def get_summary():
    """Get summary stats"""
    try:
        db = get_db()
        now_est = datetime.now(EST)

        # Use indexes for fast count queries
        total = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
        losses = db.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
        open_count = db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]

        closed_pnl = db.execute("SELECT COALESCE(SUM(pnl_dollar), 0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]

        # Fixed: SELECT specific columns + batch fetch prices
        open_trades = db.execute("SELECT id, symbol, direction, entry_price, entry_option_price, status FROM trades WHERE status='OPEN'").fetchall()

        # Collect symbols for batch fetch
        symbols = set(t["symbol"] for t in open_trades)
        prices = get_live_prices_batch(list(symbols))

        open_pnl = 0.0
        for t in open_trades:
            try:
                current_price = prices.get(t["symbol"])
                pnl_pct, pnl_dollar = calculate_pnl(dict(t), current_price)
                open_pnl += pnl_dollar
            except Exception as e:
                log.debug(f"Error calculating open trade P&L: {e}")

        total_pnl = closed_pnl + open_pnl
        db.close()

        win_rate = f"{(wins/total*100):.1f}%" if total > 0 else "0%"

        return jsonify({
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "open_count": open_count,
            "win_rate": win_rate,
            "closed_pnl": round(closed_pnl, 2),
            "open_pnl": round(open_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "timestamp": now_est.strftime("%m/%d %H:%M:%S EST")
        })
    except Exception as e:
        log.exception(f"Summary error: {e}")
        return jsonify({"error": escape(str(e))}), 500

@app.route("/")
def dashboard():
    return render_template_string(HTML)

# Initialize database indexes on startup (idempotent)
_init_db_indexes()

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
    function escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return String(text).replace(/[&<>"']/g, m => map[m]);
    }

    function formatPnl(value) {
        return value >= 0 ? '+$' + value.toFixed(2) : '-$' + Math.abs(value).toFixed(2);
    }

    function loadData() {
        // Load summary
        fetch('/api/summary')
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showError('Error loading summary: ' + escapeHtml(data.error));
                    return;
                }

                let html = '';
                html += '<div class="summary-card"><label>Total P&L</label><div class="value ' + (data.total_pnl >= 0 ? 'positive' : 'negative') + '">' + formatPnl(data.total_pnl) + '</div></div>';
                html += '<div class="summary-card"><label>Open Positions</label><div class="value">' + data.open_count + '</div></div>';
                html += '<div class="summary-card"><label>Wins / Losses</label><div class="value">' + data.win_count + ' / ' + data.loss_count + '</div></div>';
                html += '<div class="summary-card"><label>Win Rate</label><div class="value">' + escapeHtml(data.win_rate) + '</div></div>';

                document.getElementById('summary').innerHTML = html;
                document.getElementById('timestamp').textContent = data.timestamp;
            })
            .catch(err => showError('Error: ' + escapeHtml(err.message)));

        // Load positions
        fetch('/api/positions')
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showError('Error loading positions: ' + escapeHtml(data.error));
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
                        html += '<div class="position-symbol">' + escapeHtml(pos.symbol) + '</div>';
                        html += '<div class="position-meta">' + escapeHtml(pos.expiry) + ' • ' + escapeHtml(pos.entry_time) + ' • ' + pos.days_held + 'd</div>';
                        html += '</div>';

                        // Body
                        html += '<div class="position-body">';

                        if (pos.type === 'SPREAD') {
                            html += '<div class="position-type">IRON CONDOR</div>';
                            html += '<div class="position-legs">';

                            if (pos.legs && pos.legs.length > 0) {
                                pos.legs.forEach(leg => {
                                    html += '<div class="leg">';
                                    html += '<span class="leg-type">' + escapeHtml(leg.type) + ' $' + leg.strike.toFixed(0) + '</span>';
                                    html += '<span class="leg-price">$' + leg.price.toFixed(2) + '</span>';
                                    html += '</div>';
                                });
                            }

                            html += '</div>';
                            html += '<div class="status-badge ' + pos.status.toLowerCase() + '">' + escapeHtml(pos.status) + '</div>';
                        } else {
                            html += '<div class="position-type">' + escapeHtml(pos.direction) + ' $' + pos.strike.toFixed(0) + '</div>';
                            html += '<div style="font-size: 13px; color: #666; margin-bottom: 12px;">Entry: $' + pos.entry_price.toFixed(2) + '</div>';
                            html += '<div class="status-badge ' + pos.status.toLowerCase() + '">' + escapeHtml(pos.status) + '</div>';
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
            .catch(err => showError('Error: ' + escapeHtml(err.message)));
    }

    function showError(msg) {
        let errorDiv = document.createElement('div');
        errorDiv.className = 'error';
        errorDiv.textContent = msg;  // Use textContent instead of innerHTML to prevent XSS
        document.getElementById('error-container').innerHTML = '';
        document.getElementById('error-container').appendChild(errorDiv);
    }

    // Initial load
    loadData();

    // Refresh every 3 seconds
    setInterval(loadData, 3000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import os
    port = int(os.getenv("DASHBOARD_PORT", 3004))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False, threaded=True)
