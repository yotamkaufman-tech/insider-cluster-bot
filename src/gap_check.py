"""
Runs at 9:31 AM ET on trading days.
For each SCHEDULED signal for today:
- Compares prior close to today open
- If gap up > 3%: SKIP the trade
- If gap OK: confirm GO and remind to execute
"""
from datetime import date
import yfinance as yf

from .config import GAP_UP_LIMIT
from .db import fetchall, execute
from .telegram_alerts import send_message
from .business_rules import get_prior_close


def get_open_price(ticker: str):
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Open"].iloc[0])
    except Exception:
        pass
    return None


def main():
    today = date.today()

    scheduled = fetchall(
        "SELECT * FROM signals WHERE entry_date=%s AND status='SCHEDULED'",
        (today,)
    )

    if not scheduled:
        send_message(f"📋 Gap check {today}: No scheduled signals today.")
        return

    for sig in scheduled:
        ticker = sig["ticker"]
        prior_close = get_prior_close(ticker)
        open_price = get_open_price(ticker)

        if not prior_close or not open_price:
            send_message(
                f"⚠️ GAP CHECK — ${ticker}\n"
                f"Could not fetch price data. Check manually before entering."
            )
            continue

        gap_pct = (open_price - prior_close) / prior_close

        if gap_pct > GAP_UP_LIMIT:
            execute(
                "UPDATE signals SET status='SKIPPED', rejection_reason=%s WHERE id=%s",
                (f"gap up {gap_pct*100:.1f}% at open", sig["id"])
            )
            execute(
                "UPDATE positions SET status='SKIPPED' WHERE ticker=%s AND entry_date=%s AND status='PENDING'",
                (ticker, today)
            )
            send_message(
                f"⛔ SKIP — ${ticker}\n"
                f"Gap up {gap_pct*100:.1f}% at open (limit: 3.0%).\n"
                f"Signal already priced in. Do NOT enter."
            )
        else:
            execute(
                "UPDATE signals SET status='CONFIRMED' WHERE id=%s",
                (sig["id"],)
            )
            execute(
                "UPDATE positions SET status='OPEN' WHERE ticker=%s AND entry_date=%s AND status='PENDING'",
                (ticker, today)
            )
            direction = "down" if gap_pct < 0 else "flat"
            send_message(
                f"✅ GO — ${ticker}\n"
                f"Gap: {gap_pct*100:+.2f}% ({direction}) — within limit.\n"
                f"Execute entry now per the 8 AM brief."
            )


if __name__ == "__main__":
    main()
