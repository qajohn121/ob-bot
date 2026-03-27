#!/usr/bin/env python3
import json, logging, math, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf

log     = logging.getLogger("ob.paper_trader")
DB_PATH = Path("/home/ubuntu/ob-bot/data/trades.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
    entry_price REAL NOT NULL, entry_option_price REAL, strike REAL, expiry TEXT,
    call_score INTEGER, put_score INTEGER, direction_score INTEGER,
    sentiment_score REAL, reason TEXT, entry_greeks TEXT,
    rel_volume REAL, pct_change_4h REAL, rsi REAL, iv_pct REAL,
    war_catalyst INTEGER DEFAULT 0, bankruptcy_flag INTEGER DEFAULT 0,
    status TEXT DEFAULT 'OPEN', exit_price REAL, exit_option_price REAL,
    exit_greeks TEXT, pnl_pct REAL, pnl_dollar REAL, outcome_reason TEXT,
    closed_at TEXT, days_held INTEGER, max_price REAL, min_price REAL,
    scan_session TEXT, dte_profile TEXT DEFAULT '30DTE', regime TEXT DEFAULT 'NORMAL',
    predicted_move REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS learning_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at TEXT NOT NULL,
    total_trades INTEGER, win_rate REAL, avg_pnl REAL,
    best_signal TEXT, worst_signal TEXT, adjustments TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS daily_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
    top_calls TEXT, top_puts TEXT, scan_time TEXT
);
CREATE TABLE IF NOT EXISTS autopsy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER, created_at TEXT, symbol TEXT, direction TEXT,
    dte_profile TEXT DEFAULT '30DTE', outcome TEXT, pnl_pct REAL,
    entry_price REAL, exit_price REAL,
    predicted_move REAL DEFAULT 0, actual_move REAL DEFAULT 0,
    iv_entry REAL DEFAULT 0, iv_exit REAL DEFAULT 0, iv_change REAL DEFAULT 0,
    entry_score INTEGER DEFAULT 0,
    lesson TEXT, failed_signal TEXT, regime TEXT DEFAULT 'NORMAL',
    FOREIGN KEY(trade_id) REFERENCES trades(id)
);
CREATE TABLE IF NOT EXISTS recommendation_confidence (
    recommendation_source TEXT PRIMARY KEY,
    confidence INTEGER DEFAULT 50,
    reward_count INTEGER DEFAULT 0,
    punishment_count INTEGER DEFAULT 0,
    last_updated TEXT,
    last_reward_date TEXT,
    last_punishment_date TEXT
);
CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    iv_value REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, recorded_at)
);
"""

MIGRATIONS = [
    "ALTER TABLE trades ADD COLUMN dte_profile TEXT DEFAULT '30DTE'",
    "ALTER TABLE trades ADD COLUMN regime TEXT DEFAULT 'NORMAL'",
    "ALTER TABLE trades ADD COLUMN predicted_move REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN is_spread INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN short_strike REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN long_strike REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN credit_received REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN spread_type TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN recommendation_source TEXT DEFAULT 'manual'",
    "ALTER TABLE trades ADD COLUMN recommendation_rank INTEGER DEFAULT 0",
]

# Reward/Punishment system: confidence scores per recommendation source
_INITIAL_CONFIDENCE = {
    "top_call": 50,
    "top_put": 50,
    "dte_pick": 50,
    "dte_spread": 50,
    "ic_pick": 50,      # Iron Condor picks (medium-confidence setups)
    "manual": 50,
}

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)
    # Run migrations (ignore errors if column already exists)
    for sql in MIGRATIONS:
        try: conn.execute(sql)
        except: pass
    conn.commit(); conn.close()
    log.info(f"DB ready at {DB_PATH}")
    # Initialize recommendation confidence if empty
    _init_confidence()

def _conn():
    c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row; return c

def _atm_price(S, iv, T):
    try:    return max(0.01, S*iv*math.sqrt(T)*0.4)
    except: return max(0.01, S*0.05)

def _next_expiry():
    today=datetime.now(); target=today+timedelta(days=30)
    ms=target.replace(day=1); fridays=[]
    for d in range(1,32):
        try:
            day=ms.replace(day=d)
            if day.weekday()==4: fridays.append(day)
        except: break
    return fridays[2].strftime("%Y-%m-%d") if len(fridays)>=3 else target.strftime("%Y-%m-%d")

def log_trade(d, direction, sentiment=None, session="morning", dte_profile="30DTE", regime="NORMAL", spread_data=None, recommendation_source="manual", recommendation_rank=0):
    sent=sentiment or {}; price=d.get("price",100); iv=d.get("iv",0.30)
    opt_px  = _atm_price(price,iv,30/365); expiry=_next_expiry()
    greeks  = d.get("call_greeks" if direction=="CALL" else "put_greeks",{})
    ds      = d.get("call_score" if direction=="CALL" else "put_score",0)
    reason  = d.get("call_reason" if direction=="CALL" else "put_reason","")
    # Use entry from build_entry if available
    entry_data = d.get("entry_call" if direction=="CALL" else "entry_put",{})
    if entry_data:
        opt_px = entry_data.get("est_option_price", opt_px)
        expiry = entry_data.get("expiry", expiry)
        strike = entry_data.get("strike", price)
        dte_profile = entry_data.get("dte_profile", dte_profile)
    else:
        strike = price
    predicted_move = d.get("move_4h", 0)

    # Spread-specific fields
    is_spread = 1 if spread_data else 0
    short_strike = spread_data.get("short_strike", 0) if spread_data else 0
    long_strike = spread_data.get("long_strike", 0) if spread_data else 0
    credit_received = spread_data.get("credit", 0) if spread_data else 0
    spread_type = spread_data.get("spread_type", "") if spread_data else ""

    conn=_conn(); c=conn.cursor()
    c.execute("""INSERT INTO trades (created_at,symbol,direction,entry_price,entry_option_price,
        strike,expiry,call_score,put_score,direction_score,sentiment_score,reason,entry_greeks,
        rel_volume,pct_change_4h,rsi,iv_pct,war_catalyst,bankruptcy_flag,status,scan_session,
        max_price,min_price,dte_profile,regime,predicted_move,is_spread,short_strike,long_strike,
        credit_received,spread_type,recommendation_source,recommendation_rank)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(),d.get("symbol",""),direction,price,round(opt_px,3),
         round(strike,2),expiry,d.get("call_score",0),d.get("put_score",0),ds,
         sent.get("composite_score",0),reason[:500],json.dumps(greeks),
         d.get("rel_volume",1.0),d.get("move_4h",0),d.get("rsi",50),d.get("iv_pct",30),
         int(sent.get("has_war_catalyst",False)),int(sent.get("has_bankruptcy",False)),
         "OPEN",session,price,price,dte_profile,regime,predicted_move,
         is_spread,short_strike,long_strike,credit_received,spread_type,recommendation_source,recommendation_rank))
    tid=c.lastrowid; conn.commit(); conn.close()
    trade_type = f"{spread_type} spread" if is_spread else f"{direction} option"
    log.info(f"Logged #{tid}: {d.get('symbol')} {trade_type} ({recommendation_source}#{recommendation_rank}) {dte_profile}")

    # Send Telegram entry alert (async, non-blocking)
    try:
        from trade_alerts import send_trade_alert
        import asyncio
        # Prepare alert data
        alert_data = {
            "strategy": spread_type if is_spread else direction,
            "entry_price": round(price, 2),
            "strike": round(strike, 2),
            "expiry": expiry,
            "credit": round(credit_received, 2) if is_spread else None,
            "reason": reason,
            "iv_rank": d.get("ivr"),
            "pre_market_gap": d.get("premarket_gap_pct"),
        }
        if is_spread:
            alert_data["profit_zone_low"] = short_strike
            alert_data["profit_zone_high"] = long_strike
            alert_data["max_loss"] = spread_data.get("max_loss", 0)
            alert_data["pop"] = spread_data.get("pop", "~80-85%")
        else:
            alert_data["target"] = d.get("move_4h", 0) * price if d.get("move_4h") else None
            alert_data["stop"] = price * 0.95  # Simple 5% stop

        # Send alert asynchronously
        asyncio.run(send_trade_alert("ENTRY", d.get("symbol", ""), **alert_data))
    except Exception as e:
        log.debug(f"Trade alert send error: {e}")

    return tid

