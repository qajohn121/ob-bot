#!/usr/bin/env python3
import json, logging, math, time
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from vol_analysis import hv_iv_analysis

# Graceful fallback for market intelligence and sentiment imports
try:
    from market_intel import get_market_intel as _get_market_intel
except ImportError:
    def _get_market_intel():
        return {"market_bias": "NEUTRAL", "context_summary": "", "wsb_trending": []}

try:
    from sentiment import get_social_sentiment as _get_social_sentiment
except ImportError:
    def _get_social_sentiment(symbol, wsb_data=None):
        return {"composite_social_score": 50.0, "is_bearish_social": False, "is_bullish_social": False}

try:
    from paper_trader import get_recommendation_confidence as _get_recommendation_confidence
except ImportError:
    def _get_recommendation_confidence():
        return {s: {"confidence": 50} for s in ["top_call", "top_put", "dte_pick", "dte_spread", "manual"]}

try:
    from paper_trader import store_iv_snapshot as _store_iv_snapshot, get_iv_rank as _get_iv_rank
except ImportError:
    def _store_iv_snapshot(symbol, iv): pass
    def _get_iv_rank(symbol, iv): return {"ivr": None, "signal": "INSUFFICIENT_HISTORY"}

# Suppress noisy yfinance/urllib3 HTTP error prints
for _noisy in ("yfinance","urllib3","peewee","yfinance.utils","yfinance.base"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

log = logging.getLogger("ob.scanner")

SCAN_TICKERS = [
    # TIER 1: Mega-cap tech — institutional liquidity, tight spreads, high IV
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN",
    # TIER 2: Quality financials — steady premium, low-slippage
    "JPM","V","MA","UNH",
    # TIER 3: Sector leaders — institutional options volume
    "TSLA","CRM","AVGO",
    # TIER 4: Broad market ETFs — liquid, institutional-grade
    "SPY","QQQ",
    # TOTAL: 15 stocks (focused, high-quality, liquid for spreads)
]

WAR_TICKERS   = {"LMT","RTX","NOC","GD","HII","BAH","CACI","SAIC","XOM","CVX","COP","OXY","SLB","HAL","BKR","USO","GLD","NEM","WPM","GE"}
DISTRESSED_TICKERS = {"GME","AMC","BBAI","LCID","RIVN","SNAP","HOOD","MARA","RIOT","CLSK","HUT","BTBT","ARKK"}
# ETFs have no fundamentals — skip .info for these
ETF_TICKERS = {"SPY","QQQ","IWM","GLD","TLT","XLF","XLE","XLK","ARKK","SOXL","USO","XBI","SMH","DIA","VXX"}
# Tech & growth tickers for market context boosts (used in multiple places)
TECH_TICKERS = {"NVDA","AMD","PLTR","TSLA","AMZN","GOOGL","META","MSFT","AAPL","SMCI","AVGO","ARM","MRVL"}

# ── Market Regime ─────────────────────────────────────────────────────────────
def get_market_regime():
    """Returns regime dict based on VIX level."""
    try:
        vix = yf.Ticker("^VIX")
        h   = vix.history(period="5d", interval="1d")
        if not h.empty:
            v = round(float(h["Close"].iloc[-1]), 1)
            if   v > 35: return {"regime":"VOLATILE", "vix":v, "bias":"PUTS",  "size_mult":0.5,  "note":"Extreme fear — small size only, favor puts"}
            elif v > 25: return {"regime":"CAUTION",  "vix":v, "bias":"PUTS",  "size_mult":0.7,  "note":"Elevated VIX — reduce size, watch support"}
            elif v > 18: return {"regime":"NORMAL",   "vix":v, "bias":"BOTH",  "size_mult":1.0,  "note":"Normal market — use full scoring"}
            elif v > 12: return {"regime":"BULL",     "vix":v, "bias":"CALLS", "size_mult":1.0,  "note":"Low fear — calls favored, trend following"}
            else:        return {"regime":"COMPLACENT","vix":v,"bias":"CALLS", "size_mult":0.8,  "note":"Very low VIX — complacency, watch for reversal"}
    except Exception as e:
        log.debug(f"VIX fetch: {e}")
    return {"regime":"UNKNOWN", "vix":0, "bias":"BOTH", "size_mult":1.0, "note":"VIX unavailable"}

def get_market_context():
    """Intraday QQQ/SPY momentum — detects broad tech selloffs and rallies."""
    try:
        qqq = yf.Ticker("QQQ").history(period="5d", interval="1h")
        spy = yf.Ticker("SPY").history(period="5d", interval="1h")
        if qqq.empty or spy.empty:
            return {"qqq_4h": 0, "spy_4h": 0, "tech_selloff": False, "broad_selloff": False, "rally_day": False}
        qqq_4h = (qqq["Close"].iloc[-1] / qqq["Close"].iloc[-5] - 1) * 100 if len(qqq) >= 5 else 0
        spy_4h = (spy["Close"].iloc[-1] / spy["Close"].iloc[-5] - 1) * 100 if len(spy) >= 5 else 0
    except Exception as e:
        log.debug(f"Market context fetch: {e}")
        return {"qqq_4h": 0, "spy_4h": 0, "tech_selloff": False, "broad_selloff": False, "rally_day": False}
    return {
        "qqq_4h": round(qqq_4h, 2),
        "spy_4h": round(spy_4h, 2),
        "tech_selloff": qqq_4h < -1.5,       # QQQ down 1.5%+ intraday = tech selling
        "broad_selloff": spy_4h < -1.5,      # broad market selling
        "rally_day": qqq_4h > 1.5 and spy_4h > 1.0,
    }

# ── Black-Scholes helpers ─────────────────────────────────────────────────────
def _bs_delta(S,K,T,r,sigma,opt="call"):
    try:
        if T<=0 or sigma<=0: return 0.5
        from scipy.stats import norm
        d1=(math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
        return norm.cdf(d1) if opt=="call" else norm.cdf(d1)-1
    except: return 0.5

def _bs_gamma(S,K,T,r,sigma):
    try:
        if T<=0 or sigma<=0: return 0
        from scipy.stats import norm
        d1=(math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
        return norm.pdf(d1)/(S*sigma*math.sqrt(T))
    except: return 0

def estimate_greeks(td, direction):
    price=td.get("price",100); iv=td.get("iv",0.3); T=30/365; r=0.05; K=price
    delta=_bs_delta(price,K,T,r,iv,"call" if direction=="CALL" else "put")
    gamma=_bs_gamma(price,K,T,r,iv)
    vega=price*0.01*math.sqrt(T) if T>0 else 0
    theta=-(price*iv)/(2*math.sqrt(365)) if iv>0 else 0
    return {"delta":round(delta,3),"gamma":round(gamma,4),"vega":round(vega,4),"theta":round(theta,4),"iv":round(iv*100,1)}

# ── Expiry helpers ────────────────────────────────────────────────────────────
def _pick_expiry(min_dte=21, max_dte=45):
    today = datetime.now().date()
    for offset in range(min_dte, max_dte+1):
        d = today + timedelta(days=offset)
        if d.weekday()==4 and 15<=d.day<=21:
            return d.strftime("%Y-%m-%d")
    for offset in range(min_dte, max_dte+1):
        d = today + timedelta(days=offset)
        if d.weekday()==4:
            return d.strftime("%Y-%m-%d")
    return (today + timedelta(days=max(min_dte,1))).strftime("%Y-%m-%d")

def _today_expiry():
    """0DTE: today or nearest trading day."""
    today = datetime.now().date()
    if today.weekday() < 5: return today.strftime("%Y-%m-%d")
    return (today + timedelta(days=(7-today.weekday()))).strftime("%Y-%m-%d")

# ── Entry builder ─────────────────────────────────────────────────────────────
def _real_option_price(d, direction, strike, expiry):
    """
    Look up real bid/ask from stored chain data.
    Returns (mid_price, bid, ask) or (None, None, None) if not found.
    """
    chains = d.get("stored_chains", {})
    if not chains: return None, None, None
    # Find closest stored expiry to requested expiry
    target_dt = datetime.strptime(expiry, "%Y-%m-%d").date()
    best_exp = min(chains.keys(),
                   key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target_dt).days),
                   default=None)
    if not best_exp: return None, None, None
    side = "calls" if direction == "CALL" else "puts"
    chain_side = chains[best_exp].get(side, {})
    if not chain_side: return None, None, None
    # Find closest strike
    strikes = list(chain_side.keys())
    closest = min(strikes, key=lambda s: abs(s - strike), default=None)
    if closest is None or abs(closest - strike) > d.get("price", 100) * 0.10:
        return None, None, None
    data = chain_side[closest]
    return data.get("mid", 0), data.get("bid", 0), data.get("ask", 0)

def build_entry(d, direction, score, dte_profile="30DTE"):
    price = d["price"]; iv = d.get("iv", 0.30)
    profile_params = {
        "0DTE":  {"otm": 0.005, "min_dte": 0,  "max_dte": 1,  "stop": 0.25, "target": 1.50},
        "7DTE":  {"otm": 0.02,  "min_dte": 5,  "max_dte": 9,  "stop": 0.40, "target": 1.50},
        "21DTE": {"otm": 0.02,  "min_dte": 18, "max_dte": 25, "stop": 0.50, "target": 1.80},
        "30DTE": {"otm": 0.03,  "min_dte": 25, "max_dte": 35, "stop": 0.50, "target": 1.80},
        "60DTE": {"otm": 0.04,  "min_dte": 45, "max_dte": 70, "stop": 0.60, "target": 2.00},
    }
    p = profile_params.get(dte_profile, profile_params["30DTE"])
    if score >= 75: otm_pct = p["otm"]
    else:           otm_pct = p["otm"] * 1.5
    raw_strike = price*(1+otm_pct) if direction=="CALL" else price*(1-otm_pct)
    inc = 5 if price>=200 else (2.5 if price>=100 else (1 if price>=20 else 0.5))
    strike = round(round(raw_strike/inc)*inc, 2)
    if dte_profile == "0DTE":
        expiry = _today_expiry()
    else:
        expiry = _pick_expiry(p["min_dte"], p["max_dte"])
    dte = max(0, (datetime.strptime(expiry,"%Y-%m-%d").date()-datetime.now().date()).days)
    T   = max(dte/365, 0.001)

    # ── Real market price lookup (matches Robinhood mid-price) ────────────────
    price_source = "market"
    real_mid, real_bid, real_ask = _real_option_price(d, direction, strike, expiry)
    if real_mid and real_mid > 0.05:
        opt_price = round(real_mid, 2)
    else:
        # Fallback to Black-Scholes approximation
        price_source = "estimated"
        opt_price = round(price*iv*math.sqrt(T)*0.4, 2)
        if dte_profile == "0DTE":
            opt_price = round(price*iv*math.sqrt(1/365)*0.5, 2)
        opt_price = max(0.05, opt_price)

    stop   = round(opt_price * p["stop"],  2)
    target = round(opt_price * p["target"],2)
    max_contracts = max(1, int(50/(opt_price*100))) if opt_price*100 > 0 else 1
    price_label = f"~${opt_price:.2f}" if price_source=="estimated" else f"${opt_price:.2f} (mid)"
    bid_ask_str = f" bid/ask ${real_bid:.2f}/${real_ask:.2f}" if real_bid else ""
    summary = (f"BUY {direction} ${strike} exp {expiry} ({price_label}/contract{bid_ask_str}, {dte}DTE) | "
               f"Stop: ${stop} | Target: ${target}")
    return {
        "strike":strike,"expiry":expiry,"dte":dte,
        "est_option_price":opt_price,"bid":real_bid or 0,"ask":real_ask or 0,
        "stop_loss_option":stop,"target_option":target,
        "max_contracts":max_contracts,"contract_cost":round(opt_price*100,2),
        "price_source":price_source,
        "entry_summary":summary,"dte_profile":dte_profile,
    }

def _round_strike(price, target):
    """Round target to nearest valid strike increment."""
    if price >= 200:  inc = 5
    elif price >= 100: inc = 2.5
    elif price >= 20:  inc = 1
    else:              inc = 0.5
    return round(target / inc) * inc

def build_spread(d, direction, score, dte_profile):
    """
    Build a credit spread entry instead of naked option.
    CALL signal → Bear Call Spread (sell call above price, buy higher call)
    PUT signal  → Bull Put Spread  (sell put below price, buy lower put)

    Targets 50-100% of credit received as profit (typical spread trader goal).
    """
    price   = d["price"]
    iv_pct  = d.get("iv_pct", 30)

    # Spread width: 5% of price, min $2.50, max $20, rounded to strike increments
    raw_width = max(2.5, min(20, round(price * 0.05)))
    if price >= 200:  spread_width = round(raw_width / 5) * 5
    elif price >= 50: spread_width = round(raw_width / 2.5) * 2.5
    else:             spread_width = round(raw_width)

    # Credit target: ~30-35% of spread width (realistic for 30-45 DTE 1-sigma OTM spread)
    credit_pct = 0.30 + (iv_pct - 30) * 0.003  # higher IV = more credit
    credit_pct = max(0.20, min(0.45, credit_pct))
    credit     = round(spread_width * credit_pct, 2)
    max_loss   = round(spread_width - credit, 2)

    if direction == "CALL":
        # Bear Call Spread: sell OTM call, buy further OTM call
        short_strike = _round_strike(price, price * 1.02)  # 2% above price
        long_strike  = _round_strike(price, short_strike + spread_width)
        spread_type  = "BEAR CALL SPREAD"
        breakeven    = round(short_strike + credit, 2)
        bias_note    = "Stock stays BELOW short strike"
    else:
        # Bull Put Spread: sell OTM put, buy further OTM put
        short_strike = _round_strike(price, price * 0.98)  # 2% below price
        long_strike  = _round_strike(price, short_strike - spread_width)
        spread_type  = "BULL PUT SPREAD"
        breakeven    = round(short_strike - credit, 2)
        bias_note    = "Stock stays ABOVE short strike"

    take_profit_50  = round(credit * 0.50, 2)   # close at 50% of credit (conservative)
    take_profit_100 = credit                      # close at full credit (max profit)
    stop_loss       = round(max_loss * 0.75, 2)  # close if spread worth 75% of max loss

    return {
        "spread_type":    spread_type,
        "short_strike":   short_strike,
        "long_strike":    long_strike,
        "spread_width":   spread_width,
        "credit":         credit,          # received per share ($×100 per contract)
        "max_profit":     credit,
        "max_loss":       max_loss,
        "breakeven":      breakeven,
        "take_profit_50": take_profit_50,
        "take_profit_100":take_profit_100,
        "stop_loss_at":   stop_loss,
        "dte_profile":    dte_profile,
        "bias_note":      bias_note,
    }

def build_iron_condor(d, score, dte_profile="30DTE"):
    """
    Build an Iron Condor: sell OTM call spread + sell OTM put spread.
    Profits from "sideways market" — stock stays between upper and lower strikes.

    Strategy: For medium-confidence signals (40-65 score), iron condor is safer than naked calls/puts.
    - Sell call spread above current price (profit if stays below)
    - Sell put spread below current price (profit if stays above)
    - Combined probability: ~80-85% of max profit

    Returns structured dict with both spreads combined.
    """
    # Don't build IC when IV is historically cheap (not enough premium)
    ivr = d.get("ivr")
    if ivr is not None and ivr < 25:
        return None

    price = d["price"]
    iv_pct = d.get("iv_pct", 30)

    # Spread width for iron condor: tighter than single spreads (around 3-4% per side)
    raw_width = max(2.5, min(15, round(price * 0.04)))
    if price >= 200:
        spread_width = round(raw_width / 5) * 5
    elif price >= 50:
        spread_width = round(raw_width / 2.5) * 2.5
    else:
        spread_width = round(raw_width)

    # Credit calculation: ~30% of spread width per side, combined from both spreads
    credit_pct = 0.30 + (iv_pct - 30) * 0.003
    credit_pct = max(0.20, min(0.45, credit_pct))
    credit_per_side = round(spread_width * credit_pct, 2)
    total_credit = round(credit_per_side * 2, 2)  # both call and put side collect credit
    max_loss_per_side = round(spread_width - credit_per_side, 2)
    total_max_loss = round(max_loss_per_side * 2, 2)  # both sides can max loss simultaneously

    # ── Call Spread (Bear Call): sell OTM calls ──────────────────────────────
    # Target: 15-20 delta (ATM ≈ 50 delta, 15-20 delta is safely OTM)
    # Approximation: each 1% move ≈ 2 delta change, so 15 delta ≈ 7.5% OTM
    call_otm_pct = 0.02 if score >= 55 else 0.03  # tighter if more confident
    short_call_strike = _round_strike(price, price * (1 + call_otm_pct))
    long_call_strike = _round_strike(price, short_call_strike + spread_width)
    call_breakeven = round(short_call_strike + credit_per_side, 2)

    # ── Put Spread (Bull Put): sell OTM puts ───────────────────────────────
    # Target: 15-20 delta (same risk as call side)
    put_otm_pct = 0.02 if score >= 55 else 0.03
    short_put_strike = _round_strike(price, price * (1 - put_otm_pct))
    long_put_strike = _round_strike(price, short_put_strike - spread_width)
    put_breakeven = round(short_put_strike - credit_per_side, 2)

    # Exit targets
    take_profit_50 = round(total_credit * 0.50, 2)  # Close at 50% of total credit
    take_profit_100 = total_credit
    stop_loss = round(total_max_loss * 0.75, 2)  # Close if IC worth 75% of max loss

    # Boost probability of profit when IV is high (better for selling premium)
    pop_str = "~80-85%"
    if ivr is not None and ivr > 60:
        pop_str = "~85-90%"  # Higher IVR = more premium, better odds
        # Actually increase credit slightly when IV is very elevated
        total_credit = round(total_credit * 1.05, 2)

    return {
        "strategy_type": "IRON CONDOR",
        "dte_profile": dte_profile,
        # Call Spread (upper half)
        "call_spread_type": "BEAR CALL SPREAD",
        "call_short_strike": short_call_strike,
        "call_long_strike": long_call_strike,
        "call_width": spread_width,
        "call_credit": credit_per_side,
        "call_breakeven": call_breakeven,
        # Put Spread (lower half)
        "put_spread_type": "BULL PUT SPREAD",
        "put_short_strike": short_put_strike,
        "put_long_strike": long_put_strike,
        "put_width": spread_width,
        "put_credit": credit_per_side,
        "put_breakeven": put_breakeven,
        # Combined IC metrics
        "total_credit": total_credit,           # Total premium collected ($×100 per contract)
        "max_profit": total_credit,
        "max_loss": total_max_loss,
        "max_loss_per_side": max_loss_per_side,
        "take_profit_50": take_profit_50,
        "take_profit_100": take_profit_100,
        "stop_loss_at": stop_loss,
        "profit_zone": f"${short_put_strike:.2f} - ${short_call_strike:.2f}",
        "probability_of_profit": pop_str,
        "ivr_boosted": ivr is not None and ivr > 60,
        "bias_note": f"Profit if stock stays between ${short_put_strike:.2f} and ${short_call_strike:.2f}",
    }

# ── Fetch ticker data ─────────────────────────────────────────────────────────
def fetch_ticker_data(symbol):
    try:
        tk = yf.Ticker(symbol)
        hist_1y  = tk.history(period="1y",  interval="1d")
        hist_3mo = tk.history(period="3mo", interval="1d")
        hist_5d  = tk.history(period="5d",  interval="1h", prepost=True)  # Include pre-market data
        if hist_3mo.empty or len(hist_3mo)<20: return None
        price      = float(hist_3mo["Close"].iloc[-1])
        prev_close = float(hist_3mo["Close"].iloc[-2])
        pct_change = round((price-prev_close)/prev_close*100, 2)
        # Pre-market gap detection
        premarket_gap_pct = 0.0
        try:
            from datetime import time as dt_time
            if hasattr(hist_5d.index, 'time'):
                premarket_bars = hist_5d[hist_5d.index.time < dt_time(9, 30)]
                if not premarket_bars.empty:
                    premarket_close = float(premarket_bars["Close"].iloc[-1])
                    premarket_gap_pct = round((premarket_close - prev_close) / prev_close * 100, 2)
        except: pass
        avg_vol_20 = float(hist_3mo["Volume"].tail(20).mean())
        today_vol  = float(hist_3mo["Volume"].iloc[-1])
        if price < 5.0 or avg_vol_20 < 300_000: return None
        rel_volume = round(today_vol/avg_vol_20, 2) if avg_vol_20>0 else 1.0
        move_4h = 0.0
        if not hist_5d.empty and len(hist_5d)>=2:
            try:
                last_ts = hist_5d.index[-1]
                cutoff  = last_ts - pd.Timedelta(hours=4)
                window  = hist_5d[hist_5d.index >= cutoff]
                if len(window)>=2:
                    move_4h = round((price-float(window["Close"].iloc[0]))/float(window["Close"].iloc[0])*100, 2)
            except: pass
        if not hist_1y.empty and len(hist_1y)>=20:
            high_52w = float(hist_1y["High"].max()); low_52w = float(hist_1y["Low"].min())
        else:
            high_52w = float(hist_3mo["High"].max()); low_52w = float(hist_3mo["Low"].min())
        pct_from_high = round((price-high_52w)/high_52w*100, 2)
        pct_from_low  = round((price-low_52w)/low_52w*100,   2)
        closes = hist_3mo["Close"]
        delta_ = closes.diff()
        gain   = delta_.where(delta_>0, 0).rolling(14).mean()
        loss   = (-delta_.where(delta_<0, 0)).rolling(14).mean()
        rsi    = float(100-100/(1+(gain/loss).iloc[-1]))
        high_  = hist_3mo["High"]; low__ = hist_3mo["Low"]
        tr  = pd.DataFrame({"hl":high_-low__,"hc":abs(high_-closes.shift(1)),"lc":abs(low__-closes.shift(1))}).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]); atr_pct = round(atr/price*100, 2)
        iv  = 0.30
        # {expiry: {"calls":{strike:{bid,ask,mid}}, "puts":{...}}}
        stored_chains = {}; available_expiries = []
        # Initialize variables for option chain metrics (will be filled in loop or kept as defaults)
        calls_vol = 0; puts_vol = 0; calls_oi = 0; puts_oi = 0; chain = None
        try:
            exps = tk.options
            if exps:
                available_expiries = list(exps)
                today  = datetime.now().date()
                # Fetch chains for nearest expiry in each DTE bucket: 0, 7, 21, 30, 60
                wanted_dtes = [1, 7, 21, 30, 60]
                fetched_exps = set()
                for target_dte in wanted_dtes:
                    target = today + timedelta(days=target_dte)
                    best_exp = min(exps, key=lambda e: abs((datetime.strptime(e,"%Y-%m-%d").date()-target).days))
                    if best_exp in fetched_exps: continue
                    fetched_exps.add(best_exp)
                    try:
                        chain = tk.option_chain(best_exp)
                        def _extract(df):
                            out = {}
                            for _, row in df.iterrows():
                                s = round(float(row["strike"]), 2)
                                bid = float(row.get("bid", 0) or 0)
                                ask = float(row.get("ask", 0) or 0)
                                mid = round((bid+ask)/2, 2)
                                if ask > 0:  # only include if ask is real
                                    out[s] = {"bid":bid,"ask":ask,"mid":mid if mid>0 else ask}
                            return out
                        stored_chains[best_exp] = {
                            "calls": _extract(chain.calls),
                            "puts":  _extract(chain.puts),
                        }

                        # Extract option chain volume/OI metrics for this expiry
                        if not chain.calls.empty:
                            calls_vol = chain.calls['volume'].fillna(0).sum() if 'volume' in chain.calls else 0
                            calls_oi  = chain.calls['openInterest'].fillna(0).sum() if 'openInterest' in chain.calls else 0
                        else:
                            calls_vol = calls_oi = 0
                        if not chain.puts.empty:
                            puts_vol = chain.puts['volume'].fillna(0).sum() if 'volume' in chain.puts else 0
                            puts_oi  = chain.puts['openInterest'].fillna(0).sum() if 'openInterest' in chain.puts else 0
                        else:
                            puts_vol = puts_oi = 0

                        # Get IV from ATM calls
                        if not chain.calls.empty and "impliedVolatility" in chain.calls:
                            atm = chain.calls[abs(chain.calls["strike"]-price)<price*0.07]
                            if not atm.empty:
                                iv_vals = atm["impliedVolatility"].dropna()
                                if len(iv_vals)>0: iv = max(0.05, min(float(iv_vals.mean()), 5.0))
                    except: pass
        except: pass
        debt_to_equity=0.0; market_cap=0.0; short_pct_float=0.0
        if symbol not in ETF_TICKERS:
            try:
                fi = tk.info
                de = fi.get("debtToEquity",0) or 0
                debt_to_equity = float(de)/100
                market_cap     = float(fi.get("marketCap",0) or 0)
                short_pct_float = float(fi.get("shortPercentOfFloat", 0) or 0)
            except: pass
        days_to_earnings=999
        try:
            cal = tk.calendar; today = datetime.now().date()
            if cal is not None:
                earn_dates=[]
                if isinstance(cal,dict): earn_dates=cal.get("Earnings Date",[])
                elif hasattr(cal,"loc") and "Earnings Date" in cal.index: earn_dates=cal.loc["Earnings Date"].tolist()
                for d in earn_dates:
                    if hasattr(d,"date"): d=d.date()
                    elif isinstance(d,str): d=datetime.strptime(d[:10],"%Y-%m-%d").date()
                    da=(d-today).days
                    if 0<=da<days_to_earnings: days_to_earnings=da
        except: pass
        sma20 = float(closes.rolling(20).mean().iloc[-1])
        sma50_src = hist_1y["Close"] if not hist_1y.empty and len(hist_1y)>=50 else closes
        sma50 = float(sma50_src.rolling(50).mean().iloc[-1])

        # ── Historical volatility vs implied volatility ────────────────────────
        # Uses Yang-Zhang estimator on 1y daily data — tells us if options are
        # cheap, fair, or expensive relative to how much the stock actually moves.
        hv_src = hist_1y if not hist_1y.empty and len(hist_1y) >= 35 else hist_3mo
        vol_data = hv_iv_analysis(hv_src, iv, window=30)

        # ── Store IV snapshot and compute IV Rank ────────────────────────────
        _store_iv_snapshot(symbol, iv)
        iv_rank_data = _get_iv_rank(symbol, iv)
        ivr = iv_rank_data.get("ivr")

        # ── Compute option chain metrics (from last fetched expiry) ─────────────
        pc_volume_ratio = 1.0; uoa_flag = False; atm_spread_pct = 0.15
        if calls_vol > 0:
            pc_volume_ratio = round(puts_vol / calls_vol, 2)
        # Unusual options activity: volume > 3x OI on any single strike
        if chain is not None:
            try:
                all_chains = pd.concat([chain.calls, chain.puts]) if not chain.calls.empty and not chain.puts.empty else (chain.calls if not chain.calls.empty else chain.puts)
                if not all_chains.empty and 'volume' in all_chains and 'openInterest' in all_chains:
                    for _, row in all_chains.iterrows():
                        vol = float(row.get('volume', 0) or 0)
                        oi = float(row.get('openInterest', 0) or 0)
                        if oi > 0 and vol > 3 * oi and vol > 500:
                            uoa_flag = True
                            break
            except: pass
            # ATM bid/ask spread quality
            try:
                if not chain.calls.empty:
                    atm_call = chain.calls.iloc[(chain.calls['strike'] - price).abs().argsort()[:1]]
                    if not atm_call.empty:
                        bid = float(atm_call['bid'].iloc[0] or 0)
                        ask = float(atm_call['ask'].iloc[0] or 0)
                        atm_spread_pct = (ask - bid) / ask if ask > 0 else 0.15
            except: pass

        return {
            "symbol":symbol,"price":round(price,2),"pct_change":pct_change,
            "move_4h":move_4h,"rel_volume":rel_volume,"avg_volume":int(avg_vol_20),
            "rsi":round(rsi,1),"atr_pct":atr_pct,"iv":round(iv,3),"iv_pct":round(iv*100,1),
            "hv30":vol_data["hv30_pct"],
            "iv_hv_ratio":vol_data["iv_hv_ratio"],
            "iv_signal":vol_data["signal"],
            "iv_signal_desc":vol_data["description"],
            "pct_from_52w_high":pct_from_high,"pct_from_52w_low":pct_from_low,
            "high_52w":round(high_52w,2),"low_52w":round(low_52w,2),
            "above_sma20":price>sma20,"above_sma50":price>sma50,
            "sma20":round(sma20,2),"sma50":round(sma50,2),
            "debt_to_equity":round(debt_to_equity,2),"market_cap":market_cap,
            "days_to_earnings":days_to_earnings,
            "is_war_sector":symbol in WAR_TICKERS,"is_distressed":symbol in DISTRESSED_TICKERS,
            "stored_chains":stored_chains,"available_expiries":available_expiries,
            "premarket_gap_pct":premarket_gap_pct,
            "short_pct_float":round(short_pct_float, 2),
            "pc_volume_ratio":pc_volume_ratio,
            "uoa_flag":uoa_flag,
            "atm_spread_pct":round(atm_spread_pct, 3),
            "ivr":ivr,
            "fetched_at":datetime.now().isoformat(),
        }
    except Exception as e:
        log.debug(f"fetch {symbol}: {e}"); return None

