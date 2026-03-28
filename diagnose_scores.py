#!/usr/bin/env python3
"""Diagnose why no stocks are meeting threshold"""

from scanner import SCAN_TICKERS, fetch_ticker_data, score_for_call, score_for_put, get_market_context
from datetime import datetime, timezone, timedelta

EST = timezone(timedelta(hours=-5))

print("\n" + "="*70)
print("📊 SCORE DIAGNOSTIC")
print("="*70)

market_intel = get_market_context()
regime = market_intel.get('regime', {})

vix = regime.get('vix') if regime.get('vix') else 'N/A'
vix_str = f"{vix:.1f}" if isinstance(vix, (int, float)) else vix

print(f"\nCurrent Regime: {regime.get('regime', 'UNKNOWN')} | VIX: {vix_str}")
print(f"MIN_SCORE thresholds: 0DTE=75, 7DTE=70, 21DTE=68, 30DTE=65, 60DTE=60")
print()

scores = []
for symbol in SCAN_TICKERS:
    try:
        data = fetch_ticker_data(symbol)
        call_score, call_reasons = score_for_call(symbol, data, market_intel, regime)
        put_score, put_reasons = score_for_put(symbol, data, market_intel, regime)

        best_score = max(call_score, put_score)
        best_type = "CALL" if call_score >= put_score else "PUT"

        scores.append({
            'symbol': symbol,
            'score': best_score,
            'type': best_type,
            'call': call_score,
            'put': put_score
        })
    except Exception as e:
        print(f"ERROR {symbol}: {str(e)[:60]}")

# Sort by score
scores.sort(key=lambda x: x['score'], reverse=True)

print(f"{'Symbol':<8} {'CALL':<6} {'PUT':<6} {'Best':<8} {'Type':<6} {'Status':<20}")
print("-"*70)

thresholds = {'0DTE': 75, '7DTE': 70, '21DTE': 68, '30DTE': 65, '60DTE': 60}

for s in scores[:10]:  # Show top 10
    status = "✅ Qualifies for 60DTE" if s['score'] >= 60 else "⚠️  Below all thresholds"
    print(f"{s['symbol']:<8} {s['call']:>5.0f}  {s['put']:>5.0f}  {s['score']:>6.0f}     {s['type']:<6} {status}")

print()
print(f"Top Score: {scores[0]['score'] if scores else 0:.0f}/100")
print(f"Stocks above 60 threshold: {len([s for s in scores if s['score'] >= 60])}")
print("="*70 + "\n")