def log_iron_condor(d, ic_data, session="morning", dte_profile="30DTE", regime="NORMAL", recommendation_source="ic_pick", recommendation_rank=0):
    """
    Log an Iron Condor trade (two spreads combined: call spread + put spread).
    IC data contains both spreads' information.
    """
    price = d.get("price", 100)
    iv = d.get("iv", 0.30)
    symbol = d.get("symbol", "")
    call_score = d.get("call_score", 50)
    put_score = d.get("put_score", 50)
    avg_score = round((call_score + put_score) / 2)

    # IC expiry from dte_profile mapping
    expiry_map = {
        "0DTE": _today_expiry(),
        "7DTE": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        "21DTE": (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d"),
        "30DTE": _next_expiry(),
        "60DTE": (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),
    }
    expiry = expiry_map.get(dte_profile, _next_expiry())

    # IC-specific fields
    total_credit = ic_data.get("total_credit", 0)
    max_loss = ic_data.get("max_loss", 0)
    call_short_strike = ic_data.get("call_short_strike", 0)
    call_long_strike = ic_data.get("call_long_strike", 0)
    put_short_strike = ic_data.get("put_short_strike", 0)
    put_long_strike = ic_data.get("put_long_strike", 0)

    # Store IC as special "IC" direction with comprehensive strike info in spread fields
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO trades (created_at,symbol,direction,entry_price,entry_option_price,
           strike,expiry,call_score,put_score,direction_score,sentiment_score,reason,entry_greeks,
           rel_volume,pct_change_4h,rsi,iv_pct,war_catalyst,bankruptcy_flag,status,scan_session,
           max_price,min_price,dte_profile,regime,predicted_move,is_spread,short_strike,long_strike,
           credit_received,spread_type,recommendation_source,recommendation_rank)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(), symbol, "IC", price, total_credit,
         call_short_strike, expiry, call_score, put_score, avg_score,
         50.0, f"Iron Condor: CS {call_short_strike}/{call_long_strike} | PS {put_short_strike}/{put_long_strike}", "{}",
         d.get("rel_volume", 1.0), d.get("move_4h", 0), d.get("rsi", 50), d.get("iv_pct", 30),
         0, 0, "OPEN", session, price, price, dte_profile, regime, d.get("move_4h", 0),
         1, put_short_strike, put_long_strike, total_credit, "IRON CONDOR", recommendation_source, recommendation_rank)
    )
    tid = c.lastrowid
    conn.commit()
    conn.close()
    log.info(f"Logged #{tid}: {symbol} IRON CONDOR ({recommendation_source}#{recommendation_rank}) {dte_profile} | Credit: ${total_credit:.2f} Max Loss: ${max_loss:.2f}")

    # Send Telegram entry alert for Iron Condor (async, non-blocking)
    try:
        from trade_alerts import send_trade_alert
        import asyncio
        asyncio.run(send_trade_alert(
            "ENTRY",
            symbol,
            strategy="IRON_CONDOR",
            entry_price=round(price, 2),
            credit=round(total_credit, 2),
            profit_zone_low=put_short_strike,
            profit_zone_high=call_short_strike,
            max_loss=round(max_loss, 2),
            pop=ic_data.get("pop", "~80-85%"),
            iv_rank=d.get("ivr"),
            pre_market_gap=d.get("premarket_gap_pct"),
            reason=f"Iron Condor: CS {call_short_strike}/{call_long_strike} | PS {put_short_strike}/{put_long_strike}"
        ))
    except Exception as e:
        log.debug(f"IC entry alert send error: {e}")

    return tid

