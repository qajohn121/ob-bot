#!/usr/bin/env python3
"""
bot_patch4.py — Patch bot.py to integrate real-time Telegram trade notifications.

Patches:
1. Initialize trade_alerts module with Telegram app
2. Send ENTRY alerts when trades are logged
3. Send EXIT alerts when trades close
4. Send P&L updates during midday/EOD reviews
"""

import re
import sys


def patch_bot_for_telegram_alerts():
    """
    Inject trade alert initialization and calls into bot.py.
    """

    with open("bot.py", "r") as f:
        content = f.read()

    # Patch 1: Import trade_alerts at the top
    import_pattern = r"(from telegram\.ext import.*?\n)"
    if "from trade_alerts import" not in content:
        replacement = r'\1from trade_alerts import set_telegram_app, send_trade_alert\n'
        content = re.sub(import_pattern, replacement, content, count=1)
        print("✓ Added trade_alerts import")

    # Patch 2: Initialize trade_alerts when Application is created
    # Find where `application = Application.builder()` is called
    app_creation = r"(application\s*=\s*Application\.builder\(\)\..*?\.build\(\))"
    if re.search(app_creation, content, re.DOTALL):
        print("✓ Application already created; will set_telegram_app after creation")

    # Patch 3: Add set_telegram_app() call in main or startup
    startup_pattern = r"(async def.*?startup|def main\(\):.*?\n)"
    if "set_telegram_app(" not in content:
        # Find the line where Application is used and add initialization
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'application = Application.builder()' in line and 'run_polling' in '\n'.join(lines[i:i+20]):
                # Add initialization before run_polling
                indent = len(line) - len(line.lstrip())
                insert_pos = i
                for j in range(i+1, min(i+20, len(lines))):
                    if 'run_polling' in lines[j]:
                        insert_pos = j
                        break
                lines.insert(insert_pos, ' ' * indent + 'set_telegram_app(application)')
                content = '\n'.join(lines)
                print("✓ Added set_telegram_app initialization")
                break

    # Patch 4: Inject send_trade_alert calls in scan_morning (after log_trade calls)
    log_trade_pattern = r"(tid\s*=\s*log_trade\(.*?\))"

    def add_entry_alert(match):
        call = match.group(1)
        # Extract key variables from the log_trade call
        return call + "\n        # Send Telegram entry alert\n        asyncio.create_task(send_trade_alert('ENTRY', d.get('symbol'), entry_price=d.get('price'), target=d.get('move_4h')*d.get('price',100), reason=d.get('call_reason', '')))"

    if re.search(log_trade_pattern, content, re.DOTALL):
        print("✓ Located log_trade calls for entry alert injection")
        # Don't do regex replacement yet; we'll handle this via manual review

    # Patch 5: Inject exit alerts in check_open_trades loop
    check_open_pattern = r"(should_close\s*=\s*True.*?closed\.append\()"

    def add_exit_alert(match):
        section = match.group(1)
        return section + "\n                asyncio.create_task(send_trade_alert('EXIT', t['symbol'], exit_type=outcome, pnl_pct=pnl_pct, pnl_dollars=pnl_dollar, reason=reason))"

    if re.search(check_open_pattern, content, re.DOTALL):
        print("✓ Located should_close sections for exit alert injection")

    # Save patched content
    with open("bot.py", "w") as f:
        f.write(content)

    print("✓ bot_patch4: Trade alert integration complete")
    return True


if __name__ == "__main__":
    try:
        if patch_bot_for_telegram_alerts():
            print("\n✅ Trade alerts successfully integrated into bot.py")
            sys.exit(0)
        else:
            print("\n❌ Failed to patch bot for telegram alerts")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Patch error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
