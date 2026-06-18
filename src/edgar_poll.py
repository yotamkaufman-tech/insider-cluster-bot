import re
import time
import requests
import feedparser
from lxml import etree
from datetime import datetime, timezone, date, timedelta

from .config import MIN_PURCHASE, CLUSTER_WINDOW_DAYS
from .db import fetchall, execute, fetchone
from .business_rules import classify_role, next_trading_day
from .telegram_alerts import send_message

EDGAR_RSS = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&owner=include&count=100&output=atom"
)
HEADERS = {"User-Agent": "InsiderClusterBot admin@example.com"}


def fetch_rss_entries():
    try:
        resp = requests.get(EDGAR_RSS, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        print(f"RSS status: {resp.status_code}, entries: {len(feed.entries)}")
        return feed.entries
    except Exception as e:
        print(f"fetch_rss_entries error: {e}")
        return []


def fetch_filing_xml(index_url):
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        matches = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        if not matches:
            return None, None
        xml_url = "https://www.sec.gov" + matches[0]
        time.sleep(0.11)
        xml_resp = requests.get(xml_url, headers=HEADERS, timeout=15)
        xml_resp.raise_for_status()
        return xml_url, xml_resp.text
    except Exception as e:
        print(f"fetch_filing_xml error: {e}")
        return None, None


def parse_form4_xml(xml_text):
    results = []
    try:
        root = etree.fromstring(xml_text.encode(), parser=etree.XMLParser(recover=True))

        def find_text(tag):
            el = root.find(".//" + tag)
            return el.text.strip() if el is not None and el.text else None

        ticker = find_text("issuerTradingSymbol")
        if not ticker:
            return []
        ticker = ticker.upper()

        cik = find_text("issuerCik")
        insider_name = find_text("rptOwnerName") or "Unknown"
        insider_cik = find_text("rptOwnerCik") or cik
        role_raw = find_text("officerTitle") or ""

        doc_type = find_text("documentType") or ""
        is_amendment = doc_type.endswith("/A")

        period = find_text("periodOfReport")
        filing_date = date.today()
        if period:
            try:
                filing_date = date.fromisoformat(period)
            except ValueError:
                pass

        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el = txn.find(".//transactionCode")
            shares_el = txn.find(".//transactionShares/value")
            price_el = txn.find(".//transactionPricePerShare/value")

            if code_el is None or code_el.text != "P":
                continue
            if shares_el is None or price_el is None:
                continue

            try:
                shares = float(shares_el.text)
                price = float(price_el.text)
            except (ValueError, TypeError):
                continue

            value = shares * price
            if value < MIN_PURCHASE:
                continue

            role = classify_role(role_raw)
            if role is None:
                continue

            results.append({
                "cik": insider_cik,
                "ticker": ticker,
                "insider_name": insider_name,
                "insider_role": role,
                "shares": shares,
                "price": price,
                "value": value,
                "filing_date": filing_date,
                "filed_at": datetime.now(timezone.utc),
                "is_amendment": is_amendment,
            })

    except Exception as e:
        print(f"parse_form4_xml error: {e}")

    return results


def insert_filing(f, xml_url=None):
    try:
        execute("""
            INSERT INTO filings
              (cik, ticker, insider_name, insider_role, transaction_code,
               shares, price, value, filing_date, filed_at, is_amendment, raw_xml_url)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cik, ticker, filing_date, value) DO NOTHING
        """, (
            f["cik"], f["ticker"], f["insider_name"], f["insider_role"], "P",
            f["shares"], f["price"], f["value"],
            f["filing_date"], f["filed_at"], f["is_amendment"], xml_url,
        ))
    except Exception as e:
        print(f"insert_filing error: {e}")


def detect_clusters():
    cutoff = date.today() - timedelta(days=CLUSTER_WINDOW_DAYS)

    rows = fetchall(
        "SELECT DISTINCT ticker FROM filings WHERE filing_date >= %s",
        (cutoff,)
    )

    for row in rows:
        ticker = row["ticker"]

        if fetchone("SELECT id FROM positions WHERE ticker=%s AND status='OPEN'", (ticker,)):
            continue

        if fetchone(
            "SELECT id FROM signals WHERE ticker=%s AND status IN ('PENDING','SCHEDULED','CONFIRMED')",
            (ticker,)
        ):
            continue

        filings = fetchall("""
            SELECT DISTINCT cik, insider_name, insider_role, filing_date, value
            FROM filings
            WHERE ticker=%s AND filing_date >= %s
            ORDER BY filing_date ASC
        """, (ticker, cutoff))

        seen_ciks = {}
        for f in filings:
            if f["cik"] not in seen_ciks:
                seen_ciks[f["cik"]] = f

        unique_insiders = list(seen_ciks.values())
        if len(unique_insiders) < 2:
            continue

        unique_insiders.sort(key=lambda x: x["filing_date"])
        i1 = unique_insiders[0]
        i2 = unique_insiders[1]

        cluster_type = "A" if i1["filing_date"] == i2["filing_date"] else "B"
        entry_date = next_trading_day(i2["filing_date"])

        execute("""
            INSERT INTO signals
              (ticker, cluster_type,
               insider_1_name, insider_1_role, insider_1_value,
               insider_2_name, insider_2_role, insider_2_value,
               filing_date_1, filing_date_2,
               detection_time, entry_date, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING')
            ON CONFLICT DO NOTHING
        """, (
            ticker, cluster_type,
            i1["insider_name"], i1["insider_role"], float(i1["value"]),
            i2["insider_name"], i2["insider_role"], float(i2["value"]),
            i1["filing_date"], i2["filing_date"],
            datetime.now(timezone.utc), entry_date,
        ))

        send_message(
            f"NEW CLUSTER {ticker} Type {cluster_type}\n"
            f"Entry: {entry_date}\n"
            f"{i1['insider_name']} ({i1['insider_role']}) ${float(i1['value']):,.0f}\n"
            f"{i2['insider_name']} ({i2['insider_role']}) ${float(i2['value']):,.0f}"
        )


def main():
    entries = fetch_rss_entries()
    new_count = 0
    skipped = 0

    for entry in entries:
        index_url = entry.get("link", "")
        if not index_url:
            continue
        xml_url, xml_text = fetch_filing_xml(index_url)
        if not xml_text:
            skipped += 1
            continue
        filings = parse_form4_xml(xml_text)
        for f in filings:
            insert_filing(f, xml_url)
            new_count += 1

    detect_clusters()
    print(f"edgar_poll done: {len(entries)} entries, {skipped} skipped, {new_count} inserted.")


if __name__ == "__main__":
    main()