# ── Autopsy: rule-based analysis of why a trade won or lost ──────────────────
def _generate_autopsy_lesson(trade, pnl_pct, cur_price, cur_iv_pct):
    direction   = trade["direction"]
    entry_price = trade["entry_price"]
    iv_entry    = trade.get("iv_pct") or 30
    iv_exit     = cur_iv_pct
    iv_change   = round(iv_exit - iv_entry, 1)
    actual_move_stock = round((cur_price - entry_price) / entry_price * 100, 2)
    actual_move = actual_move_stock if direction=="CALL" else -actual_move_stock
    predicted   = trade.get("predicted_move", 0) or trade.get("pct_change_4h", 0) or 0
    outcome     = "WIN" if pnl_pct > 0 else "LOSS"
    dte_profile = trade.get("dte_profile","30DTE")
    score       = trade.get("direction_score",0)
    lessons=[]; failed=[]

    if outcome == "LOSS":
        # Stock moved right way but option still lost = theta/IV problem
        if actual_move > 0 and pnl_pct < -15:
            lessons.append(f"Stock moved right (+{actual_move:.1f}%) but option lost — IV crush or theta decay")
            failed.append("iv_timing")
        # Wrong direction entirely
        elif actual_move < 0:
            lessons.append(f"Directional miss — predicted move but stock went {actual_move:.1f}%")
            failed.append("momentum")
        # IV crushed
        if iv_change < -8:
            lessons.append(f"IV dropped {abs(iv_change):.0f}pts after entry (entered high-IV day)")
            failed.append("iv_rank")
        # Held too long
        days_held = trade.get("days_held",0) or 0
        if days_held > 5 and dte_profile in ("0DTE","7DTE"):
            lessons.append(f"Held {days_held} days on {dte_profile} trade — theta burned it")
            failed.append("hold_time")
        # Low score trade
        if score < 55:
            lessons.append(f"Low conviction entry (score {score}) — raise minimum threshold")
            failed.append("low_score")
    else:  # WIN
        if actual_move > abs(predicted)*1.5:
            lessons.append(f"Strong follow-through (+{actual_move:.1f}%) — momentum signal confirmed")
        elif iv_change > 5:
            lessons.append(f"IV expansion +{iv_change:.0f}pts amplified gains — good timing")
        else:
            lessons.append("Solid execution — thesis played out as expected")
        if score >= 70:
            lessons.append(f"High-conviction setup (score {score}) delivered")

    return {
        "actual_move":   round(actual_move, 2),
        "iv_entry":      round(iv_entry, 1),
        "iv_exit":       round(iv_exit, 1),
        "iv_change":     iv_change,
        "lesson":        " | ".join(lessons) if lessons else ("Trade closed at breakeven" if pnl_pct==0 else "Unknown"),
        "failed_signal": ", ".join(failed) if failed else "none",
    }