# ── Scoring ───────────────────────────────────────────────────────────────────
def score_for_call(d, w=None, sentiment=None):
    """Score for CALL. Optional sentiment param adds -15 to +15 adjustment."""
    w=w or {}; score=0; reasons=[]
    mv=d["move_4h"]; rv=d["rel_volume"]
    if mv>8:   score+=int(30*w.get("momentum_weight",1)); reasons.append(f"🚀 +{mv:.1f}% 4h (explosive)")
    elif mv>5: score+=int(22*w.get("momentum_weight",1)); reasons.append(f"📈 +{mv:.1f}% 4h (strong)")
    elif mv>3: score+=int(14*w.get("momentum_weight",1)); reasons.append(f"📈 +{mv:.1f}% 4h")
    elif mv>1.5: score+=6; reasons.append(f"↗ +{mv:.1f}% drift")
    if rv>4.0: score+=int(20*w.get("volume_weight",1)); reasons.append(f"🔥 Vol {rv:.1f}x")
    elif rv>2.5: score+=int(14*w.get("volume_weight",1)); reasons.append(f"📊 Vol {rv:.1f}x")
    elif rv>1.5: score+=6
    if d["above_sma20"] and d["above_sma50"]: score+=int(15*w.get("trend_weight",1)); reasons.append("Above SMA20+50")
    elif d["above_sma20"]: score+=int(8*w.get("trend_weight",1)); reasons.append("Above SMA20")
    if d["pct_from_52w_high"]>-3 and mv>0: score+=10; reasons.append("Near 52W high")
    rsi=d["rsi"]
    if 50<=rsi<=70: score+=10; reasons.append(f"RSI {rsi:.0f} healthy")
    elif rsi<50 and mv>3: score+=6; reasons.append(f"RSI {rsi:.0f} room to run")
    dte=d["days_to_earnings"]
    if 1<=dte<=5: score+=10; reasons.append(f"⚡ Earnings {dte}d")
    elif 6<=dte<=14: score+=5; reasons.append(f"Earnings {dte}d")
    if d["is_war_sector"] and mv>0: score+=int(10*w.get("sector_weight",1)); reasons.append("🎯 War sector")
    # IV/HV ratio — are we buying cheap or expensive options?
    iv_hv = d.get("iv_hv_ratio")
    iv_sig = d.get("iv_signal", "UNKNOWN")
    if iv_hv is not None:
        if   iv_sig == "CHEAP"     and iv_hv < 0.75: score += 15; reasons.append(f"IV cheap vs HV ({iv_hv:.2f}x) ✅")
        elif iv_sig == "CHEAP":                       score += 10; reasons.append(f"IV underpriced ({iv_hv:.2f}x HV)")
        elif iv_sig == "FAIR":                        score +=  3
        elif iv_sig == "RICH":                        score -=  8; reasons.append(f"IV pricey ({iv_hv:.2f}x HV) ⚠️")
        elif iv_sig == "VERY_RICH":                   score -= 18; reasons.append(f"IV very expensive ({iv_hv:.2f}x HV) ❌")
    else:
        if d["iv_pct"] > 50: score += 5; reasons.append(f"IV {d['iv_pct']:.0f}%")

    # ── Social sentiment adjustment: -15 to +15 ────────────────────────────
    if sentiment is not None:
        bull_pct = sentiment.get("stocktwits_bull_pct", 50)
        bear_pct = sentiment.get("stocktwits_bear_pct", 50)
        wsb_score = sentiment.get("wsb_score", 0)
        if sentiment.get("is_bullish_social"):
            adj = min(15, int((bull_pct - 65) * 0.5) + 5)
            score += adj
            reasons.append(f"📱 Social bull {bull_pct:.0f}%")
        elif sentiment.get("is_bearish_social"):
            adj = max(-15, -int((bear_pct - 65) * 0.5) - 5)
            score += adj
            reasons.append(f"📱 Social bear {bear_pct:.0f}%")
        if wsb_score >= 70:
            score += 5
            reasons.append(f"WSB trending #{sentiment.get('wsb_rank', 0)}")

    # ── New signal: IVR (IV Rank) ────────────────────────────────────────────
    ivr = d.get("ivr")
    if ivr is not None:
        if ivr < 25:
            score += 12
            reasons.append(f"📈 IVR {ivr:.0f}% (cheap)")
        elif ivr < 40:
            score += 5
            reasons.append(f"IVR {ivr:.0f}%")
        elif ivr > 70:
            score -= 10
            reasons.append(f"⚠️ IVR {ivr:.0f}% (expensive, prefer IC)")

    # ── Unusual options activity ─────────────────────────────────────────────
    if d.get("uoa_flag"):
        score += 12
        reasons.append("🔥 Unusual options activity")

    # ── Pre-market gap ───────────────────────────────────────────────────────
    gap = d.get("premarket_gap_pct", 0)
    if gap > 3:
        score += 8
        reasons.append(f"☀️ Pre-mkt gap +{gap:.1f}%")
    elif gap < -3:
        score -= 8
        reasons.append(f"Pre-mkt gap {gap:.1f}%")

    # ── Short squeeze setup ──────────────────────────────────────────────────
    if d.get("short_pct_float", 0) > 0.15 and d.get("wsb_score", 0) > 50:
        score += 12
        reasons.append(f"💥 Squeeze: {d['short_pct_float']*100:.0f}% short + WSB")

    # ── Bid/ask spread quality ───────────────────────────────────────────────
    if d.get("atm_spread_pct", 0) > 0.15:
        score -= 8
        reasons.append("⚠️ Wide bid/ask spread")

    return min(100, max(0, score)), " | ".join(reasons[:3]) if reasons else "Mixed signals"

