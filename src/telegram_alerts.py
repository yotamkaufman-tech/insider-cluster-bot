import requests
from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })
    resp.raise_for_status()
    return resp.json()
def format_signal_alert(signal: dict) -> str:
    combined = signal['insider_1_value'] + signal['insider_2_value']
    return (
        f"🚨 *CLUSTER SIGNAL DETECTED*\n\n"
        f"📌 *Ticker:* `{signal['ticker']}`\n"
        f"📋 *Type:* {signal['cluster_type']} — Same-day buy\n\n"
        f"👤 *{signal['insider_1_role']}:* {signal['insider_1_name']}\n"
        f"   💰 ${signal['insider_1_value']:,.0f} on {signal['filing_date_1']}\n\n"
        f"👤 *{signal['insider_2_role']}:* {signal['insider_2_name']}\n"
        f"   💰 ${signal['insider_2_value']:,.0f} on {signal['filing_date_2']}\n\n"
        f"💵 *Combined:* ${combined:,.0f}\n"
        f"📅 *Entry Date:* {signal['entry_date']}\n"
        f"⏳ *Window Closes:* {signal['entry_date'] + timedelta(days=5)}\n\n"
        f"🔗 [View on OpenInsider](https://openinsider.com/{signal['ticker']})\n"
        f"📊 [TradingView Chart](https://www.tradingview.com/chart/?symbol={signal['ticker']})"
