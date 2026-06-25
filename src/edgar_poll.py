import re
import time
import requests
from lxml import etree
from datetime import datetime, timezone, date, timedelta

from .config import MIN_PURCHASE, CLUSTER_WINDOW_DAYS
from .db import fetchall, execute, fetchone
from .business_rules import classify_role, next_trading_day
from .telegram_alerts import send_message

# ── EDGAR endpoints ───────────────────────────────────────────────────────────
# Switched from RSS (returns all filing types, only ~4% are Form 4s) to
# EFTS full-text search API which returns ONLY Form 4s — 500+ per day.
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
HEADERS  = {"User-Agent": "InsiderClusterBot admin@example.com"}

MAX_EFTS_PAGES = 6   # 6 × 100 = 600 filings max per poll run (covers full day)


# ── Fetch Form 4 entries from EFTS ────────────────────────────────────────────

def fetch_rss_entries():
    """
    Fetches today's + yesterday's Form 4 filings from the EDGAR EFTS API.
    Returns list of dicts: {index_url, acc_dashed, cik}

    Replaces the old RSS-based approach which returned all filing types mixed
    together — only ~4 of every 100 entries were Form 4s, causing 96% to be
    silently dropped.
    """
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    results  = []
    seen_acc = set()

    for page in range(MAX_EFTS_PAGES):
        params = {
            "q":         '""',
            "dateRange": "custom",
            "startdt":   yesterday,
            "enddt":     today,
            "forms":     "4",
            "from":      page * 100,
        }
        try:
            resp = requests.get(EFTS_URL, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data  = resp.json()
            hits  = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)

            if page == 0:
                print(f"EFTS status: {resp.status_code}, total Form 4s today: {total}")

            if not hits:
                break

            for hit in hits:
                src        = hit.get("_source", {})
                acc_dashed = src.get("adsh", "")
                ciks       = src.get("ciks", [])

                if not acc_dashed or not ciks:
                    continue

                if acc_dashed in seen_acc:
                    continue
                seen_acc.add(acc_dashed)

                # Use first CIK (reporter) to build the index URL — always resolves
                cik        = ciks[0].lstrip("0")
                acc_nodash = acc_dashed.replace("-", "")
                index_url  = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}"
                    f"/{acc_nodash}/{acc_dashed}-index.htm"
                )
                results.append({
                    "index_url":  index_url,
                    "acc_dashed": acc_dashed,
                    "cik":        cik,
                })

            # Stop paginating if we've got all results
            if len(results) >= total:
                break

            time.sleep(0.1)

        except Exception as e:
            print(f"fetch_rss_entries error (page {page}): {e}")
            break

    print(f"Form 4 entries fetched: {len(results)}")
    return results


# ── Fetch individual filing XML (unchanged) ───────────────────────────────────