def score_for_put(d, w=None, sentiment=None):
    """Score for PUT. Optional sentiment param adds -15 to +15 adjustment."""
    w=w or {}; score=0; reasons=[]
    mv=d["move_4h"]; rv=d["rel_volume"]
    if mv<-8:  score+=int(30*w.get("momentum_weight",1)); reasons.append(f"💥 {mv:.1f}% 4h (crash)")
    elif mv<-5:score+=int(22*w.get("momentum_weight",1)); reasons.append(f"📉 {mv:.1f}% 4h (strong drop)")
    elif mv<-3:score+=int(14*w.get("momentum_weight",1)); reasons.append(f"📉 {mv:.1f}% 4h")
    elif mv<-1.5: score+=6; reasons.append(f"↘ {mv:.1f}% drift")
    if rv>4.0: score+=int(20*w.get("volume_weight",1)); reasons.append(f"🔥 Vol {rv:.1f}x panic")
    elif rv>2.5:score+=int(14*w.get("volume_weight",1)); reasons.append(f"📊 Vol {rv:.1f}x")
    elif rv>1.5: score+=6
    if not d["above_sma20"] and not d["above_sma50"]: score+=int(15*w.get("trend_weight",1)); reasons.append("Below SMA20+50")
    elif not d["above_sma50"]: score+=int(8*w.get("trend_weight",1)); reasons.append("Below SMA50")
    if d["pct_from_52w_low"]<5 and mv<0: score+=10; reasons.append("Near 52W low")
    rsi=d["rsi"]
    if rsi>70: score+=10; reasons.append(f"RSI {rsi:.0f} overbought")
    elif rsi>60 and mv<-2: score+=6; reasons.append(f"RSI {rsi:.0f} falling")
    distress=0
    if d["is_distressed"]: distress+=5
    if d["debt_to_equity"]>3: distress+=5; reasons.append(f"⚠️ D/E {d['debt_to_equity']:.1f}")
    if d["pct_from_52w_high"]<-40: distress+=5; reasons.append(f"Down {abs(d['pct_from_52w_high']):.0f}% from peak")
    if distress>0: score+=int(distress*w.get("distress_weight",1))
    dte=d["days_to_earnings"]
    if 1<=dte<=5 and mv<-2: score+=10; reasons.append(f"⚡ Earnings {dte}d falling")
    elif 6<=dte<=14 and mv<0: score+=5
    # IV/HV ratio — buying puts is still buying premium; want cheap IV
    iv_hv = d.get("iv_hv_ratio")
    iv_sig = d.get("iv_signal", "UNKNOWN")
    if iv_hv is not None:
        if   iv_sig == "CHEAP"     and iv_hv < 0.75: score += 15; reasons.append(f"IV cheap vs HV ({iv_hv:.2f}x) ✅")
        elif iv_sig == "CHEAP":                       score += 10; reasons.append(f"IV underpriced ({iv_hv:.2f}x HV)")
        elif iv_sig == "FAIR":                        score +=  3
        elif iv_sig == "RICH":                        score -=  8; reasons.append(f"IV pricey ({iv_hv:.2f}x HV) ⚠️")
        elif iv_sig == "VERY_RICH":                   score -= 18; reasons.append(f"IV very expensive ({iv_hv:.2f}x HV) ❌")
    else:
        if d["iv_pct"] > 60: score += 5; reasons.append(f"IV {d['iv_pct']:.0f}%")

    # ── Social sentiment adjustment: -15 to +15 ────────────────────────────
    if sentiment is not None:
        bull_pct = sentiment.get("stocktwits_bull_pct", 50)
        bear_pct = sentiment.get("stocktwits_bear_pct", 50)
        if sentiment.get("is_bearish_social"):
            adj = min(15, int((bear_pct - 65) * 0.5) + 5)
            score += adj
            reasons.append(f"📱 Social bear {bear_pct:.0f}%")
        elif sentiment.get("is_bullish_social"):
            adj = max(-15, -int((bull_pct - 65) * 0.5) - 5)
            score += adj
            reasons.append(f"📱 Social bull {bull_pct:.0f}%")

    # ── IVR scoring (same for puts - high IVR = expensive puts) ──────────────
    ivr = d.get("ivr")
    if ivr is not None:
        if ivr < 25:
            score += 12
            reasons.append(f"📈 IVR {ivr:.0f}% (cheap)")
        elif ivr < 40:
            score += 5
            reasons.append(f"IVR {ivr:.0f}%")
        elif ivr > 70:
            score -= 10
            reasons.append(f"⚠️ IVR {ivr:.0f}% (expensive)")

    # ── Unusual options activity ─────────────────────────────────────────────
    if d.get("uoa_flag"):
        score += 12
        reasons.append("🔥 Unusual options activity")

    # ── Pre-market gap (negative gap = bullish for puts) ─────────────────────
    gap = d.get("premarket_gap_pct", 0)
    if gap < -3:
        score += 8
        reasons.append(f"🌑 Pre-mkt gap {gap:.1f}%")
    elif gap > 3:
        score -= 8
        reasons.append(f"Pre-mkt gap +{gap:.1f}%")

    # ── P/C volume ratio > 1.5 = heavy put buying = bearish flow ───────────
    pc_ratio = d.get("pc_volume_ratio", 1.0)
    if pc_ratio > 1.5:
        score += 8
        reasons.append(f"Put buying {pc_ratio:.1f}x")

    # ── Bid/ask spread quality ───────────────────────────────────────────────
    if d.get("atm_spread_pct", 0) > 0.15:
        score -= 8
        reasons.append("⚠️ Wide bid/ask spread")

    return min(100, max(0, score)), " | ".join(reasons[:3]) if reasons else "Bearish signals"

