from datetime import date, timedelta
import yfinance as yf

from .db import fetchall, execute
from .telegram_alerts import send_message
from .config import STOP_LOSS_PCT


def get_close_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def count_trading_days(start, end):
    d = start
    count = 0
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def main():
    today = date.today()
    open_positions = fetchall("SELECT * FROM positions WHERE status='OPEN'")

    if not open_positions:
        send_message(f"EOD {today}: No open positions.")
        return

    stops_triggered = []
    exits_tomorrow = []
    portfolio_lines = []
    total_value = 0.0

    for p in open_positions:
        ticker = p["ticker"]
        close = get_close_price(ticker)
        if not close:
            close = float(p["entry_price"])

        entry_price = float(p["entry_price"])
        shares = float(p["shares"])
        current_value = shares * close
        total_value += current_value
        pnl_pct = (close - entry_price) / entry_price * 100

        execute(
            "UPDATE positions SET allocated_capital=%s WHERE id=%s",
            (current_value, p["id"])
        )

        days_held = count_trading_days(p["entry_date"], today)

        portfolio_lines.append(
            f"  {ticker}: day {days_held} | "
            f"close ${close:.2f} | "
            f"PnL {pnl_pct:+.1f}% | "
            f"value ${current_value:,.0f}"
        )

        stop_price = float(p["stop_price"])
        if close <= stop_price:
            execute(
                "UPDATE positions SET status='STOP_TRIGGERED' WHERE id=%s",
                (p["id"],)
            )
            stops_triggered.append(
                f"  STOP {ticker} close ${close:.2f} "
                f"stop ${stop_price:.2f} "
                f"({pnl_pct:+.1f}%) SELL AT TOMORROW OPEN"
            )

        exit_target = p["exit_date_target"]
        if today >= exit_target:
            exits_tomorrow.append(
                f"  EXIT {ticker} day {days_held} complete "
                f"PnL {pnl_pct:+.1f}% SELL AT TOMORROW OPEN"
            )

    lines = [
        f"<b>EOD {today}</b>",
        f"Portfolio: ${total_value:,.0f}",
        "<b>Positions:</b>",
    ]
    lines.extend(portfolio_lines)

    if stops_triggered:
        lines.append("<b>STOP TRIGGERS:</b>")
        lines.extend(stops_triggered)

    if exits_tomorrow:
        lines.append("<b>EXITS TOMORROW AT OPEN:</b>")
        lines.extend(exits_tomorrow)

    if not stops_triggered and not exits_tomorrow:
        lines.append("No stops triggered. No exits due tomorrow.")

    send_message("\n".join(lines))


if __name__ == "__main__":
    main()
