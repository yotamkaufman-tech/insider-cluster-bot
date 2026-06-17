"""
Business rules: role classification, signal filters.
All strategy constants come from config.py.
"""
from datetime import date, timedelta
from typing import Optional
import yfinance as yf

ROLE_KEYWORDS = {
    "CEO": ["chief executive", " ceo", "co-ceo", "interim ceo", "pres, ceo", "pres,ceo"],
    "CFO": ["chief financial", " cfo", "svp, cfo", "evp, cfo", "exec vp, cfo"],
    "COO": ["chief operating", " coo", "evp, coo", "svp, coo"],
    "Chairman": ["chairman", "chair of the board", "exec chair", "exec cob", " cob"],
}


def classify_role(title: str) -> Optional[str]:
    """Return canonical role or None if not qualifying."""
    t = title.lower()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return role
    return None


def is_qualifying_role(title: str) -> bool:
    return classify_role(title) is not None


def get_prior_close(ticker: str) -> Optional[float]:
    """Fetch prior trading day close price."""
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) >= 2:
            return float(hist["Close"].iloc[-2])
        elif len(hist) == 1:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_current_price(ticker: str) -> Optional[float]:
    """Fetch latest price (intraday or last close)."""
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


def get_market_cap(ticker: str) -> Optional[float]:
    """Return market cap in USD, or None on failure."""
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.market_cap) if info.market_cap else None
    except Exception:
        return None


def get_avg_daily_volume_noti