# ── Minimum score thresholds — never show a trade below these ─────────────────
MIN_SCORE = {"0DTE": 75, "7DTE": 70, "21DTE": 68, "30DTE": 65, "60DTE": 60}

# ── Minimum conviction gap — prevents ambiguous signals ─────────────────────
# A signal is meaningless when call_score ≈ put_score. Require sufficient directional confidence.
MIN_CONVICTION_GAP = {"0DTE": 20, "7DTE": 18, "21DTE": 15, "30DTE": 15, "60DTE": 12}

# ── DTE Profile Pickers ───────────────────────────────────────────────────────
def _pick_0dte(results):
    """0DTE: explosive momentum only — >4% 4h move + >3x vol + score>=70 + conviction gap."""
    candidates = [r for r in results
                  if abs(r["move_4h"]) > 4 and r["rel_volume"] > 3.0
                  and max(r["call_score"], r["put_score"]) >= MIN_SCORE["0DTE"]
                  and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["0DTE"]]
    if not candidates: return None
    best = max(candidates, key=lambda x: max(x["call_score"], x["put_score"]))
    direction = "CALL" if best["call_score"] >= best["put_score"] else "PUT"
    score = best[f"{direction.lower()}_score"]
    entry = build_entry(best, direction, score, "0DTE")
    return {**best, "dte_profile":"0DTE", "direction":direction, "profile_score":score,
            "entry_call": entry if direction=="CALL" else build_entry(best,"CALL",best["call_score"],"0DTE"),
            "entry_put":  entry if direction=="PUT"  else build_entry(best,"PUT", best["put_score"], "0DTE"),
            "spread": build_spread(best, direction, score, "0DTE"),
            "profile_reason": f"0DTE MOMENTUM: {best[direction.lower()+'_reason']}"}

