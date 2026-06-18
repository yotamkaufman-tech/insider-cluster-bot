from datetime import date, timedelta

from .db import fetchall, execute
from .telegram_alerts import send_message
from .business_rules import (
    get_current_price,
    get_market_cap,
    get_adv,
    has_upcoming_earnings,
    next_trading_day,
    STOP_LOSS_PCT,
    HOLD_DAYS,
    MIN_PURCHASE,
)

MIN_MARKET_CAP = 500_000_000   # $500M
MIN_ADV_SHARES = 500_000       # 500k shares/day


def check_filters(ticker):
    mc = get_market_cap(ticker)
    if mc and mc < MIN_MARKET_CAP:
        return False, f"market cap ${mc:,.0f} below minimum"
    adv = get_adv(ticker)
    if adv and adv < MIN_ADV_SHARES:
        return False, f"ADV {adv:,.0f} below minimum"
    if has_upcoming_earnings(ticker):
        return False, "earnings within 5 days"
    return True, None


def main():
    today = date.today()

    scheduled = fetchall(
        "SELECT * FROM signals WHERE status='PENDING' AND entry_date <= %s",
        (today,)
    )

    if not scheduled:
        send_message(f"Pre-open {today}: No new signals today.")
        return

    open_positions = fetchall("SELECT * FROM positions WHERE status='OPEN'")
    n_open = len(open_positions)

    # Mark current values
    total_value = 0.0
    for p in open_positions:
        price = get_current_price(p["ticker"])
        if price:
            current_val = float(p["shares"]) * price
            execute("UPDATE positions SET allocated_capital=%s WHERE id=%s",
                    (current_val, p["id"]))
            total_value += current_val
        else:
            total_value += float(p["allocated_capital"])

    lines = [f"<b>Pre-Open Brief {today}</b>"]

    for sig in scheduled:
        ticker = sig["ticker"]

        ok, reason = check_filters(ticker)
        if not ok:
            execute("UPDATE signals SET status='REJECTED', rejection_reason=%s WHERE id=%s",
                    (reason, sig["id"]))
            lines.append(f"SKIP {ticker}: {reason}")
            continue

        price = get_current_price(ticker)
        if not price:
            lines.append(f"SKIP {ticker}: could not fetch price")
            continue

        # Equal-weight target
        n_total = n_open + 1
        target_per_position = (total_value + price * 100) / n_total  # rough estimate

        # Trim instructions
        trim_lines = []
        for p in open_positions:
            p_price = get_current_price(p["ticker"])
            if not p_price:
                continue
            current_val = float(p["shares"]) * p_price
            if current_val > target_per_position * 1.05:
                excess = current_val - target_per_position
                shares_to_sell = int(excess / p_price)
                if shares_to_sell > 0:
                    trim_lines.append(
                        f"  TRIM {p['ticker']}: sell {shares_to_sell} shares "
                        f"@ ~${p_price:.2f} (raise ${excess:,.0f})"
                    )

        stop_price = round(price * (1 - STOP_LOSS_PCT), 2)
        exit_date = today + __import__('datetime').timedelta(days=HOLD_DAYS)
        # advance to trading day
        while exit_date.weekday() >= 5:
            exit_date += __import__('datetime').timedelta(days=1)

        execute("""
            UPDATE signals SET status='SCHEDULED' WHERE id=%s
        """, (sig["id"],))

        lines.append(f"\n<b>NEW SIGNAL: {ticker}</b>")
        lines.append(f"  Cluster: {sig['cluster_type']} | Entry: {today}")
        lines.append(f"  {sig['insider_1_name']} ({sig['insider_1_role']}) "
                     f"${float(sig['insider_1_value']):,.0f}")
        lines.append(f"  {sig['insider_2_name']} ({sig['insider_2_role']}) "
                     f"${float(sig['insider_2_value']):,.0f}")
        lines.append(f"  Current price: ${price:.2f}")
        lines.append(f"  Stop: ${stop_price:.2f} | Exit by: {exit_date}")

        if trim_lines:
            lines.append("  <b>TRIM FIRST:</b>")
            lines.extend(trim_lines)

        lines.append(f"  <b>THEN BUY: {ticker} @ limit ${price * 1.005:.2f}</b>")

    send_message("\n".join(lines))


if __name__ == "__main__":
    main()
