#!/bin/bash

# Iron Condor Phase 1 Monitoring Dashboard
# Tracks performance of IC trades vs naked options

VPS_USER="ubuntu"
VPS_IP="170.9.254.97"
VPS_SSH_KEY="$HOME/.ssh/oracle_key"
VPS_BOT_DIR="/home/ubuntu/ob-bot"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
}

print_section() {
    echo ""
    echo -e "${YELLOW}▶ $1${NC}"
}

# Get monitoring data from VPS
get_vps_data() {
    ssh -i "$VPS_SSH_KEY" "$VPS_USER@$VPS_IP" "python3 << 'PYTHON_SCRIPT'
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

db_path = Path('/home/ubuntu/ob-bot/data/trades.db')

def get_stats():
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get all-time stats
    all_time = c.execute('''
        SELECT
            status,
            recommendation_source,
            COUNT(*) as count,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl
        FROM trades
        WHERE status IN ('WIN', 'LOSS')
        GROUP BY status, recommendation_source
    ''').fetchall()

    # Get today's stats
    today = datetime.now().strftime('%Y-%m-%d')
    today_stats = c.execute('''
        SELECT
            status,
            recommendation_source,
            COUNT(*) as count,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE DATE(created_at) = ? AND status IN ('WIN', 'LOSS')
        GROUP BY status, recommendation_source
    ''', (today,)).fetchall()

    # Get IC-specific stats
    ic_stats = c.execute('''
        SELECT
            status,
            COUNT(*) as count,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE spread_type = 'IRON CONDOR' AND status IN ('WIN', 'LOSS')
    ''').fetchall()

    # Get naked stats (for comparison)
    naked_stats = c.execute('''
        SELECT
            status,
            COUNT(*) as count,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE spread_type = '' AND status IN ('WIN', 'LOSS') AND direction IN ('CALL', 'PUT')
    ''').fetchall()

    # Get open trades
    open_trades = c.execute('''
        SELECT
            symbol,
            direction,
            spread_type,
            entry_price,
            pnl_pct,
            created_at
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY created_at DESC
    ''').fetchall()

    # Get confidence scores
    confidence = c.execute('''
        SELECT * FROM recommendation_confidence
    ''').fetchall()

    conn.close()

    print(json.dumps({
        'all_time': [dict(row) for row in all_time],
        'today': [dict(row) for row in today_stats],
        'ic_stats': [dict(row) for row in ic_stats],
        'naked_stats': [dict(row) for row in naked_stats],
        'open_trades': [dict(row) for row in open_trades],
        'confidence': [dict(row) for row in confidence],
        'timestamp': datetime.now().isoformat()
    }))
PYTHON_SCRIPT
"
}

# Main display
clear
print_header "🎯 IRON CONDOR PHASE 1 MONITORING DASHBOARD"

echo -e "${GREEN}Fetching live data from VPS...${NC}"
DATA=$(get_vps_data)

# Parse JSON data
TIMESTAMP=$(echo "$DATA" | python3 -c "import sys, json; print(json.load(sys.stdin)['timestamp'])")

print_section "📊 Real-time Status"
echo "Bot Status: $(ssh -i $VPS_SSH_KEY $VPS_USER@$VPS_IP "sudo systemctl is-active ob-bot" 2>/dev/null || echo "OFFLINE")"
echo "Last Updated: $TIMESTAMP"
echo "Database: /home/ubuntu/ob-bot/data/trades.db"

print_section "🎯 IRON CONDOR vs NAKED COMPARISON"
echo ""
echo "Parsing performance data..."

python3 << 'PYTHON_END'
import json
import sys

data_str = ''''"$DATA"''''
data = json.loads(data_str)

# Calculate IC vs Naked stats
ic_wins = sum(1 for row in data['ic_stats'] if row['status'] == 'WIN')
ic_losses = sum(1 for row in data['ic_stats'] if row['status'] == 'LOSS')
ic_total = ic_wins + ic_losses
ic_wr = (ic_wins / ic_total * 100) if ic_total > 0 else 0
ic_avg_pnl = sum(row['avg_pnl'] * row['count'] for row in data['ic_stats']) / ic_total if ic_total > 0 else 0