def _pick_7dte(results):
    """7DTE: earnings within 1-7 days + score>=62 + conviction gap."""
    candidates = [r for r in results
                  if 1 <= r.get("days_to_earnings", 999) <= 7
                  and max(r["call_score"], r["put_score"]) >= MIN_SCORE["7DTE"]
                  and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["7DTE"]]
    if not candidates:  # loosen earnings window slightly
        candidates = [r for r in results
                      if 1 <= r.get("days_to_earnings", 999) <= 10
                      and max(r["call_score"], r["put_score"]) >= MIN_SCORE["7DTE"]
                      and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["7DTE"]]
    if not candidates: return None
    best = max(candidates, key=lambda x: max(x["call_score"], x["put_score"]))
    direction = "CALL" if best["call_score"] >= best["put_score"] else "PUT"
    score = best[f"{direction.lower()}_score"]
    dte_val = best.get("days_to_earnings", 999)
    return {**best, "dte_profile":"7DTE", "direction":direction, "profile_score":score,
            "entry_call": build_entry(best,"CALL",best["call_score"],"7DTE"),
            "entry_put":  build_entry(best,"PUT", best["put_score"], "7DTE"),
            "spread": build_spread(best, direction, score, "7DTE"),
            "profile_reason": f"7DTE EARNINGS in {dte_val}d: {best[direction.lower()+'_reason']}"}