def write_autopsy(trade_id, pnl_pct, cur_price, outcome, regime="NORMAL"):
    try:
        conn = _conn()
        t    = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        if not t: return
        t = dict(t)
        # Try to get current IV
        cur_iv_pct = t.get("iv_pct") or 30
        try:
            tk   = yf.Ticker(t["symbol"])
            exps = tk.options
            if exps:
                price     = cur_price
                best_exp  = exps[0]
                chain     = tk.option_chain(best_exp)
                atm       = chain.calls[abs(chain.calls["strike"]-price)<price*0.07]
                if not atm.empty:
                    iv_vals = atm["impliedVolatility"].dropna()
                    if len(iv_vals)>0: cur_iv_pct = round(float(iv_vals.mean())*100,1)
        except: pass
        a = _generate_autopsy_lesson(t, pnl_pct, cur_price, cur_iv_pct)
        conn2 = _conn()
        conn2.execute("""INSERT INTO autopsy
            (trade_id,created_at,symbol,direction,dte_profile,outcome,pnl_pct,
             entry_price,exit_price,predicted_move,actual_move,
             iv_entry,iv_exit,iv_change,entry_score,lesson,failed_signal,regime)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, datetime.now().isoformat(), t["symbol"], t["direction"],
             t.get("dte_profile","30DTE"), outcome, round(pnl_pct,2),
             t["entry_price"], round(cur_price,2),
             t.get("predicted_move",0), a["actual_move"],
             a["iv_entry"], a["iv_exit"], a["iv_change"],
             t.get("direction_score",0), a["lesson"][:500], a["failed_signal"], regime))
        conn2.commit(); conn2.close()
        log.info(f"Autopsy #{trade_id}: {outcome} {pnl_pct:.1f}% — {a['lesson'][:80]}")
    except Exception as e:
        log.error(f"write_autopsy #{trade_id}: {e}")

def get_lessons(limit=20):
    """Return recent autopsy lessons for dashboard display."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM autopsy ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        conn.close(); return rows
    except: return []

def get_signal_accuracy():
    """Return win rate grouped by which signal failed."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT failed_signal, outcome, COUNT(*) as cnt FROM autopsy GROUP BY failed_signal, outcome"
        ).fetchall()]
        conn.close()
        signals={}
        for r in rows:
            s = r["failed_signal"] or "none"
            if s not in signals: signals[s]={"win":0,"loss":0}
            if r["outcome"]=="WIN": signals[s]["win"]+=r["cnt"]
            else:                   signals[s]["loss"]+=r["cnt"]
        result=[]
        for s,v in signals.items():
            total=v["win"]+v["loss"]
            if total>0:
                result.append({"signal":s,"total":total,"win_rate":round(v["win"]/total*100,1)})
        return sorted(result, key=lambda x:x["total"], reverse=True)
    except: return []

def _est_current_opt(S_now, S_entry, T, iv, direction):
    try:
        move = (S_now-S_entry)/S_entry
        if direction=="PUT": move=-move
        tval     = _atm_price(S_now,iv,T); intrinsic=max(0,S_now*move)
        return max(0.01, tval+intrinsic*0.5)
    except: return _atm_price(S_now,iv,T)

def check_open_trades(regime="NORMAL"):
    conn   = _conn()
    trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    conn.close(); closed=[]
    for t in trades:
        try:
            tk   = yf.Ticker(t["symbol"]); hist=tk.history(period="1d",interval="5m")
            if hist.empty: continue
            cur  = float(hist["Close"].iloc[-1])
            conn2= _conn()
            conn2.execute("UPDATE trades SET max_price=MAX(max_price,?),min_price=MIN(min_price,?) WHERE id=?",
                          (cur,cur,t["id"])); conn2.commit(); conn2.close()
            iv   = (t["iv_pct"] or 30)/100
            exp_str=t.get("expiry","")
            T    = max(0.001,(datetime.strptime(exp_str,"%Y-%m-%d")-datetime.now()).days/365) if exp_str else 20/365
            cur_opt   = _est_current_opt(cur,t["entry_price"],T,iv,t["direction"])
            entry_opt = t.get("entry_option_price") or 0
            if entry_opt>0:
                pnl_pct   = round((cur_opt-entry_opt)/entry_opt*100,1)
                pnl_dollar= round((cur_opt-entry_opt)*100,2)
            else:
                pnl_pct   = round((cur-t["entry_price"])/t["entry_price"]*(1 if t["direction"]=="CALL" else -1)*200,1)
                pnl_dollar= pnl_pct
            days_held = (datetime.now()-datetime.fromisoformat(t["created_at"])).days
            expired   = exp_str and datetime.strptime(exp_str,"%Y-%m-%d").date()<datetime.now().date()
            # DTE-profile specific stop rules
            dte_profile = t.get("dte_profile","30DTE") or "30DTE"
            stop_pct = {"0DTE":-25,"7DTE":-40,"21DTE":-50,"30DTE":-50,"60DTE":-60}.get(dte_profile,-50)
            should_close=False; outcome="OPEN"; reason=""
            if expired:                    should_close=True; outcome="WIN" if pnl_pct>0 else "LOSS"; reason="Expired"
            elif pnl_pct>=80:              should_close=True; outcome="WIN";  reason=f"TP +{pnl_pct:.0f}%"
            elif pnl_pct<=stop_pct:        should_close=True; outcome="LOSS"; reason=f"SL {pnl_pct:.0f}%"
            elif dte_profile=="0DTE" and days_held>=1: should_close=True; outcome="WIN" if pnl_pct>0 else "LOSS"; reason="0DTE expired"
            if should_close:
                conn3=_conn(); conn3.execute(
                    "UPDATE trades SET status=?,exit_price=?,exit_option_price=?,pnl_pct=?,pnl_dollar=?,outcome_reason=?,closed_at=?,days_held=? WHERE id=?",
                    (outcome,round(cur,2),round(cur_opt,3),pnl_pct,pnl_dollar,reason,datetime.now().isoformat(),days_held,t["id"]))
                conn3.commit(); conn3.close()
                write_autopsy(t["id"], pnl_pct, cur, outcome, regime)
                # Update recommendation source confidence
                rec_source = t.get("recommendation_source") or "manual"
                update_recommendation_confidence(rec_source, outcome, pnl_pct)
                closed.append({"id":t["id"],"symbol":t["symbol"],"direction":t["direction"],
                                "dte_profile":dte_profile,"pnl_pct":pnl_pct,"pnl_dollar":pnl_dollar,
                                "outcome":outcome,"reason":reason})
                log.info(f"Closed #{t['id']} {t['symbol']} {t['direction']} {dte_profile}: {outcome} {pnl_pct:.1f}%")

                # Send Telegram exit alert (async, non-blocking)
                try:
                    from trade_alerts import send_trade_alert
                    import asyncio
                    asyncio.run(send_trade_alert(
                        "EXIT",
                        t["symbol"],
                        exit_type=outcome,
                        exit_price=round(cur, 2),
                        pnl_pct=pnl_pct,
                        pnl_dollars=pnl_dollar,
                        days_held=days_held,
                        reason=reason
                    ))
                except Exception as e:
                    log.debug(f"Exit alert send error: {e}")
        except Exception as e: log.error(f"check #{t['id']}: {e}")
    return closed

def get_performance_stats():
    conn=_conn()
    total   =conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    open_cnt=conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
    wins    =conn.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
    losses  =conn.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
    closed  =[dict(r) for r in conn.execute("SELECT * FROM trades WHERE status IN ('WIN','LOSS') ORDER BY closed_at DESC LIMIT 50").fetchall()]
    open_list=[dict(r) for r in conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC").fetchall()]
    pnls    =[t["pnl_pct"] for t in closed if t.get("pnl_pct") is not None]
    total_pnl=sum(t.get("pnl_dollar",0) or 0 for t in closed)
    avg_pnl =round(sum(pnls)/len(pnls),1) if pnls else 0
    win_p   =[p for p in pnls if p>0]; los_p=[p for p in pnls if p<=0]
    avg_win =round(sum(win_p)/len(win_p),1) if win_p else 0
    avg_loss=round(sum(los_p)/len(los_p),1) if los_p else 0
    win_rate=round(wins/max(wins+losses,1)*100,1)
    ev      =round((win_rate/100*avg_win)+((1-win_rate/100)*avg_loss),1)
    def ds(direction):
        rows=[r for r in closed if r["direction"]==direction]
        if not rows: return {"trades":0,"win_rate":0,"avg_pnl":0}
        w=sum(1 for r in rows if r["status"]=="WIN"); p=[r["pnl_pct"] for r in rows if r.get("pnl_pct")]
        return {"trades":len(rows),"win_rate":round(w/len(rows)*100,1),"avg_pnl":round(sum(p)/len(p),1) if p else 0}
    def by_dte(profile):
        rows=[r for r in closed if (r.get("dte_profile") or "30DTE")==profile]
        if not rows: return {"trades":0,"win_rate":0,"avg_pnl":0,"ev":0}
        w=sum(1 for r in rows if r["status"]=="WIN"); p=[r["pnl_pct"] for r in rows if r.get("pnl_pct")]
        wr=round(w/len(rows)*100,1); ap=round(sum(p)/len(p),1) if p else 0
        wp=[x for x in p if x>0]; lp=[x for x in p if x<=0]
        aw=round(sum(wp)/len(wp),1) if wp else 0; al=round(sum(lp)/len(lp),1) if lp else 0
        ev=round((wr/100*aw)+((1-wr/100)*al),1)
        return {"trades":len(rows),"win_rate":wr,"avg_pnl":ap,"ev":ev}
    conn.close()
    return {
        "total_trades":total,"open_trades":open_cnt,"wins":wins,"losses":losses,
        "win_rate":win_rate,"avg_pnl_pct":avg_pnl,"total_pnl_dollar":round(total_pnl,2),
        "avg_win_pct":avg_win,"avg_loss_pct":avg_loss,"expectancy":ev,
        "call_stats":ds("CALL"),"put_stats":ds("PUT"),
        "dte_stats":{p:by_dte(p) for p in ["0DTE","7DTE","21DTE","30DTE","60DTE"]},
        "recent_trades":closed[:10],"open_positions":open_list,
    }

def get_performance_by_recommendation_source():
    """Get win rate and EV breakdown by recommendation source (top_call, top_put, dte_pick, etc)."""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT recommendation_source, status, pnl_pct, recommendation_rank FROM trades "
            "WHERE status IN ('WIN','LOSS') AND recommendation_source IS NOT NULL"
        ).fetchall()]
        conn.close()

        sources = {}
        for r in rows:
            src = r["recommendation_source"]
            if src not in sources:
                sources[src] = {"wins": 0, "losses": 0, "pnl": []}
            if r["status"] == "WIN":
                sources[src]["wins"] += 1
            else:
                sources[src]["losses"] += 1
            pnl = r["pnl_pct"] or 0
            sources[src]["pnl"].append(pnl)

        result = {}
        for src, data in sources.items():
            total = data["wins"] + data["losses"]
            wr = round(data["wins"] / max(total, 1) * 100, 1)
            pnl_list = data["pnl"]
            avg_pnl = round(sum(pnl_list) / len(pnl_list), 1) if pnl_list else 0
            wp = [x for x in pnl_list if x > 0]
            lp = [x for x in pnl_list if x <= 0]
            aw = round(sum(wp) / len(wp), 1) if wp else 0
            al = round(sum(lp) / len(lp), 1) if lp else 0
            ev = round((wr / 100 * aw) + ((1 - wr / 100) * al), 1)
            result[src] = {
                "trades": total, "wins": data["wins"], "losses": data["losses"],
                "win_rate": wr, "avg_pnl": avg_pnl, "avg_win": aw, "avg_loss": al, "ev": ev
            }
        return result
    except Exception as e:
        log.error(f"get_performance_by_recommendation_source: {e}")
        return {}

def get_todays_trades():
    """Get all trades created TODAY, regardless of status (OPEN, WIN, LOSS)."""
    from datetime import datetime, timedelta
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _conn()
        trades = [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE DATE(created_at)=? ORDER BY created_at DESC",
            (today_str,)
        ).fetchall()]
        conn.close()
        return trades
    except Exception as e:
        log.error(f"get_todays_trades: {e}")
        return []

def get_open_trades_with_pnl():
    conn=_conn(); trades=[dict(r) for r in conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC").fetchall()]; conn.close()
    result=[]
    for t in trades:
        item=dict(t)
        try:
            tk=yf.Ticker(t["symbol"]); hist=tk.history(period="1d",interval="5m")
            if not hist.empty:
                cur=float(hist["Close"].iloc[-1]); item["current_price"]=round(cur,2)
                item["price_change_pct"]=round((cur-t["entry_price"])/t["entry_price"]*100,2)
                iv=(t["iv_pct"] or 30)/100; exp_str=t.get("expiry","")
                T=max(0.001,(datetime.strptime(exp_str,"%Y-%m-%d")-datetime.now()).days/365) if exp_str else 20/365
                cur_opt=_est_current_opt(cur,t["entry_price"],T,iv,t["direction"])
                entry_opt=t.get("entry_option_price") or 0
                if entry_opt>0:
                    item["option_pnl_pct"]=round((cur_opt-entry_opt)/entry_opt*100,1)
                    item["option_pnl_dollar"]=round((cur_opt-entry_opt)*100,2)
                else: item["option_pnl_pct"]=0; item["option_pnl_dollar"]=0
                item["current_option_price"]=round(cur_opt,3)
        except: item["current_price"]=t["entry_price"]; item["option_pnl_pct"]=0; item["option_pnl_dollar"]=0
        result.append(item)
    return result

# ── Reward/Punishment System ───────────────────────────────────────────────────
def _init_confidence():
    """Initialize confidence table with default values for each recommendation source."""
    conn = _conn()
    for source, initial_conf in _INITIAL_CONFIDENCE.items():
        try:
            conn.execute(
                "INSERT OR IGNORE INTO recommendation_confidence (recommendation_source, confidence, reward_count, punishment_count, last_updated) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, initial_conf, 0, 0, datetime.now().isoformat())
            )
        except: pass
    conn.commit(); conn.close()

def get_recommendation_confidence():
    """Return confidence scores for all recommendation sources."""
    _init_confidence()  # Ensure table is initialized
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM recommendation_confidence ORDER BY recommendation_source"
        ).fetchall()]
        conn.close()
        result = {}
        for r in rows:
            result[r["recommendation_source"]] = {
                "confidence": r["confidence"],
                "reward_count": r["reward_count"],
                "punishment_count": r["punishment_count"],
                "last_updated": r["last_updated"],
            }
        # Ensure all sources are present with defaults
        for source in _INITIAL_CONFIDENCE:
            if source not in result:
                result[source] = {"confidence": 50, "reward_count": 0, "punishment_count": 0, "last_updated": ""}
        return result
    except Exception as e:
        log.error(f"get_recommendation_confidence: {e}")
        return {s: {"confidence": 50, "reward_count": 0, "punishment_count": 0, "last_updated": ""}
                for s in _INITIAL_CONFIDENCE}

def update_recommendation_confidence(source, outcome, pnl_pct):
    """
    Update confidence for a recommendation source based on trade outcome.
    Reward winners more than we punish losers to encourage trying new strategies.
    """
    if source not in _INITIAL_CONFIDENCE:
        return

    pnl_pct = pnl_pct or 0
    try:
        conn = _conn()
        current = conn.execute(
            "SELECT confidence, reward_count, punishment_count FROM recommendation_confidence WHERE recommendation_source=?",
            (source,)
        ).fetchone()

        if not current:
            _init_confidence()
            current = conn.execute(
                "SELECT confidence, reward_count, punishment_count FROM recommendation_confidence WHERE recommendation_source=?",
                (source,)
            ).fetchone()

        conf = current["confidence"]
        rewards = current["reward_count"]
        punishments = current["punishment_count"]

        if outcome == "WIN":
            # Scale reward by profitability
            if pnl_pct > 50:
                delta = 5
            elif pnl_pct > 30:
                delta = 4
            elif pnl_pct > 10:
                delta = 3
            else:  # 0-10% win
                delta = 1
            conf = min(100, conf + delta)
            rewards += 1
            last_date_field = "last_reward_date"
        else:  # LOSS
            # Scale punishment by loss magnitude
            if pnl_pct < -50:
                delta = -10
            elif pnl_pct < -30:
                delta = -8
            elif pnl_pct < -10:
                delta = -5
            else:  # -10 to 0% loss
                delta = -2
            conf = max(0, conf + delta)
            punishments += 1
            last_date_field = "last_punishment_date"

        conn.execute(
            "UPDATE recommendation_confidence SET confidence=?, reward_count=?, punishment_count=?, last_updated=?, "+last_date_field+"=? "
            "WHERE recommendation_source=?",
            (conf, rewards, punishments, datetime.now().isoformat(), datetime.now().isoformat(), source)
        )
        conn.commit(); conn.close()
        log.info(f"Updated {source} confidence: {current['confidence']}→{conf} ({outcome} {pnl_pct:+.1f}%)")
    except Exception as e:
        log.error(f"update_recommendation_confidence {source}: {e}")

def get_confidence_change_today(source):
    """Return the confidence change for a source TODAY (for EOD report)."""
    try:
        conn = _conn()
        today_str = datetime.now().strftime("%Y-%m-%d")
        # Count today's wins and losses for this source
        closed_today = [dict(r) for r in conn.execute(
            "SELECT outcome, pnl_pct FROM trades WHERE DATE(closed_at)=? AND recommendation_source=? AND status IN ('WIN','LOSS')",
            (today_str, source)
        ).fetchall()]
        conn.close()

        if not closed_today:
            return {"change": 0, "wins": 0, "losses": 0, "summary": "No trades closed yet"}

        wins = sum(1 for t in closed_today if t["outcome"] == "WIN")
        losses = sum(1 for t in closed_today if t["outcome"] == "LOSS")

        # Calculate what the change would have been
        change = 0
        for t in closed_today:
            pnl = t["pnl_pct"] or 0
            if t["outcome"] == "WIN":
                if pnl > 50:
                    change += 5
                elif pnl > 30:
                    change += 4
                elif pnl > 10:
                    change += 3
                else:
                    change += 1
            else:
                if pnl < -50:
                    change -= 10
                elif pnl < -30:
                    change -= 8
                elif pnl < -10:
                    change -= 5
                else:
                    change -= 2

        summary = f"{wins}W/{losses}L (change {change:+d})"
        return {"change": change, "wins": wins, "losses": losses, "summary": summary}
    except Exception as e:
        log.error(f"get_confidence_change_today {source}: {e}")
        return {"change": 0, "wins": 0, "losses": 0, "summary": "Error"}

# ── IV Rank (IVR) System ───────────────────────────────────────────────────
def store_iv_snapshot(symbol, iv_value):
    """Store IV snapshot for a ticker. Keep rolling 365-day window."""
    try:
        conn = _conn()
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute("INSERT INTO iv_history (symbol, iv_value, recorded_at) VALUES (?, ?, ?)",
                  (symbol, iv_value, now))
        # Purge rows older than 365 days
        cutoff = (datetime.now() - timedelta(days=365)).isoformat()
        c.execute("DELETE FROM iv_history WHERE symbol=? AND recorded_at<?", (symbol, cutoff))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"store_iv_snapshot {symbol}: {e}")

def get_iv_rank(symbol, current_iv):
    """
    Compute IV Rank: where current IV stands in symbol's 52-week range.
    IVR = (current_IV - min_IV) / (max_IV - min_IV) * 100
    Returns dict with 'ivr' (0-100), 'days_history', and 'signal' ('HIGH'/'NORMAL'/'LOW').
    """
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT iv_value FROM iv_history WHERE symbol=? AND recorded_at >= datetime('now', '-365 days') ORDER BY recorded_at ASC",
            (symbol,)
        ).fetchall()
        conn.close()

        if not rows or len(rows) < 10:
            return {"ivr": None, "days_history": len(rows), "signal": "INSUFFICIENT_HISTORY"}

        iv_values = [r[0] for r in rows]
        min_iv = min(iv_values)
        max_iv = max(iv_values)

        if max_iv == min_iv:
            ivr = 50.0
        else:
            ivr = round(((current_iv - min_iv) / (max_iv - min_iv)) * 100, 1)
            ivr = max(0, min(100, ivr))

        # Map to signal
        signal = "HIGH" if ivr > 60 else ("LOW" if ivr < 30 else "NORMAL")
        return {"ivr": ivr, "days_history": len(rows), "signal": signal}
    except Exception as e:
        log.debug(f"get_iv_rank {symbol}: {e}")
        return {"ivr": None, "days_history": 0, "signal": "ERROR"}

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO); init_db()
    s=get_performance_stats()
    print(f"Trades: {s['total_trades']} | WR: {s['win_rate']}% | EV: {s['expectancy']:+.1f}%")
    print(f"DTE Stats: {json.dumps(s['dte_stats'],indent=2)}")
    lessons=get_lessons(5)
    print(f"\nRecent Lessons ({len(lessons)}):")
    for l in lessons: print(f"  {l['symbol']} {l['outcome']} {l['pnl_pct']:+.1f}%: {l['lesson'][:80]}")

def get_trade_alert_data(trade_id: int) -> dict:
    """Return formatted alert data for a trade (for Telegram notifications)."""
    conn = _conn()
    trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    conn.close()
    if not trade:
        return {}

    t = dict(trade)
    if t["status"] == "OPEN":
        return {
            "alert_type": "ENTRY",
            "symbol": t["symbol"],
            "direction": t["direction"],
            "entry_price": t["entry_price"],
            "entry_option_price": t["entry_option_price"],
            "strike": t["strike"],
            "expiry": t["expiry"],
            "reason": t["reason"],
            "spread_type": t.get("spread_type", ""),
            "credit_received": t.get("credit_received", 0),
            "dte_profile": t.get("dte_profile", "30DTE"),
            "regime": t.get("regime", "NORMAL"),
        }
    elif t["status"] in ("WIN", "LOSS"):
        return {
            "alert_type": "EXIT",
            "symbol": t["symbol"],
            "direction": t["direction"],
            "entry_price": t["entry_price"],
            "exit_price": t["exit_price"],
            "pnl_pct": t["pnl_pct"],
            "pnl_dollars": t["pnl_dollar"],
            "outcome": t["status"],
            "outcome_reason": t["outcome_reason"],
            "days_held": t.get("days_held", 0),
        }
    return {}

def get_open_trades_alert_summary() -> str:
    """Return text summary of all open trades for Telegram update."""
    conn = _conn()
    trades = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY created_at DESC").fetchall()
    conn.close()

    if not trades:
        return "📭 No open trades"

    lines = [f"📊 <b>Open Trades ({len(trades)})</b>"]
    for t in trades:
        t = dict(t)
        pnl_indicator = "📈" if (t.get("max_price", 0) - t["entry_price"]) > 0 else "📉"
        lines.append(f"{pnl_indicator} <b>{t['symbol']}</b> | {t['direction']} @ ${t['entry_price']:.2f} | {t['dte_profile']}")

    return "\n".join(lines)