def fetch_filing_xml(index_url):
    """
    Scrape the EDGAR index page for the raw Form 4 XML href.
    """
    try:
        time.sleep(0.12)
        resp = requests.get(index_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text

        all_xml = re.findall(
            r'href=["\']([^"\']+\.xml)["\']',
            html,
            re.IGNORECASE
        )
        raw_xml_list = [
            x for x in all_xml
            if "xsl" not in x.lower() and x.startswith("/Archives")
        ]

        for xml_path in raw_xml_list:
            xml_url = "https://www.sec.gov" + xml_path
            time.sleep(0.12)
            try:
                xml_resp = requests.get(xml_url, headers=HEADERS, timeout=20)
                xml_resp.raise_for_status()
                if "<ownershipDocument" in xml_resp.text:
                    return xml_url, xml_resp.text
            except Exception as e:
                print(f"fetch_filing_xml: error fetching {xml_url}: {e}")
                continue

        return None, None

    except Exception as e:
        print(f"fetch_filing_xml error ({index_url}): {e}")
        return None, None


# ── Parse Form 4 XML (unchanged, debug print retained) ───────────────────────

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

        cik          = find_text("issuerCik")
        insider_name = find_text("rptOwnerName") or "Unknown"
        insider_cik  = find_text("rptOwnerCik") or cik

        # Try officerTitle first, then otherText as fallback
        role_raw = find_text("officerTitle") or find_text("otherText") or ""

        doc_type     = find_text("documentType") or ""
        is_amendment = doc_type.endswith("/A")

        period = find_text("periodOfReport")
        filing_date = date.today()
        if period:
            try:
                filing_date = date.fromisoformat(period)
            except ValueError:
                pass

        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el   = txn.find(".//transactionCode")
            shares_el = txn.find(".//transactionShares/value")
            price_el  = txn.find(".//transactionPricePerShare/value")

            if code_el is None or code_el.text != "P":
                continue
            if shares_el is None or price_el is None:
                continue

            try:
                shares = float(shares_el.text)
                price  = float(price_el.text)
            except (ValueError, TypeError):
                continue

            value = shares * price
            if value < MIN_PURCHASE:
                continue

            role = classify_role(role_raw)
            if role is None:
                print(f"  ROLE REJECTED: ticker={ticker} raw_title='{role_raw}'")
                continue

            results.append({
                "cik":          insider_cik,
                "ticker":       ticker,
                "insider_name": insider_name,
                "insider_role": role,
                "shares":       shares,
                "price":        price,
                "value":        value,
                "filing_date":  filing_date,
                "filed_at":     datetime.now(timezone.utc),
                "is_amendment": is_amendment,
            })

    except Exception as e:
        print(f"parse_form4_xml error: {e}")

    return results


# ── DB helpers (unchanged) ────────────────────────────────────────────────────

def build_seen_keys():
    try:
        rows = fetchall("SELECT cik, ticker, filing_date, shares FROM filings")
        return {
            (r["cik"], r["ticker"], str(r["filing_date"]), float(r["shares"]))
            for r in rows
        }
    except Exception as e:
        print(f"build_seen_keys error: {e}")
        return set()


def insert_filing(f, xml_url=None):
    try:
        execute("""
            INSERT INTO filings
              (cik, ticker, insider_name, insider_role, transaction_code,
               shares, price, value, filing_date, filed_at, is_amendment, raw_xml_url)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cik, ticker, filing_date, shares) DO NOTHING
        """, (
            f["cik"], f["ticker"], f["insider_name"], f["insider_role"], "P",
            f["shares"], f["price"], f["value"],
            f["filing_date"], f["filed_at"], f["is_amendment"], xml_url,
        ))
        return True
    except Exception as e:
        print(f"insert_filing error: {e}")
        return False


# ── Cluster detection (unchanged) ─────────────────────────────────────────────

def detect_clusters():
    cutoff = date.today() - timedelta(days=CLUSTER_WINDOW_DAYS)
    rows = fetchall(
        "SELECT DISTINCT ticker FROM filings WHERE filing_date >= %s", (cutoff,)
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
            FROM filings WHERE ticker=%s AND filing_date >= %s
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
        entry_date   = next_trading_day(i2["filing_date"])

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
            f"🔔 NEW CLUSTER {ticker} Type {cluster_type}\n"
            f"Entry: {entry_date}\n"
            f"{i1['insider_name']} ({i1['insider_role']}) ${float(i1['value']):,.0f}\n"
            f"{i2['insider_name']} ({i2['insider_role']}) ${float(i2['value']):,.0f}"
        )


# ── Main (unchanged) ──────────────────────────────────────────────────────────

def main():
    entries    = fetch_rss_entries()
    skipped    = 0
    inserted   = 0
    duplicates = 0
    no_xml     = 0

    seen_keys = build_seen_keys()

    for e in entries:
        xml_url, xml_text = fetch_filing_xml(e["index_url"])
        if not xml_text:
            no_xml += 1
            continue

        filings = parse_form4_xml(xml_text)
        if not filings:
            skipped += 1
            continue

        for f in filings:
            key = (f["cik"], f["ticker"], str(f["filing_date"]), float(f["shares"]))
            if key in seen_keys:
                duplicates += 1
                continue
            if insert_filing(f, xml_url):
                seen_keys.add(key)
                inserted += 1
            else:
                duplicates += 1

    detect_clusters()
    print(
        f"edgar_poll done: {len(entries)} form4 entries, "
        f"{no_xml} no-XML, {skipped} no-P-transactions, "
        f"{duplicates} duplicates, {inserted} inserted."
    )


if __name__ == "__main__":
    main()
