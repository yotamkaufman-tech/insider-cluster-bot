import os

DATABASE_URL = os.environ["DATABASE_URL"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Strategy constants
MIN_PURCHASE = 50_000
CLUSTER_WINDOW_DAYS = 14
GAP_UP_LIMIT = 0.03       # 3% gap filter
STOP_LOSS_PCT = 0.15      # -15% stop loss (closing price basis)
HOLD_DAYS = 5             # exit at open of Day 6
LIMIT_SLIPPAGE = 0.005    # 0.5% limit order tolerance
