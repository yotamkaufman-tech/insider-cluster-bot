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

        # Update allocated_capital to current value for future trim calculations
        execute(
            "UPDATE positions SET allocated_capital=%s WHERE id=%s",
            (current_value, p["id"])
        )

        # Count trading days since entry
        entry_date = p["entry_date"]
        days_held = 0
        d = entry_date
        while d < today:
            d += timedelta(days=1)
            if d.weekday() < 5:
                days_held += 1

        portfolio_lines.append(
            f"  {ticker}: {days_held}d held | close ${close:.2f} | "
           
