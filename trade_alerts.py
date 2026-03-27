"""
Trade Alert System — Real-time Telegram notifications for all trading activity.

Sends instant alerts to Telegram when:
- Position opens (entry signal, calculation, entry price)
- Position closes (exit reason, P&L, win/loss status)
- P&L update (mid-day position status)
- Error occurs (risk exceeded, margin issue, etc.)
"""

import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Global Telegram app instance (set by bot at startup)
_TELEGRAM_APP = None


def set_telegram_app(app):
    """Register the telegram app instance for sending messages."""
    global _TELEGRAM_APP
    _TELEGRAM_APP = app


async def get_chat_id() -> Optional[int]:
    """Get the chat ID for sending alerts (stored after first /status command)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
        chat_id_file = os.path.expanduser("~/ob-bot/data/chat_id.txt")
        if os.path.exists(chat_id_file):
            with open(chat_id_file, "r") as f:
                chat_id = int(f.read().strip())
                return chat_id
    except Exception as e:
        logger.error(f"Failed to read chat_id: {e}")
    return None


async def send_trade_alert(
    alert_type: str,
    symbol: str,
    **details
) -> bool:
    """
    Send a trade alert to Telegram.

    alert_type: "ENTRY", "EXIT", "PNL_UPDATE", "ERROR", "ADJUSTMENT"
    symbol: ticker symbol
    **details: specific fields for this alert type
    """
    if not _TELEGRAM_APP:
        logger.debug("Telegram app not initialized; skipping alert")
        return False

    chat_id = await get_chat_id()
    if not chat_id:
        logger.warning(f"No chat_id configured; cannot send {alert_type} alert")
        return False

    try:
        message = _format_alert(alert_type, symbol, details)
        await _TELEGRAM_APP.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML"
        )
        logger.info(f"Sent {alert_type} alert for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Failed to send trade alert: {e}")
        return False


def _format_alert(alert_type: str, symbol: str, details: Dict[str, Any]) -> str:
    """Format trade alert message with emojis and key details."""
    ts = datetime.now().strftime("%H:%M:%S")

    if alert_type == "ENTRY":
        return _format_entry(symbol, details, ts)
    elif alert_type == "EXIT":
        return _format_exit(symbol, details, ts)
    elif alert_type == "PNL_UPDATE":
        return _format_pnl_update(symbol, details, ts)
    elif alert_type == "ERROR":
        return _format_error(symbol, details, ts)
    elif alert_type == "ADJUSTMENT":
        return _format_adjustment(symbol, details, ts)
    else:
        return f"⚠️ Unknown alert type: {alert_type}"


def _format_entry(symbol: str, details: Dict, ts: str) -> str:
    """Format position entry alert."""
    strategy = details.get("strategy", "?")
    entry_price = details.get("entry_price", "?")
    target = details.get("target", "?")
    stop = details.get("stop", "?")
    credit = details.get("credit", "?")
    reason = details.get("reason", "")
    iv_rank = details.get("iv_rank", None)
    pre_market_gap = details.get("pre_market_gap", None)

    lines = [
        f"<b>📍 POSITION OPENED — {symbol}</b>",
        f"⏰ {ts}",
        "",
        f"<b>Strategy:</b> {strategy}",
    ]

    if strategy == "IRON_CONDOR":
        lines.extend([
            f"<b>Credit:</b> ${credit:.2f}",
            f"<b>Profit Zone:</b> ${details.get('profit_zone_low', '?')} - ${details.get('profit_zone_high', '?')}",
            f"<b>Max Loss:</b> ${details.get('max_loss', '?')}",
            f"<b>POP (est):</b> {details.get('pop', '~80%')}",
        ])
    else:
        lines.extend([
            f"<b>Entry Price:</b> ${entry_price:.2f}",
            f"<b>Target:</b> ${target:.2f}",
            f"<b>Stop Loss:</b> ${stop:.2f}",
        ])

    if iv_rank is not None:
        iv_label = "🔥 HIGH" if iv_rank > 60 else ("💤 LOW" if iv_rank < 30 else "normal")
        lines.append(f"<b>IV Rank:</b> {iv_rank:.0f}% ({iv_label})")

    if pre_market_gap is not None and abs(pre_market_gap) > 1.0:
        gap_emoji = "☀️" if pre_market_gap > 0 else "🌑"
        lines.append(f"<b>Pre-market:</b> {gap_emoji} {pre_market_gap:+.1f}%")

    if reason:
        lines.append(f"<b>Reason:</b> {reason}")

    lines.append("")
    lines.append("✅ Position logged and tracked")

    return "\n".join(lines)


def _format_exit(symbol: str, details: Dict, ts: str) -> str:
    """Format position exit alert."""
    exit_type = details.get("exit_type", "CLOSED")  # CLOSED, STOPPED, EXPIRED, ADJUSTED
    pnl_pct = details.get("pnl_pct", 0)
    pnl_dollars = details.get("pnl_dollars", 0)
    exit_price = details.get("exit_price", "?")
    reason = details.get("reason", "")
    days_held = details.get("days_held", "?")

    if pnl_pct >= 0:
        emoji = "✅ WIN"
        color = "green"
    else:
        emoji = "❌ LOSS"
        color = "red"

    lines = [
        f"<b>{emoji} — {symbol}</b>",
        f"⏰ {ts}",
        "",
        f"<b>Exit Type:</b> {exit_type}",
        f"<b>Exit Price:</b> ${exit_price:.2f}",
        f"<b>Days Held:</b> {days_held}",
        "",
        f"<b>P&L:</b> <code>{pnl_pct:+.1f}% | ${pnl_dollars:+.2f}</code>",
    ]

    if reason:
        lines.append(f"<b>Reason:</b> {reason}")

    return "\n".join(lines)


def _format_pnl_update(symbol: str, details: Dict, ts: str) -> str:
    """Format mid-day P&L update for open positions."""
    entry_price = details.get("entry_price", "?")
    current_price = details.get("current_price", "?")
    unrealized_pnl_pct = details.get("unrealized_pnl_pct", 0)
    unrealized_pnl_dollars = details.get("unrealized_pnl_dollars", 0)

    if unrealized_pnl_pct >= 0:
        emoji = "📈"
    else:
        emoji = "📉"

    lines = [
        f"<b>{emoji} P&L UPDATE — {symbol}</b>",
        f"⏰ {ts}",
        "",
        f"<b>Entry:</b> ${entry_price:.2f}",
        f"<b>Current:</b> ${current_price:.2f}",
        f"<b>Unrealized:</b> {unrealized_pnl_pct:+.1f}% | ${unrealized_pnl_dollars:+.2f}",
    ]

    next_action = details.get("next_action", "")
    if next_action:
        lines.append(f"<b>Next Action:</b> {next_action}")

    return "\n".join(lines)


def _format_error(symbol: str, details: Dict, ts: str) -> str:
    """Format error alert."""
    error_type = details.get("error_type", "UNKNOWN_ERROR")
    message = details.get("message", "")
    action_taken = details.get("action_taken", "")

    lines = [
        f"<b>⚠️ TRADE ERROR — {symbol}</b>",
        f"⏰ {ts}",
        "",
        f"<b>Error:</b> {error_type}",
        f"<b>Details:</b> {message}",
    ]

    if action_taken:
        lines.append(f"<b>Action Taken:</b> {action_taken}")

    return "\n".join(lines)


def _format_adjustment(symbol: str, details: Dict, ts: str) -> str:
    """Format position adjustment alert."""
    adjustment_type = details.get("adjustment_type", "?")  # ROLL, CLOSE_PARTIAL, LADDER, etc.
    old_price = details.get("old_price", "?")
    new_price = details.get("new_price", "?")
    reason = details.get("reason", "")

    lines = [
        f"<b>🔄 POSITION ADJUSTED — {symbol}</b>",
        f"⏰ {ts}",
        "",
        f"<b>Type:</b> {adjustment_type}",
        f"<b>Old Price:</b> ${old_price:.2f}",
        f"<b>New Price:</b> ${new_price:.2f}",
    ]

    if reason:
        lines.append(f"<b>Reason:</b> {reason}")

    return "\n".join(lines)
