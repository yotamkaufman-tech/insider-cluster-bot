"""
Phase 1: Poll EDGAR RSS every 15 minutes.
- Fetches new Form 4 filings
- Parses XML for each filing
- Filters by code P, role, $50k minimum
- Stores qualifying filings in DB
- Detects clusters (Type A and B) in rolling 14-day window
- Inserts confirmed signals into signals table
- Sends Telegram alert for each new cluster
"""
import re
import time
import requests
import feedparser
from datetime import datetime, timezone, date, timedelta
from xml.etree import ElementTree as ET

from .config import MIN_PURCHASE, CLUSTER_WINDOW_DAYS
from .db import fetchall, execute, fetchone
from .business_rules import classify_role, next_trading_day
from .telegram_alerts import send_message

EDGAR_RSS = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&owner=include&count=100&output=atom"
)
HEADERS = {"User-Agent": "InsiderClusterBot contact@example.com"}


# ── RSS polling ──────────────────────────────────────────────────────────────

def fetch_rss_entries():
    feed = feedparser.parse(EDGAR_RSS)
    return feed.entries


def extract_accession_from_url(url: str):
    """Extract accession number from EDGAR filing index URL."""
    m = re.search(r"accession-number=([\d-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/Archives/edgar/data/\d+/([\d-]+)/", url)
    if m:
        return m.group(1)
    return None


# ── XML parsing ───────────────────────────────────────────────────────────────

def fetch_filing_xml(index_url: str):
    """
    Given an EDGAR filing index URL, find and return the Form 4 XML content.
    Returns (xml_url, xml_text) or (None, None).
    """
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        # Find XML file link in the index page
        matches = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        if not matches:
            return None, None
        xml_url = "https://www.sec.gov" + matches[0]
        time.sleep(0.1
