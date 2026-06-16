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
