#!/usr/bin/env python3
"""
market_intel.py — Market-wide intelligence signals (no API keys required).

Provides 6 free data sources aggregated into a market_bias enum:
  1. CNN Fear & Greed index (via alternative.me, always free)
  2. CBOE equity put/call ratio (via yfinance ^PCALL)
  3. WSB trending tickers (via ApeWisdom API, no auth)
  4. VIX term structure (via yfinance VIX9D/VIX30)
  5. Composite market bias (STRONG_PUTS / PUTS / NEUTRAL / CALLS / STRONG_CALLS)
  6. One-liner context summary for Telegram

All signals cached with TTL. Silent fallback to neutral on any API failure.
"""
import json, logging, math, time
import requests
import yfinance as yf
import pandas as pd

log = logging.getLogger("ob.market_intel")

# ── Cache storage ──────────────────────────────────────────────────────────
_CACHE_FG = {}
_CACHE_PC = {}
_CACHE_WSB = {}
_CACHE_VIX = {}
_CACHE_INTEL = {}

TTL_FG = 1800      # 30 min
TTL_PC = 3600      # 60 min (EOD relevant)
TTL_WSB = 1800     # 30 min
TTL_VIX = 900      # 15 min (intraday)
TTL_INTEL = 1800   # 30 min aggregate

# ── Neutral fallback (returned on any unhandled error) ──────────────────────
_MARKET_INTEL_NEUTRAL = {
    "market_bias": "NEUTRAL",
    "context_summary": "Market intel unavailable",
    "fear_greed": {
        "value": 50, "label": "Neutral",
        "extreme_fear": False, "extreme_greed": False, "source": "fallback"
    },
    "pc_ratio": {
        "pc_ratio": 0.85, "bearish_signal": False,
        "bullish_signal": False, "source": "fallback"
    },
    "wsb_trending": [],
    "vix_term": {
        "vix9d": 0, "vix30": 0, "ratio": 1.0,
        "backwardation": False, "contango": False, "source": "fallback"
    },
    "bias_score": 0,
    "fetched_at": "",
}

# ── Generic TTL cache helper ───────────────────────────────────────────────
def _cached(cache_dict, key, ttl, compute_fn):
    """Check cache TTL; recompute if stale or missing."""
    now = time.time()
    if key in cache_dict:
        ts, val = cache_dict[key]
        if now - ts < ttl:
            return val
    try:
        val = compute_fn()
        cache_dict[key] = (now, val)
        return val
    except Exception as e:
        log.debug(f"Cache compute {key}: {e}")
        return None

# ── Fear & Greed Index (alternative.me) ────────────────────────────────────
def get_fear_greed():
    """
    Returns CNN/crypto F&G index from alternative.me.
    Always free. Crypto-based but tracks equity sentiment closely.
    TTL: 30 min.
    """
    def _fetch():
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1&format=json",
            timeout=8,
            headers={"User-Agent": "OBBot/1.0"}
        )
        if r.status_code != 200:
            return None
        data = r.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]
        return {
            "value": value,
            "label": label,
            "extreme_fear": label == "Extreme Fear",
            "extreme_greed": label == "Extreme Greed",
            "source": "alternative.me"
        }

    result = _cached(_CACHE_FG, "fg", TTL_FG, _fetch)
    if result is None:
        return {
            "value": 50, "label": "Neutral",
            "extreme_fear": False, "extreme_greed": False, "source": "fallback"
        }
    return result

# ── CBOE Put/Call Ratio (via yfinance ^PCALL) ─────────────────────────────
def get_cboe_pc_ratio():
    """
    Pulls CBOE equity put/call ratio via yfinance ticker ^PCALL.
    TTL: 60 min.
    Threshold: > 1.0 = bearish (heavy put buying); < 0.7 = bullish (complacency).
    """
    def _fetch():
        try:
            df = yf.download("^PCALL", period="5d", interval="1d", progress=False)
            if df.empty or len(df) == 0:
                return None
            pc_ratio = float(df["Close"].iloc[-1])
            if pd.isna(pc_ratio):
                return None
            return {
                "pc_ratio": round(pc_ratio, 4),
                "bearish_signal": pc_ratio > 1.0,
                "bullish_signal": pc_ratio < 0.70,
                "source": "cboe_via_yfinance"
            }
        except Exception as e:
            log.debug(f"get_cboe_pc_ratio: {e}")
            return None

    result = _cached(_CACHE_PC, "pc", TTL_PC, _fetch)
    if result is None:
        return {
            "pc_ratio": 0.85, "bearish_signal": False,
            "bullish_signal": False, "source": "fallback"
        }
    return result

# ── WSB Trending Tickers (ApeWisdom API) ──────────────────────────────────
def get_wsb_trending():
    """
    Fetches top 100 tickers from ApeWisdom WallStreetBets filter.
    Returns top 25 only. No auth required.
    TTL: 30 min.
    """
    def _fetch():
        r = requests.get(
            "https://apewisdom.io/api/v1.0/filter/wallstreetbets",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OBBot/1.0)"}
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        # Keep top 25 for memory efficiency
        top25 = results[:25]
        return [
            {
                "ticker": r["ticker"],
                "mentions": r["mentions"],
                "rank": r["rank"],
                "rank_change_24h": r.get("rank_24h_ago", r["rank"]) - r["rank"]
            }
            for r in top25
        ]

    result = _cached(_CACHE_WSB, "wsb", TTL_WSB, _fetch)
    if result is None:
        return []
    return result