def _pick_21dte(results):
    """21DTE: trend continuation — SMA aligned + RSI 45-68 + score>=58 + conviction gap."""
    candidates = [r for r in results
                  if r["above_sma20"] and r["above_sma50"]
                  and 45 <= r["rsi"] <= 68
                  and r["call_score"] >= MIN_SCORE["21DTE"]
                  and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["21DTE"]]
    if not candidates:  # loosen: just need score and SMA20
        candidates = [r for r in results
                      if r["above_sma20"]
                      and max(r["call_score"], r["put_score"]) >= MIN_SCORE["21DTE"]
                      and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["21DTE"]]
    if not candidates: return None
    best = max(candidates, key=lambda x: max(x["call_score"], x["put_score"]))
    direction = "CALL" if best["call_score"] >= best["put_score"] else "PUT"
    score = best[f"{direction.lower()}_score"]
    return {**best, "dte_profile":"21DTE", "direction":direction, "profile_score":score,
            "entry_call": build_entry(best,"CALL",best["call_score"],"21DTE"),
            "entry_put":  build_entry(best,"PUT", best["put_score"], "21DTE"),
            "spread": build_spread(best, direction, score, "21DTE"),
            "profile_reason": f"21DTE TREND: {best[direction.lower()+'_reason']}"}

