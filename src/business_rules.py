from datetime import date, timedelta
import yfinance as yf

MIN_PURCHASE = 50_000
CLUSTER_WINDOW_DAYS = 14
STOP_LOSS_PCT = 0.15
HOLD_DAYS = 5

ROLE_KEYWORDS = {
    "CEO": [
        "chief executive", "ceo", "co-ceo", "interim ceo", "pres", "president & ceo",
        "president and ceo", "principal executive",
    ],
    "CFO": [
        "chief financial", "cfo", "principal financial", "principal accounting",
        "chief accounting",
    ],
    "COO": [
        "chief operating", "chief operations", "coo",
    ],
    "Chairman": [
        "chairman", "chair of the board", "executive chair", "exec chair",
        "executive chairman",
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


def next_trading_day(d):
    next_day = d + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


def is_trading_day(d=None):
    if d is None:
        d = date.today()
    return d.weekday() < 5


def get_current_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_prior_close(ticker):
    """
    Return yesterday's close price for gap checks.
    Uses last 2 daily bars; falls back to most recent close if only one.
    """
    try:
        hist = yf.Ticker(ticker).history(period="3d")
        if hist.shape[0] >= 2:
            return float(hist["Close"].iloc[-2])
        elif hist.shape[0] == 1:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_market_cap(ticker):
    try:
        info = yf.Ticker(ticker).info
        return info.get("marketCap", None)
    except Exception:
        return None


def get_adv(ticker):
    """Average daily volume in shares over last 20 days."""
    try:
        hist = yf.Ticker(ticker).history(period="20d")
        if not hist.empty:
            return float(hist["Volume"].mean())
    except Exception:
        pass
    return None


def has_upcoming_earnings(ticker, days=5):
    """Best-effort check using yfinance calendar."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return False
        if hasattr(cal, "empty") and cal.empty:
            return False
        earnings_date = None
        if isinstance(cal, dict):
            earnings_date = cal.get("Earnings Date", [None])[0]
        else:
            if "Earnings Date" in cal.columns:
                earnings_date = cal["Earnings Date"].iloc[0]
        if earnings_date is None:
            return False
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        today = date.today()
        return today <= earnings_date <= today + timedelta(days=days)
    except Exception:
        return False
