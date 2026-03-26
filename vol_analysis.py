#!/usr/bin/env python3
"""
vol_analysis.py — Historical volatility estimators.

Implements Yang-Zhang and Garman-Klass estimators from Euan Sinclair's
"Volatility Trading" (via github.com/jasonstrimpel/volatility-trading).

These are used in scanner.py to compute the IV/HV ratio:
  - IV/HV < 0.85 → options are CHEAP relative to realized vol → buy premium
  - IV/HV 0.85–1.3 → fairly priced → neutral
  - IV/HV > 1.5 → options are EXPENSIVE → penalize score

All functions accept a yfinance DataFrame with Open, High, Low, Close columns.
Returns annualized volatility as a decimal (e.g., 0.28 = 28% annualized).
"""
import math
import logging
import numpy as np

log = logging.getLogger("ob.vol")


def yang_zhang_hv(df, window=30, trading_periods=252):
    """
    Yang-Zhang volatility estimator.
    Best for OHLC data — handles overnight gaps and intraday range.
    Requires at least window+2 rows of Open, High, Low, Close data.
    Returns annualized HV as decimal, or None if insufficient data.
    """
    try:
        if len(df) < window + 2:
            return None

        data = df[["Open", "High", "Low", "Close"]].tail(window + 1).copy()
        data = data.dropna()
        if len(data) < window + 1:
            return None

        # Overnight return: today's open vs yesterday's close
        log_oc = np.log(data["Open"] / data["Close"].shift(1)).dropna()
        # Open-to-close return (skip first row — no overnight for it)
        log_co = np.log(data["Close"] / data["Open"]).iloc[1:]
        # Intraday high/low vs open
        log_ho = np.log(data["High"] / data["Open"]).iloc[1:]
        log_lo = np.log(data["Low"]  / data["Open"]).iloc[1:]

        n = min(len(log_oc), len(log_co), window)
        if n < 5:
            return None

        log_oc = log_oc.iloc[-n:]
        log_co = log_co.iloc[-n:]
        log_ho = log_ho.iloc[-n:]
        log_lo = log_lo.iloc[-n:]

        # Per-day variance components
        var_overnight  = float(log_oc.var())   # sample variance of overnight gaps
        var_open_close = float(log_co.var())   # sample variance of open-to-close returns
        rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
        var_rs = float(rs.mean())              # Rogers-Satchell mean daily estimate

        # Yang-Zhang weighting parameter
        k = 0.34 / (1.34 + (n + 1) / max(n - 1, 1))

        var_daily = var_overnight + k * var_open_close + (1 - k) * var_rs
        return math.sqrt(max(var_daily, 0) * trading_periods)

    except Exception as e:
        log.debug(f"yang_zhang_hv: {e}")
        return None


def garman_klass_hv(df, window=30, trading_periods=252):
    """
    Garman-Klass volatility estimator.
    Simpler than Yang-Zhang, uses only High/Low/Open/Close (no overnight gaps).
    Good as a cross-check. Returns annualized HV as decimal.
    """
    try:
        if len(df) < window:
            return None

        data = df[["Open", "High", "Low", "Close"]].tail(window).copy().dropna()
        if len(data) < window:
            return None

        log_hl = np.log(data["High"] / data["Low"])
        log_co = np.log(data["Close"] / data["Open"])

        rs = 0.5 * log_hl ** 2 - (2 * math.log(2) - 1) * log_co ** 2
        return math.sqrt(trading_periods * float(rs.mean()))

    except Exception as e:
        log.debug(f"garman_klass_hv: {e}")
        return None


def hv_iv_analysis(df, iv_decimal, window=30):
    """
    Compute HV30 and compare against IV to generate a trading signal.

    Parameters:
        df          : yfinance DataFrame with Open/High/Low/Close
        iv_decimal  : implied volatility as decimal (e.g. 0.35 for 35%)
        window      : rolling window for HV (default 30 days)

    Returns dict:
        hv30        : Yang-Zhang HV as decimal (or None)
        hv30_pct    : hv30 * 100 (display value)
        iv_hv_ratio : iv / hv30 (None if hv30 unavailable)
        signal      : "CHEAP" | "FAIR" | "RICH" | "VERY_RICH" | "UNKNOWN"
        score_adj   : integer points to add (positive) or subtract (negative)
        description : human-readable string for Telegram/log
    """
    hv30 = yang_zhang_hv(df, window=window)

    if hv30 is None or hv30 <= 0 or iv_decimal is None or iv_decimal <= 0:
        return {
            "hv30": None, "hv30_pct": None,
            "iv_hv_ratio": None, "signal": "UNKNOWN",
            "score_adj": 0,
            "description": f"IV:{iv_decimal*100:.0f}% (HV unavailable)",
        }

    ratio = round(iv_decimal / hv30, 2)
    hv_pct = round(hv30 * 100, 1)
    iv_pct  = round(iv_decimal * 100, 1)

    if ratio < 0.75:
        signal = "CHEAP"; score_adj = +15
        desc = f"IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% ({ratio:.2f}x) — options CHEAP ✅"
    elif ratio < 0.90:
        signal = "CHEAP"; score_adj = +10
        desc = f"IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% ({ratio:.2f}x) — options underpriced"
    elif ratio <= 1.30:
        signal = "FAIR"; score_adj = +3
        desc = f"IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% ({ratio:.2f}x) — fair pricing"
    elif ratio <= 1.60:
        signal = "RICH"; score_adj = -8
        desc = f"IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% ({ratio:.2f}x) — options pricey ⚠️"
    else:
        signal = "VERY_RICH"; score_adj = -18
        desc = f"IV {iv_pct:.0f}% vs HV {hv_pct:.0f}% ({ratio:.2f}x) — options very expensive ❌"

    return {
        "hv30": round(hv30, 4),
        "hv30_pct": hv_pct,
        "iv_hv_ratio": ratio,
        "signal": signal,
        "score_adj": score_adj,
        "description": desc,
    }


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)

    print("=== Volatility Analysis Test ===\n")
    for sym in ["AAPL", "NVDA", "TSLA"]:
        tk = yf.Ticker(sym)
        hist = tk.history(period="1y", interval="1d")
        if hist.empty:
            print(f"{sym}: no data"); continue

        yz  = yang_zhang_hv(hist, window=30)
        gk  = garman_klass_hv(hist, window=30)

        # Mock IV from option chain
        try:
            exp = tk.options[0]
            chain = tk.option_chain(exp)
            price = float(hist["Close"].iloc[-1])
            atm = chain.calls[abs(chain.calls["strike"] - price) < price * 0.07]
            iv = float(atm["impliedVolatility"].dropna().mean()) if not atm.empty else 0.30
        except:
            iv = 0.30

        analysis = hv_iv_analysis(hist, iv)
        print(f"{sym}:")
        print(f"  Yang-Zhang HV30 : {yz*100:.1f}%" if yz else "  Yang-Zhang HV30 : N/A")
        print(f"  Garman-Klass HV : {gk*100:.1f}%" if gk else "  Garman-Klass HV : N/A")
        print(f"  IV              : {iv*100:.1f}%")
        print(f"  Signal          : {analysis['signal']} (ratio {analysis['iv_hv_ratio']}x, score adj {analysis['score_adj']:+d})")
        print(f"  {analysis['description']}\n")
