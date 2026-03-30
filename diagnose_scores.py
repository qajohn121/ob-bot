#!/usr/bin/env python3
"""Diagnose why no stocks are meeting threshold"""

from scanner import SCAN_TICKERS, fetch_ticker_data, score_for_call, score_for_put, get_market_context, MIN_SCORE, MIN_CONVICTION_GAP
from datetime import datetime, timezone, timedelta

EST = timezone(timedelta(hours=-5))

print("\n" + "="*70)
print("📊 SCORE DIAGNOSTIC - AGGRESSIVE MODE")
print("="*70)

market_intel = get_market_context()
regime = market_intel.get('regime', {})

vix = regime.get('vix') if regime.get('vix') else 'N/A'
vix_str = f"{vix:.1f}" if isinstance(vix, (int, float)) else vix

print(f"\nCurrent Regime: {regime.get('regime', 'UNKNOWN')} | VIX: {vix_str}")
print(f"MIN_SCORE thresholds: 0DTE={MIN_SCORE['0DTE']}, 7DTE={MIN_SCORE['7DTE']}, 21DTE={MIN_SCORE['21DTE']}, 30DTE={MIN_SCORE['30DTE']}, 60DTE={MIN_SCORE['60DTE']}")
print(f"MIN_CONVICTION_GAP: 0DTE={MIN_CONVICTION_GAP['0DTE']}, 7DTE={MIN_CONVICTION_GAP['7DTE']}, 21DTE={MIN_CONVICTION_GAP['21DTE']}, 30DTE={MIN_CONVICTION_GAP['30DTE']}, 60DTE={MIN_CONVICTION_GAP['60DTE']}")
print()

scores = []
for symbol in SCAN_TICKERS:
    try:
        data = fetch_ticker_data(symbol)
        if data is None:
            continue
        call_score, call_reasons = score_for_call(data)
        put_score, put_reasons = score_for_put(data)

        best_score = max(call_score, put_score)
        best_type = "CALL" if call_score >= put_score else "PUT"
        conviction_gap = abs(call_score - put_score)

        scores.append({
            'symbol': symbol,
            'score': best_score,
            'type': best_type,
            'call': call_score,
            'put': put_score,
            'conviction_gap': conviction_gap,
            'call_reason': call_reasons,
            'put_reason': put_reasons
        })
    except Exception as e:
        print(f"ERROR {symbol}: {str(e)[:80]}")

# Sort by score
scores.sort(key=lambda x: x['score'], reverse=True)

print(f"{'Symbol':<8} {'CALL':<6} {'PUT':<6} {'Best':<8} {'Gap':<6} {'Type':<6} {'Status':<25}")
print("-"*85)

for s in scores[:15]:  # Show top 15
    min_threshold = MIN_SCORE.get('60DTE', 48)  # Lowest threshold
    status = "✅ QUALIFIES" if s['score'] >= min_threshold else "❌ Below minimum"
    gap_status = "✓" if s['conviction_gap'] >= 5 else "✗ Low"
    print(f"{s['symbol']:<8} {s['call']:>5.0f}  {s['put']:>5.0f}  {s['score']:>6.0f}    {s['conviction_gap']:>5.0f} ({gap_status}) {s['type']:<6} {status}")

print()
print(f"Top Score: {scores[0]['score'] if scores else 0:.0f}/100")
min_aggressive = MIN_SCORE.get('60DTE', 48)
print(f"Stocks above {min_aggressive} threshold (AGGRESSIVE): {len([s for s in scores if s['score'] >= min_aggressive])}")
print(f"Stocks with good conviction gap (>5): {len([s for s in scores if s['conviction_gap'] >= 5])}")
print("="*70 + "\n")

# Show top 3 with details
if scores:
    print("🔍 TOP 3 DETAILED BREAKDOWN:")
    for i, s in enumerate(scores[:3], 1):
        print(f"\n#{i} {s['symbol']} - Score: {s['score']:.0f} (CALL: {s['call']:.0f}, PUT: {s['put']:.0f})")
        print(f"   Call Reason: {s['call_reason']}")
        print(f"   Put Reason: {s['put_reason']}")
        print(f"   Conviction Gap: {s['conviction_gap']:.0f}")
        print(f"   Qualifies for: ", end="")
        qualifications = []
        for dte, threshold in sorted(MIN_SCORE.items(), key=lambda x: int(x[0].rstrip('DTE'))):
            if s['score'] >= threshold:
                qualifications.append(dte)
        if qualifications:
            print(", ".join(qualifications))
        else:
            print("None (too low)")