naked_wins = sum(1 for row in data['naked_stats'] if row['status'] == 'WIN')
naked_losses = sum(1 for row in data['naked_stats'] if row['status'] == 'LOSS')
naked_total = naked_wins + naked_losses
naked_wr = (naked_wins / naked_total * 100) if naked_total > 0 else 0
naked_avg_pnl = sum(row['avg_pnl'] * row['count'] for row in data['naked_stats']) / naked_total if naked_total > 0 else 0

print("\n📈 IRON CONDOR PERFORMANCE (All-Time)")
if ic_total > 0:
    print(f"   Trades: {ic_total} ({ic_wins}W/{ic_losses}L)")
    print(f"   Win Rate: {ic_wr:.1f}%")
    print(f"   Avg P&L: {ic_avg_pnl:+.1f}%")
else:
    print("   No IC trades yet")

print("\n📉 NAKED OPTION PERFORMANCE (All-Time)")
if naked_total > 0:
    print(f"   Trades: {naked_total} ({naked_wins}W/{naked_losses}L)")
    print(f"   Win Rate: {naked_wr:.1f}%")
    print(f"   Avg P&L: {naked_avg_pnl:+.1f}%")
else:
    print("   No naked trades yet")

if ic_total > 0 and naked_total > 0:
    print("\n📊 IMPROVEMENT vs NAKED:")
    print(f"   Win Rate: {ic_wr - naked_wr:+.1f}% (target: +15-20%)")
    print(f"   Avg Return: {ic_avg_pnl - naked_avg_pnl:+.1f}%")

# Confidence scores
print("\n\n🧬 RECOMMENDATION SOURCE CONFIDENCE")
for conf in data['confidence']:
    src = conf['recommendation_source']
    conf_val = conf['confidence']
    rewards = conf['reward_count']
    punishments = conf['punishment_count']

    if src in ['ic_pick', 'top_call', 'top_put', 'dte_pick', 'dte_spread']:
        conf_bar = "█" * (conf_val // 5) + "░" * ((100 - conf_val) // 5)
        trend = "📈" if punishments == 0 else ("📉" if rewards == 0 else "➡️")
        print(f"   {trend} {src:15} {conf_val:3d}% {conf_bar} ({rewards}W/{punishments}L)")

# Open trades
open_count = len(data['open_trades'])
print(f"\n\n📌 OPEN TRADES: {open_count}")
if open_count > 0:
    for trade in data['open_trades'][:5]:  # Show first 5
        pnl = trade['pnl_pct'] or 0
        pnl_icon = "🟢" if pnl > 0 else ("🔴" if pnl < -30 else "🟡")
        print(f"   {pnl_icon} {trade['symbol']} {trade['direction']} {trade['spread_type'] or 'naked'} | P&L: {pnl:+.1f}%")
    if open_count > 5:
        print(f"   ... and {open_count - 5} more")

PYTHON_END

print ""
print_section "📋 HOW TO MONITOR"
echo "1. Run this script daily: $0"
echo "2. Watch IC win rate target: >75% (vs naked 55%)"
echo "3. Check confidence trends: IC should have high confidence if working"
echo "4. Monitor open trades during market hours"
echo "5. Check logs: ssh -i ~/.ssh/oracle_key ubuntu@170.9.254.97 'tail -20 ~/ob-bot/data/ob_bot.log'"

print_section "🔍 DETAILED ANALYSIS"
echo "View full stats: sqlite3 /home/ubuntu/ob-bot/data/trades.db '.mode column' 'SELECT * FROM trades LIMIT 10;'"
echo ""
echo "Group by recommendation source:"
echo "  sqlite3 /home/ubuntu/ob-bot/data/trades.db "
echo "    'SELECT recommendation_source, status, COUNT(*) FROM trades GROUP BY recommendation_source, status;'"
echo ""
echo "Iron Condor-only stats:"
echo "  sqlite3 /home/ubuntu/ob-bot/data/trades.db "
echo "    'SELECT status, COUNT(*), AVG(pnl_pct) FROM trades WHERE spread_type=\"IRON CONDOR\" GROUP BY status;'"

print_section "✅ NEXT STEPS"
echo "1. Monitor for 1 week of trading (5 market days)"
echo "2. Collect minimum 20+ IC trades for statistical significance"
echo "3. Compare IC win rate to naked option win rate"
echo "4. If IC>70% WR and naked<60%, Phase 1 is successful"
echo "5. Then proceed to Phase 2: Volatility Gating"

echo ""
echo -e "${GREEN}Dashboard complete. Check again tomorrow!${NC}"
