#!/usr/bin/env python3
"""Simple scan to show top scores"""

from scanner import run_scan
from datetime import datetime, timezone, timedelta

EST = timezone(timedelta(hours=-5))

print("\n" + "="*70)
print("📊 SIMPLE SCAN")
print("="*70)

base = run_scan()
results = base.get("all_results", [])

print(f"\nTime: {datetime.now(EST).strftime('%m/%d %H:%M:%S EST')}")
print(f"Regime: {base.get('regime', {}).get('regime', 'UNKNOWN')}")
print(f"Total stocks scanned: {len(results)}")
print()

# Sort by best score (max of call/put)
results_sorted = sorted(results, key=lambda x: max(x.get("call_score", 0), x.get("put_score", 0)), reverse=True)

print(f"{'Symbol':<8} {'Call':<8} {'Put':<8} {'Best':<8} {'Type':<6}")
print("-"*50)

for r in results_sorted[:10]:
    call = r.get("call_score", 0)
    put = r.get("put_score", 0)
    best = max(call, put)
    typ = "CALL" if call >= put else "PUT"
    print(f"{r['symbol']:<8} {call:>6.0f}   {put:>6.0f}   {best:>6.0f}   {typ}")

print()
print(f"Highest score: {max([max(r.get('call_score',0), r.get('put_score',0)) for r in results]):.0f}/100")
print(f"MIN_SCORE for 60DTE: 60")
print(f"Stocks above 60: {len([r for r in results if max(r.get('call_score',0), r.get('put_score',0)) >= 60])}")
print("="*70 + "\n")