def _pick_30dte(results):
    """30DTE: highest-conviction call or put with score>=55 + conviction gap."""
    candidates = [r for r in results
                  if max(r["call_score"], r["put_score"]) >= MIN_SCORE["30DTE"]
                  and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["30DTE"]]
    if not candidates: return None
    best_c = max(candidates, key=lambda x: x["call_score"])
    best_p = max(candidates, key=lambda x: x["put_score"])
    if best_c["call_score"] >= best_p["put_score"]:
        best=best_c; direction="CALL"; score=best["call_score"]
    else:
        best=best_p; direction="PUT";  score=best["put_score"]
    return {**best, "dte_profile":"30DTE", "direction":direction, "profile_score":score,
            "entry_call": build_entry(best,"CALL",best["call_score"],"30DTE"),
            "entry_put":  build_entry(best,"PUT", best["put_score"], "30DTE"),
            "spread": build_spread(best, direction, score, "30DTE"),
            "profile_reason": f"30DTE STANDARD: {best[direction.lower()+'_reason']}"}

def _pick_60dte(results):
    """60DTE: macro/thesis play — war sector or high-IV + score>=50 + conviction gap."""
    candidates = [r for r in results
                  if (r.get("is_war_sector") or r["iv_pct"] > 45)
                  and max(r["call_score"], r["put_score"]) >= MIN_SCORE["60DTE"]
                  and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["60DTE"]]
    if not candidates:  # any ticker meeting minimum score
        candidates = [r for r in results
                      if max(r["call_score"], r["put_score"]) >= MIN_SCORE["60DTE"]
                      and abs(r["call_score"] - r["put_score"]) >= MIN_CONVICTION_GAP["60DTE"]]
    if not candidates: return None
    best = max(candidates, key=lambda x: max(x["call_score"], x["put_score"]))
    direction = "CALL" if best["call_score"] >= best["put_score"] else "PUT"
    score = best[f"{direction.lower()}_score"]
    return {**best, "dte_profile":"60DTE", "direction":direction, "profile_score":score,
            "entry_call": build_entry(best,"CALL",best["call_score"],"60DTE"),
            "entry_put":  build_entry(best,"PUT", best["put_score"], "60DTE"),
            "spread": build_spread(best, direction, score, "60DTE"),
            "profile_reason": f"60DTE MACRO: {best[direction.lower()+'_reason']}"}

# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan(tickers=None, learning_weights=None):
    universe = tickers or SCAN_TICKERS
    log.info(f"Scanning {len(universe)} tickers...")
    results=[]; scanned=0
    for symbol in universe:
        try:
            d = fetch_ticker_data(symbol)
            if d is None: continue
            cs,cr = score_for_call(d, learning_weights)
            ps,pr = score_for_put(d,  learning_weights)
            gc = estimate_greeks(d,"CALL"); gp = estimate_greeks(d,"PUT")
            ec = build_entry(d,"CALL",cs,"30DTE"); ep = build_entry(d,"PUT",ps,"30DTE")
            results.append({**d,"call_score":cs,"call_reason":cr,"put_score":ps,"put_reason":pr,
                             "call_greeks":gc,"put_greeks":gp,"entry_call":ec,"entry_put":ep})
            scanned+=1
        except Exception as e: log.warning(f"scan {symbol}: {e}")
        time.sleep(0.1)
    top_calls = sorted(results, key=lambda x:x["call_score"], reverse=True)[:5]
    top_puts  = sorted(results, key=lambda x:x["put_score"],  reverse=True)[:5]
    log.info(f"Scan done. {scanned}/{len(universe)} tickers.")
    return {"top_calls":top_calls,"top_puts":top_puts,"scan_time":datetime.now().isoformat(),
            "universe_size":len(universe),"scanned":scanned,"all_results":results}

def _apply_regime_bias(results, regime_info):
    """Re-rank results based on regime bias (CALLS/PUTS/BOTH)."""
    bias = regime_info.get("bias", "BOTH")
    if bias == "CALLS":
        return sorted(results, key=lambda x: x["call_score"] - x["put_score"]*0.3, reverse=True)
    elif bias == "PUTS":
        return sorted(results, key=lambda x: x["put_score"] - x["call_score"]*0.3, reverse=True)
    return results  # BOTH: no re-ranking

