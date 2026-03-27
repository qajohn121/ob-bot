# Reward/Punishment System — Bot Self-Learning

## Overview

The bot now has a **reward/punishment confidence system** that learns from trading outcomes. Each recommendation source (top_call, top_put, dte_pick, dte_spread) gets a confidence score (0-100%) that automatically adjusts based on historical performance.

**The user's explicit requirement:** "I want the bot to understand if we make losses we won't be able to sustain this the bot needs to know that"

**The solution:** When a recommendation source loses money, its confidence drops. This causes the bot to make fewer recommendations from that source, reducing risk. Winning sources get rewarded with higher confidence and more recommendations.

---

## How It Works

### 1. Confidence Initialization
Each recommendation source starts at **50% confidence**:
- `top_call` — 50%
- `top_put` — 50%
- `dte_pick` — 50%
- `dte_spread` — 50%

These are stored in the `recommendation_confidence` table in `trades.db`.

### 2. Reward Logic (Trade Wins)
When a trade **closes with profit**, the source gets rewarded:

| Profit Level | Reward |
|---|---|
| +50% or more | +5 points |
| +30% to +50% | +4 points |
| +10% to +30% | +3 points |
| +0% to +10% | +1 point |

**Example:** A `top_call` recommendation wins +35% → top_call confidence goes from 50% → 54%

### 3. Punishment Logic (Trade Losses)
When a trade **closes with loss**, the source gets punished:

| Loss Level | Punishment |
|---|---|
| -50% or worse | -10 points |
| -30% to -50% | -8 points |
| -10% to -30% | -5 points |
| 0% to -10% | -2 points |

**Example:** A `dte_pick` recommendation loses -35% → dte_pick confidence goes from 50% → 42%

**Key insight:** Losses are punished more harshly than wins are rewarded (asymmetric). This reflects the trading philosophy: "small frequent losses will kill us faster than small frequent wins will enrich us."

### 4. Confidence Capped at 0-100%
Confidence is bounded:
- Minimum: 0% (source is completely distrusted, recommendations suppressed)
- Maximum: 100% (source is fully trusted, no score reduction)

---

## Integration into Recommendation Scoring

### During Scan (scanner.py)

**Phase 1:** Fetch confidence scores for all sources
```python
rec_confidence = _get_recommendation_confidence()
# Returns: {"top_call": 50, "top_put": 50, "dte_pick": 50, ...}
```

**Phase 3b:** Apply confidence multiplier to DTE-picker recommendations
```python
dte_conf_mult = rec_confidence.get("dte_pick").get("confidence", 50) / 100.0
# If dte_pick confidence is 50%, all DTE-picker scores get multiplied by 0.5
for r in results:
    r["call_score"] = round(r["call_score"] * dte_conf_mult)
    r["put_score"] = round(r["put_score"] * dte_conf_mult)
```

### During Morning Scan (bot_patch3.py)

Before logging top_call and top_put, apply confidence multiplier:
```python
top_call_conf = rec_confidence.get("top_call").get("confidence", 50) / 100.0
top_put_conf = rec_confidence.get("top_put").get("confidence", 50) / 100.0

# Reduce scores for low-confidence sources
if top_call_conf < 1.0:
    for p in top_calls:
        p["call_score"] = max(0, round(p["call_score"] * top_call_conf))
```

### Result
Low-confidence sources have **lower scores** and are **less likely to be selected** as top recommendations.

---

## Trade Closure and Confidence Update

When a trade closes (via `check_open_trades()`):

1. Trade outcome determined: WIN or LOSS
2. P&L percentage calculated
3. Recommendation source looked up: `trade.recommendation_source`
4. Confidence updated: `update_recommendation_confidence(source, outcome, pnl_pct)`
5. Logged: `"Updated top_call confidence: 50→52 (WIN +2.5%)"`

---

## EOD Report: Confidence Visibility

Every EOD review (3:30 PM) shows confidence changes:

