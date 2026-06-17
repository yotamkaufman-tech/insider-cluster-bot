"""
Runs at 08:00 AM ET every trading day.
- Finds signals scheduled for today
- Checks earnings filter
- Fetches current prices for all open positions
- Computes equal-weight trim amounts
- Sends full rebalance brief to Telegram
"""
from datetime import date
from .db import fetchall, fetchone, execute
from .business_rules import (
    get_current_price, get_prior_close,
    get_market_cap, get_avg_daily_volume_notional,
    check_earnings_within_n_days, nth_trading_day_after,
)
from .telegram_alerts import send_message
from .config import MIN_PURCHASE, LIMIT_SLIPPAGE, STOP_LOSS_PCT, HOLD_DAYS


def get_portfolio_value(positions) -> float:
    """Sum current_value of all open positions."""
    return sum(float(p["allocated_capital"]) for p in positions)


def main():
    today = date.today()

    # 1. Find signals with entry_date = today
    pending = fetchall(
        "SELECT * FROM signals WHERE entry_date=%s AND status='PENDING'",
        (today,)
    )

    # 2. Check exits due today (Day 6)
    exits_due = fetchall(
        "SELECT * FROM positions WHERE exit_date_target=%s AND status='OPEN'",
        (today,)
    )

    # 3. Build exit alert
    if exits_due:
        exit_lines = ["<b>🔔 EXIT AT OPEN TODAY (Day 6)</b>"]
        for p in exits_due:
            exit_lines.append(
                f"  • Sell ALL {p['ticker']} — opened {p['entry_date']}, "
                f"entry ${float(p['entry_price']):,.2f}"
            )
        send_message("\n".join(exit_lines))

    if not pending:
        if not exits_due:
            send_message(f"📋 Pre-open {today}: No new signals. No exits due.")
        return

    # 4. Get all open positions
    open_positions = fetchall(
        "SELECT * FROM positions WHERE status='OPEN'"
    )

    # 5. Fetch fresh prices for open positions
    position_prices = {}
    for p in open_positions:
        price = get_current_price(p["ticker"])
        position_prices[p["ticker"]] = price if price else float(p["entry_price"])

    # 6.
