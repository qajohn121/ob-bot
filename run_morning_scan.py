#!/usr/bin/env python3
"""Run morning scan and display results with EST timestamps"""

from scanner import run_scan_dte_profiles
from paper_trader import log_trade, log_iron_condor
from datetime import datetime, timezone, timedelta

EST = timezone(timedelta(hours=-5))

print("\n" + "="*70)
print("  🤖 MORNING SCAN REPORT")
print("="*70)

result = run_scan_dte_profiles()

scan_time = datetime.now(EST).strftime("%m/%d %H:%M:%S EST")
regime = result['regime']

print(f"\n📍 Scan Time: {scan_time}")
print(f"📊 Regime: {regime['regime']} | VIX: {regime['vix']:.1f} | Bias: {regime['bias']}")
print(f"📌 Size Multiplier: {regime['size_mult']:.1f}x | {regime['note']}")

picks = result.get('dte_picks', {})
print(f"\n{'DTE':<8} {'Symbol':<8} {'Score':<10} {'Type':<8} {'Strike':<8} {'Expiry':<12} {'Status':<20}")
print("-"*80)

auto_trades_made = []

for dte_tier in ['0DTE', '7DTE', '21DTE', '30DTE', '60DTE']:
    pick = picks.get(dte_tier)
    if pick:
        symbol = pick.get('symbol', 'N/A')
        score = pick.get('score', 0)
        direction = pick.get('direction', 'UNKNOWN')

        # Check for Iron Condor
        if pick.get('ic_available'):
            entry = pick.get('entry_iron_condor', {})
            strike = entry.get('short_call_strike', 'N/A')
            expiry = entry.get('expiry', 'N/A')
            trade_type = "IC"

            # Log the Iron Condor trade
            try:
                log_iron_condor(
                    symbol=symbol,
                    short_call_strike=entry.get('short_call_strike'),
                    long_call_strike=entry.get('long_call_strike'),
                    short_put_strike=entry.get('short_put_strike'),
                    long_put_strike=entry.get('long_put_strike'),
                    expiry=expiry,
                    credit=entry.get('credit', 0),
                    dte_profile=dte_tier,
                    reason=f"Auto-trade from morning scan (score {score:.0f})"
                )
                status = "✅ LOGGED (IC)"
                auto_trades_made.append(f"{symbol} IC")
            except Exception as e:
                status = f"⚠️  Error: {str(e)[:30]}"
        else:
            # Regular call/put
            entry = pick.get('entry_call') if direction == 'CALL' else pick.get('entry_put')
            if entry:
                strike = entry.get('strike', 'N/A')
                expiry = entry.get('expiry', 'N/A')
                price = entry.get('price', 0)

                # Log the trade
                try:
                    log_trade(
                        symbol=symbol,
                        direction=direction,
                        strike=strike,
                        expiry=expiry,
                        entry_price=price,
                        dte_profile=dte_tier,
                        reason=f"Auto-trade from morning scan (score {score:.0f})"
                    )
                    status = f"✅ LOGGED ({direction})"
                    auto_trades_made.append(f"{symbol} {direction}")
                except Exception as e:
                    status = f"⚠️  Error: {str(e)[:30]}"
            else:
                strike = "N/A"
                expiry = "N/A"
                status = "⚠️  No entry data"

        print(f"{dte_tier:<8} {symbol:<8} {score:>7.0f}/100  {trade_type:<8} {strike:<8} {expiry:<12} {status}")
    else:
        print(f"{dte_tier:<8} {'--':<8} {'--':<10} {'--':<8} {'--':<8} {'--':<12} {'❌ Below threshold'}")

print("\n" + "="*70)
print(f"✅ Auto-Trades Executed: {len(auto_trades_made)}")
if auto_trades_made:
    for trade in auto_trades_made:
        print(f"   • {trade}")
print(f"\n📊 Dashboard: http://170.9.254.97:3003")
print("="*70 + "\n")
