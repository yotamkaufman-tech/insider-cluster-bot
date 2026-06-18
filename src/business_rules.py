from datetime import date, timedelta

MIN_PURCHASE = 50_000
CLUSTER_WINDOW_DAYS = 14
STOP_LOSS_PCT = 0.15
HOLD_DAYS = 5

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
        "chief operations",
        "coo",
    ],
    "Chairman": [
        "chairman",
        "chair of the board",
        "executive chair",
        "exec chair",
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
