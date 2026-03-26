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

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)
    # Run migrations (ignore errors if column already exists)
    for sql in MIGRATIONS:
        try: conn.execute(sql)
        except: pass
    conn.commit(); conn.close()
    log.info(f"DB ready at {DB_PATH}")

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
    log.info(f"Logged #{tid}: {d.get('symbol')} {trade_type} ({recommendation_source}#{recommendation_rank}) {dte_profile}"); return tid

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
                closed.append({"id":t["id"],"symbol":t["symbol"],"direction":t["direction"],
                                "dte_profile":dte_profile,"pnl_pct":pnl_pct,"pnl_dollar":pnl_dollar,
                                "outcome":outcome,"reason":reason})
                log.info(f"Closed #{t['id']} {t['symbol']} {t['direction']} {dte_profile}: {outcome} {pnl_pct:.1f}%")
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

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO); init_db()
    s=get_performance_stats()
    print(f"Trades: {s['total_trades']} | WR: {s['win_rate']}% | EV: {s['expectancy']:+.1f}%")
    print(f"DTE Stats: {json.dumps(s['dte_stats'],indent=2)}")
    lessons=get_lessons(5)
    print(f"\nRecent Lessons ({len(lessons)}):")
    for l in lessons: print(f"  {l['symbol']} {l['outcome']} {l['pnl_pct']:+.1f}%: {l['lesson'][:80]}")