# ── VIX Term Structure (backwardation / contango) ──────────────────────────
def get_vix_term_structure():
    """
    Ratio of VIX9D (9-day) to VIX (30-day).
    Ratio > 1.0 = backwardation (panic/fear)
    Ratio < 0.85 = contango (calm/complacency)
    TTL: 15 min.
    """
    def _fetch():
        try:
            # Batch fetch both VIX series in one call
            df = yf.download("^VIX9D ^VIX", period="5d", interval="1d", progress=False)
            if df.empty or len(df) == 0:
                return None

            # yfinance returns MultiIndex columns for multiple tickers
            # Structure: df[("Close", "^VIX9D")] and df[("Close", "^VIX")]
            vix9d_close = float(df[("Close", "^VIX9D")].iloc[-1])
            vix30_close = float(df[("Close", "^VIX")].iloc[-1])

            if pd.isna(vix9d_close) or pd.isna(vix30_close) or vix30_close == 0:
                return None

            ratio = vix9d_close / vix30_close
            return {
                "vix9d": round(vix9d_close, 2),
                "vix30": round(vix30_close, 2),
                "ratio": round(ratio, 3),
                "backwardation": ratio > 1.0,
                "contango": ratio < 0.85,
                "source": "vix_term_yfinance"
            }
        except Exception as e:
            log.debug(f"get_vix_term_structure: {e}")
            return None

    result = _cached(_CACHE_VIX, "vix", TTL_VIX, _fetch)
    if result is None:
        return {
            "vix9d": 0, "vix30": 0, "ratio": 1.0,
            "backwardation": False, "contango": False, "source": "fallback"
        }
    return result

# ── Compute Market Bias (Internal Aggregation) ─────────────────────────────
def _compute_market_intel():
    """
    Aggregates all 4 signals into a market_bias enum and context summary.
    Bias scoring algorithm (see plan for details).
    """
    fg = get_fear_greed()
    pc = get_cboe_pc_ratio()
    wsb = get_wsb_trending()
    vix_ts = get_vix_term_structure()

    bias_score = 0

    # Fear & Greed contribution
    fg_value = fg.get("value", 50)
    if fg_value <= 15:
        bias_score -= 3  # extreme fear
    elif fg_value <= 25:
        bias_score -= 2
    elif fg_value >= 85:
        bias_score += 3  # extreme greed
    elif fg_value >= 75:
        bias_score += 2

    # P/C ratio contribution
    pc_ratio = pc.get("pc_ratio", 0.85)
    if pc_ratio > 1.0:
        bias_score -= 2  # bearish signal
    elif pc_ratio < 0.70:
        bias_score += 2  # bullish signal
    elif pc_ratio < 0.85:
        bias_score += 1  # mild bullish

    # VIX term structure contribution
    if vix_ts.get("backwardation"):
        bias_score -= 1
    elif vix_ts.get("contango"):
        bias_score += 1

    # WSB tech dump signal (qualitative)
    TECH_TICKERS = {"NVDA", "AMD", "PLTR", "TSLA", "AMZN", "GOOGL", "META", "MSFT", "AAPL", "SMCI", "AVGO", "ARM", "MRVL"}
    tech_in_top10 = sum(1 for w in wsb[:10] if w.get("ticker") in TECH_TICKERS)
    if tech_in_top10 >= 3 and fg_value < 40:
        bias_score -= 1  # tech being talked down + fear

    # Map bias_score to enum
    if bias_score <= -4:
        market_bias = "STRONG_PUTS"
    elif bias_score <= -2:
        market_bias = "PUTS"
    elif bias_score <= 1:
        market_bias = "NEUTRAL"
    elif bias_score <= 3:
        market_bias = "CALLS"
    else:
        market_bias = "STRONG_CALLS"

    # Build context summary for Telegram
    parts = [
        f"F&G: {fg_value} ({fg.get('label', 'Neutral')})",
        f"P/C: {pc_ratio:.2f}",
    ]

    if vix_ts.get("backwardation"):
        parts.append("VIX: backwardation")
    elif vix_ts.get("contango"):
        parts.append("VIX: contango")

    if wsb:
        top3 = [w["ticker"] for w in wsb[:3]]
        parts.append("WSB: " + ", ".join(top3))

    context_summary = " | ".join(parts)

    return {
        "market_bias": market_bias,
        "context_summary": context_summary,
        "fear_greed": fg,
        "pc_ratio": pc,
        "wsb_trending": wsb,
        "vix_term": vix_ts,
        "bias_score": bias_score,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

# ── Public API ─────────────────────────────────────────────────────────────
def get_market_intel():
    """
    Cached aggregate of all market intelligence signals.
    Always returns a valid dict — never raises.
    TTL: 30 min.
    """
    def _fetch():
        try:
            return _compute_market_intel()
        except Exception as e:
            log.error(f"_compute_market_intel: {e}")
            return _MARKET_INTEL_NEUTRAL.copy()

    result = _cached(_CACHE_INTEL, "intel", TTL_INTEL, _fetch)
    if result is None:
        return _MARKET_INTEL_NEUTRAL.copy()
    return result

# ── Main / Testing ────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log.info("Market Intel Module Test")

    intel = get_market_intel()
    print(json.dumps(intel, indent=2, default=str))
    print(f"\nMarket Bias: {intel['market_bias']}")
    print(f"Context: {intel['context_summary']}")