```
🧬 RECOMMENDATION SOURCE CONFIDENCE (Self-Learning)
The bot rewards winning recommendation types and punishes losers.

  📈 🚀 Top 5 Calls: Confidence 54% | Today: REWARDED (3W/1L, change +8)
  📉 💥 Top 5 Puts: Confidence 42% | Today: PUNISHED (1W/4L, change -18)
  ➡️ 🎯 DTE Picks: Confidence 50% | Today: NEUTRAL (2W/2L, change 0)
  📈 📐 Spreads: Confidence 58% | Today: REWARDED (2W/0L, change +8)

💡 How it works: Winning trades (+reward) boost confidence. Losing trades (-punishment)
reduce it. Low-confidence sources get lower recommendation scores tomorrow.
```

---

## Example Scenario

**Day 1 Morning:** All sources at 50% confidence

**Day 1 Trades:**
- top_call NVDA: WIN +45% (reward +4)
- top_put QQQ: LOSS -35% (punishment -8)
- dte_pick AMD: WIN +12% (reward +3)
- dte_spread SPY: LOSS -5% (punishment -2)

**Day 1 EOD Report shows:**
```
📈 Top 5 Calls: 54% (was 50%, +4)
📉 Top 5 Puts: 42% (was 50%, -8)
➡️ DTE Picks: 53% (was 50%, +3)
📉 Spreads: 48% (was 50%, -2)
```

**Day 2 Morning Scan:**
- Top 5 calls: NOT multiplied (54% ≈ full trust)
- Top 5 puts: scores × 0.84 (42% → reduced by 16%)
- DTE picks: scores × 0.53 (53% → slightly reduced)
- Spreads: scores × 0.48 (48% → heavily reduced)

**Result:** Top puts are less likely to be recommended tomorrow because they underperformed today.

---

## Safety Mechanisms

1. **Minimum score thresholds:** If confidence reduction drives all scores below `MIN_SCORE` for a DTE tier, **no recommendation is made for that tier**. This is intentional—when a source is very low confidence, we skip it entirely rather than make weak recommendations.

2. **Confidence floor at 0:** A source can't go below 0% confidence. Once at 0%, it's effectively off.

3. **All-time tracking:** Confidence is cumulative across days. One bad day won't destroy confidence (it's -10 at worst), but consistent losses will.

4. **Feedback indicator:** The `[conf 42%]` tag is added to reasons so the user can see when scores were adjusted due to low confidence.

---

## What the Bot "Knows"

The bot now implicitly understands:
- ✅ If a recommendation source consistently loses, DON'T recommend from it
- ✅ If we don't recommend from losing sources, we reduce total losses
- ✅ If we keep recommending from losing sources, we won't sustain the strategy long-term
- ✅ Therefore, automatically suppress losing sources via confidence reduction

This matches the user's explicit requirement: **the bot must understand that consistent losses are unsustainable.**

---

## Monitoring

Check confidence scores at any time:
```bash
ssh ubuntu@170.9.254.97 "cd ~/ob-bot && python3 -c \"
from paper_trader import get_recommendation_confidence
c = get_recommendation_confidence()
for src, data in c.items():
    print(f'{src}: {data[\"confidence\"]}% (wins:{data[\"reward_count\"]} losses:{data[\"punishment_count\"]})')
\""
```

---

## Files Modified

1. **paper_trader.py**
   - Added `recommendation_confidence` table to schema
   - Added `_init_confidence()` to initialize defaults
   - Added `get_recommendation_confidence()` to read scores
   - Added `update_recommendation_confidence()` to modify scores
   - Added `get_confidence_change_today()` to show daily changes
   - Modified `check_open_trades()` to call `update_recommendation_confidence()` when trades close

2. **scanner.py**
   - Added import for `_get_recommendation_confidence()`
   - Phase 1: Fetch confidence scores
   - Phase 3b: Apply confidence multiplier to DTE-picker scores
   - Return `rec_confidence` dict in scan results

3. **bot_patch3.py**
   - Morning scan: Apply confidence multiplier to top_call and top_put before logging
   - EOD report: Show confidence changes with REWARDED/PUNISHED/NEUTRAL status
   - Added explanation of how confidence system works

---

## Next Steps

The bot is now **self-learning**. To see it in action:

1. Run morning scans (10:00 AM)
2. Let trades close (auto-close at midday/EOD)
3. Check EOD report (3:30 PM) to see confidence changes
4. Monitor over 1-2 weeks as the system learns which recommendation sources work best
5. Notice that low-confidence sources get fewer recommendations

**The bot will naturally gravitate toward strategies that work and away from those that don't.**
