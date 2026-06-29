#!/usr/bin/env python3
"""
insider_bot.py
Single-file insider cluster signal bot.
Logs to Notion. Alerts via Telegram.

To run a smoke test via GitHub Actions:
  Set secret TEST_MODE = 1 in GitHub Secrets, trigger workflow manually.
  Remove TEST_MODE secret when going live.
"""

import os, re, time, requests
from lxml import etree
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
NOTION_TOKEN     = os.environ.get('NOTION_TOKEN', '')
NOTION_DB_ID     = os.environ.get('NOTION_DATABASE_ID', '')
TEST_MODE        = os.environ.get('TEST_MODE', '0') == '1'

MIN_PURCHASE   = 50_000
MIN_MARKET_CAP = 500_000_000
MAX_GAP_PCT    = 0.03
CLUSTER_DAYS   = 14
HOLD_DAYS      = 5
HEADERS        = {'User-Agent': 'InsiderClusterBot admin@example.com'}
EFTS_URL       = 'https://efts.sec.gov/LATEST/search-index'
NOTION_HDRS    = {
    'Authorization': 'Bearer ' + NOTION_TOKEN,
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28',
}

ROLE_KEYWORDS = {
    'CEO':      ['chief executive','ceo','co-ceo','interim ceo',
                 'president & ceo','president and ceo','president/ceo',
                 'pres, ceo','pres, chief executive','pres. & ceo'],
    'CFO':      ['chief financial','cfo','svp finance','evp finance',
                 'exec vp, cfo','treasurer and cfo'],
    'COO':      ['chief operating','coo','evp operations','svp operations'],
    'Chairman': ['chairman','chair of the board','exec chair',
                 'executive chairman','exec. chairman','cob'],
}

NYSE_HOLIDAYS = {
    date(2026,1,1),  date(2026,1,19), date(2026,2,16),
    date(2026,4,3),  date(2026,5,25), date(2026,7,3),
    date(2026,9,7),  date(2026,11,26),date(2026,12,25),
}


# ── HELPERS ─────────────────────────────────────────────────────────