def run_scan_dte_profiles(learning_weights=None):
    """Run full scan with 6-phase market intelligence integration."""
    log.info("Running DTE-profile scan with market intelligence...")

    # ── Phase 1: Market intelligence signals and recommendation confidence ────
    market_intel = _get_market_intel()
    market_bias = market_intel.get("market_bias", "NEUTRAL")
    wsb_data = market_intel.get("wsb_trending", [])
    rec_confidence = _get_recommendation_confidence()  # Read confidence scores for each source
    log.info(f"Market intel: {market_intel.get('context_summary', 'unavailable')}")
    log.info(f"Recommendation confidence: top_call={rec_confidence.get('top_call',{}).get('confidence',50)}%, "
             f"top_put={rec_confidence.get('top_put',{}).get('confidence',50)}%, "
             f"dte_pick={rec_confidence.get('dte_pick',{}).get('confidence',50)}%")

    # ── Phase 2: Initial scan (unchanged) ──────────────────────────────────
    base   = run_scan(learning_weights=learning_weights)
    results = base["all_results"]
    regime  = get_market_regime()
    mkt_ctx = get_market_context()
    bias    = regime.get("bias", "BOTH")

    # ── Phase 3: Selective StockTwits fetch for top-20 technical scorers ────
    # Sort by raw technical score, take top 20, fetch social sentiment for each
    sorted_by_raw = sorted(results, key=lambda x: max(x["call_score"], x["put_score"]), reverse=True)
    top20_symbols = set([r["symbol"] for r in sorted_by_raw[:20]])
    log.info(f"Fetching social sentiment for top-20: {list(top20_symbols)[:5]}...")

    for r in results:
        if r["symbol"] in top20_symbols:
            try:
                time.sleep(0.3)  # Rate limit protection for StockTwits
                sent = _get_social_sentiment(r["symbol"], wsb_data)
                # Re-score with sentiment adjustment
                new_cs, new_cr = score_for_call(r, learning_weights, sent)
                new_ps, new_pr = score_for_put(r, learning_weights, sent)
                r["call_score"] = new_cs
                r["put_score"] = new_ps
                r["call_reason"] = new_cr
                r["put_reason"] = new_pr
                r["social_sentiment"] = sent
            except Exception as e:
                log.debug(f"Social sentiment {r['symbol']}: {e}")

    # ── Phase 3b: Apply confidence multiplier to scores ─────────────────────
    # DTE picker recommendations will have "dte_pick" source, so apply that confidence
    # top_call/top_put sources are handled in bot_patch3.py (separate picking logic)
    dte_conf_mult = rec_confidence.get("dte_pick", {}).get("confidence", 50) / 100.0
    if dte_conf_mult < 1.0:  # Only log if confidence is below 100%
        log.info(f"Applying DTE confidence multiplier {dte_conf_mult:.2f} to all DTE-picker recommendations")
        for r in results:
            # Reduce both call and put scores by confidence multiplier
            # This makes low-confidence DTE picks less likely to be selected
            original_call = r["call_score"]
            original_put = r["put_score"]
            r["call_score"] = round(r["call_score"] * dte_conf_mult)
            r["put_score"] = round(r["put_score"] * dte_conf_mult)
            if original_call != r["call_score"] or original_put != r["put_score"]:
                r["call_reason"] = f"[conf {rec_confidence.get('dte_pick', {}).get('confidence', 50)}%] " + r.get("call_reason", "")
                r["put_reason"] = f"[conf {rec_confidence.get('dte_pick', {}).get('confidence', 50)}%] " + r.get("put_reason", "")

    # ── Phase 4: Apply QQQ/SPY context boosts ──────────────────────────────
    for r in results:
        if mkt_ctx.get("tech_selloff") and r["symbol"] in TECH_TICKERS:
            r["put_score"] = min(100, r["put_score"] + 15)
            r["put_reason"] = "📉 Tech selloff day | " + r.get("put_reason","")
        if mkt_ctx.get("rally_day") and r["symbol"] in TECH_TICKERS:
            r["call_score"] = min(100, r["call_score"] + 10)

    # ── Phase 5: Apply market intel global boosts ──────────────────────────
    if market_bias == "STRONG_PUTS":
        for r in results:
            if r["symbol"] in TECH_TICKERS:
                r["put_score"] = min(100, r["put_score"] + 20)
                r["put_reason"] = "🌐 STRONG_PUTS market | " + r.get("put_reason", "")
    elif market_bias == "PUTS":
        for r in results:
            r["put_score"] = min(100, r["put_score"] + 8)
    elif market_bias == "STRONG_CALLS":
        for r in results:
            if r["symbol"] in TECH_TICKERS:
                r["call_score"] = min(100, r["call_score"] + 20)
    elif market_bias == "CALLS":
        for r in results:
            r["call_score"] = min(100, r["call_score"] + 8)

    # ── Phase 6: Re-rank and run DTE pickers ───────────────────────────────
    # Re-rank by regime bias before feeding pickers
    biased_results = _apply_regime_bias(results, regime)

    used_symbols = set()
    def _pick_unique(picker_fn, res):
        # Filter out already-used symbols — no fallback to prevent duplicate signals
        filtered = [r for r in res if r["symbol"] not in used_symbols]
        pick = picker_fn(filtered)
        if pick:
            used_symbols.add(pick["symbol"])
            # Ensure direction matches regime bias for non-neutral picks
            if bias == "CALLS" and pick.get("direction") == "PUT" and pick.get("call_score",0) >= 45:
                pick["direction"] = "CALL"
            elif bias == "PUTS" and pick.get("direction") == "CALL" and pick.get("put_score",0) >= 45:
                pick["direction"] = "PUT"
        return pick

    picks = {
        "0DTE":  _pick_unique(_pick_0dte,  biased_results),
        "7DTE":  _pick_unique(_pick_7dte,  biased_results),
        "21DTE": _pick_unique(_pick_21dte, biased_results),
        "30DTE": _pick_unique(_pick_30dte, biased_results),
        "60DTE": _pick_unique(_pick_60dte, biased_results),
    }

    # ── Add Iron Condor entries for medium-confidence picks ──────────────────
    # If score is 40-65 (uncertain direction), build IC instead of naked option
    for dte_tier, pick in picks.items():
        if pick is None:
            continue
        call_score = pick.get("call_score", 0)
        put_score = pick.get("put_score", 0)
        max_score = max(call_score, put_score)

        # Only build IC if medium confidence (40-65 is uncertain/choppy)
        if 40 <= max_score <= 65:
            try:
                ic = build_iron_condor(pick, max_score, dte_profile=dte_tier)
                # Add IC entry to the pick
                pick["iron_condor"] = ic
                pick["ic_available"] = True
            except Exception as e:
                log.debug(f"Iron condor build {pick['symbol']} {dte_tier}: {e}")
                pick["ic_available"] = False
        else:
            pick["ic_available"] = False

    return {**base, "dte_picks":picks, "regime":regime, "market_intel":market_intel, "rec_confidence":rec_confidence}

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s")
    r = run_scan_dte_profiles()
    regime = r["regime"]
    print(f"\nMarket Regime: {regime['regime']} (VIX {regime['vix']}) — {regime['note']}")
    print("\n=== DTE PROFILE PICKS ===")
    for dte, pick in r["dte_picks"].items():
        if pick:
            e = pick["entry_call"] if pick["direction"]=="CALL" else pick["entry_put"]
            print(f"\n[{dte}] {pick['symbol']} {pick['direction']} score:{pick['profile_score']}")
            print(f"  {e['entry_summary']}")
            print(f"  {pick['profile_reason']}")
        else:
            print(f"\n[{dte}] No qualifying setup found")
