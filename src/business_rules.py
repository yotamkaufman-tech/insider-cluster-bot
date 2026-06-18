from datetime import date, timedelta
from typing import Optional
import yfinance as yf

ROLE_KEYWORDS = {
    "CEO": [
        "chief executive",
        "ceo",
        "co-ceo",
        "interim ceo",
    ],
    "CFO": [
        "chief financial",
        "cfo",
    ],
    "COO": [
        "chief operating",
        "coo",
        "chief operations",
    ],
    "Chairman": [
        "chairman",
        "chair of the board",
        "exec chair",
        "executive chair",
        "cob",
    ],
}


def classify_role(title):
    if not title:
        return None
    t = title.lower().strip()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return role
    return None

def get_current_price(ticker):
    try:
        info = yf.Ticker(ticker).fast_info
        price = info.last_price
        if price and price > 0:
            return float(price)
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_market_cap(ticker):
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.market_cap) if info.market_cap else None
    except Exception:
        return None


def get_avg_daily_volume_notional(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="1mo")
        if hist.empty:
            return None
        notional = hist["Close"] * hist["Volume"]
        return float(notional.mean())
    except Exception:
        return None


def check_earnings_within_n_days(ticker, entry_date, n=5):
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None or cal.empty:
            return False
        earnings_date = cal.iloc[0].get("Earnings Date")
        if earnings_date is None:
            return False
        import pandas as pd
        ed = pd.Timestamp(earnings_date).date()
        delta = abs((ed - entry_date).days)
        return delta <= n * 1.5
    except Exception:
        return False


def next_trading_day(from_date):
    d = from_date + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def nth_trading_day_after(from_date, n):
    d = from_date
    count = 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d
