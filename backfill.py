#!/usr/bin/env python3
"""
backfill.py
One-off script to retroactively populate the "Insider Filings Log" Notion
database with qualifying P-transactions from the last N days (default 14,
matching CLUSTER_DAYS in insider_bot.py).

Why this exists: insider_bot.py only started persisting filings to Notion
on 2026-08-07. Anything filed before that date (e.g. the ACI cluster —
Morris Susan filed 2026-07-30, McCollam Sharon filed 2026-08-03) was never
captured, so it can never be detected by the normal daily run even with
the widened 7-day fetch window. This script fills that gap once.

Usage:
  Set the same env vars insider_bot.py uses (NOTION_TOKEN,
  NOTION_FILINGS_DB_ID), then run:

    python backfill.py            # backfills last 14 days
    python backfill.py --days 21  # custom window

Safe to re-run: it dedupes against existing AccessionKey values already
in the Filings Log, so nothing gets double-inserted.
"""

import os, re, sys, time, argparse, requests
from lxml import etree
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

NOTION_TOKEN         = os.environ.get('NOTION_TOKEN', '')
NOTION_FILINGS_DB_ID = os.environ.get('NOTION_FILINGS_DB_ID', '')
MIN_PURCHASE = 50_000
HEADERS      = {'User-Agent': 'InsiderClusterBot admin@example.com'}
EFTS_URL     = 'https://efts.sec.gov/LATEST/search-index'
NOTION_HDRS  = {
    'Authorization': 'Bearer ' + NOTION_TOKEN,
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28',
}

ROLE_KEYWORDS = {
    'CEO':      ['chief executive','ceo','co-ceo','interim ceo',
                 'president & ceo','president and ceo','president/ceo',
                 'pres, ceo','pres, chief executive','pres. & ceo',
                 'chairman of the board','exec chair'],
    'CFO':      ['chief financial','cfo','svp finance','evp finance',
                 'exec vp, cfo','treasurer and cfo','finance officer',
                 'president & cfo','pres, cfo'],
    'COO':      ['chief operating','coo','evp operations','svp operations',
                 'president of operations','vp upstream','vp operations'],
    'Chairman': ['chairman','chair of the board','exec chair',
                 'executive chairman','exec. chairman','cob'],
}
SECONDARY_EXEC_PATTERNS = ['evp', 'svp', 'vp', 'executive vice president',
                           'senior vice president', 'm&a', 'mergers',
                           'acquisitions', 'corporate affairs', 'operations']


def safe_num(x, default=None):
    if x is None:
        return default
    if isinstance(x, (int, float, Decimal)):
        return float(x)
    s = str(x).strip().replace(',', '')
    if not s or s.upper() in {'#ERROR!', 'N/A', 'NA', 'NONE', 'NULL'}:
        return default
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return default


def classify_role(raw, company=''):
    t = (raw or '').lower().strip()
    c = (company or '').lower().strip()
    for role, kws in ROLE_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return role
    if any(p in t for p in SECONDARY_EXEC_PATTERNS):
        if any(p in t for p in ['upstream', 'operations', 'finance']):
            return 'Other C-Level'
        if any(p in c for p in ['petroleum', 'energy', 'exploration', 'oil', 'gas']):
            return 'Other C-Level'
    return 'Other'


def _np(text):
    return {'rich_text': [{'text': {'content': str(text)}}]}

def _nt(text):
    return {'title': [{'text': {'content': str(text)}}]}


def fetch_entries(days_back):
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=days_back)).isoformat()
    results, seen = [], set()
    for page in range(20):  # wider page budget than daily run, since window is bigger
        success, last_err = False, None
        for attempt in range(3):
            try:
                r = requests.get(EFTS_URL, headers=HEADERS, timeout=20, params={
                    'q': '""', 'dateRange': 'custom',
                    'startdt': start, 'enddt': today,
                    'forms': '4', 'from': page * 100
                })
                r.raise_for_status()
                data  = r.json()
                hits  = data.get('hits', {}).get('hits', [])
                total = data.get('hits', {}).get('total', {}).get('value', 0)
                if page == 0:
                    print('EFTS status=' + str(r.status_code) + ' total=' + str(total))
                if not hits:
                    success = True
                    break
                for h in hits:
                    src  = h.get('_source', {})
                    acc  = src.get('adsh', '')
                    ciks = src.get('ciks', [])
                    if not acc or not ciks or acc in seen:
                        continue
                    seen.add(acc)
                    cik = ciks[0].lstrip('0')
                    nd  = acc.replace('-', '')
                    results.append({
                        'accession': acc,
                        'index_url':
                        'https://www.sec.gov/Archives/edgar/data/' + cik +
                        '/' + nd + '/' + acc + '-index.htm'})
                if len(results) >= total:
                    success = True
                    break
                time.sleep(0.1)
                success = True
                break
            except Exception as e:
                last_err = e
                wait = 1.5 * (2 ** attempt)
                print('fetch_entries page ' + str(page) + ' attempt ' + str(attempt + 1) + ' error: ' + str(e) + ' — retry in ' + str(wait) + 's')
                time.sleep(wait)
        if not success:
            print('fetch_entries page ' + str(page) + ' failed after retries: ' + str(last_err))
            continue
        if not results and page > 0:
            break
    print('Entries fetched: ' + str(len(results)))
    return results


def fetch_xml(index_url):
    try:
        time.sleep(0.12)
        r = requests.get(index_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        pat   = r'href=["\']([^"\']+\.xml)["\']'
        paths = [x for x in re.findall(pat, r.text, re.IGNORECASE)
                 if 'xsl' not in x.lower() and x.startswith('/Archives')]
        for p in paths:
            url = 'https://www.sec.gov' + p
            time.sleep(0.12)
            try:
                xr = requests.get(url, headers=HEADERS, timeout=20)
                xr.raise_for_status()
                if '<ownershipDocument' in xr.text:
                    return url, xr.text
            except Exception:
                continue
    except Exception as e:
        print('fetch_xml error: ' + str(e))
    return None, None


def parse_xml(xml_text, accession=''):
    results = []
    try:
        root = etree.fromstring(xml_text.encode(),
                                parser=etree.XMLParser(recover=True))
        def ft(tag):
            el = root.find('.//' + tag)
            return el.text.strip() if el is not None and el.text else None

        ticker = (ft('issuerTradingSymbol') or '').upper().strip()
        if not ticker or ticker in ('NONE', 'N/A', ''):
            return []

        name     = ft('rptOwnerName') or 'Unknown'
        cik      = ft('rptOwnerCik') or ft('issuerCik') or ''
        role_raw = ft('officerTitle') or ft('otherText') or ''
        company  = ft('issuerName') or ''
        fd       = date.today()
        p        = ft('periodOfReport')
        if p:
            try: fd = date.fromisoformat(p)
            except ValueError: pass

        for txn in root.findall('.//nonDerivativeTransaction'):
            code  = txn.find('.//transactionCode')
            sh    = txn.find('.//transactionShares/value')
            price = txn.find('.//transactionPricePerShare/value')
            if code is None or code.text != 'P':
                continue
            shares = safe_num(sh.text if sh is not None else None)
            px     = safe_num(price.text if price is not None else None)
            if shares is None or px is None:
                continue
            value = shares * px
            if value < MIN_PURCHASE:
                continue
            role = classify_role(role_raw, company)
            if role == 'Other':
                continue
            results.append({
                'ticker': ticker, 'insider_name': name,
                'insider_cik': cik, 'insider_role': role,
                'value': value, 'filing_date': fd,
                'transaction_code': 'P', 'company': company,
                'accession': accession,
            })
    except Exception as e:
        print('parse_xml error: ' + str(e))
    return results


def load_logged_accessions():
    if not NOTION_TOKEN or not NOTION_FILINGS_DB_ID:
        return set()
    seen = set()
    try:
        cursor = None
        while True:
            body = {'page_size': 100}
            if cursor:
                body['start_cursor'] = cursor
            resp = requests.post(
                'https://api.notion.com/v1/databases/' + NOTION_FILINGS_DB_ID + '/query',
                headers=NOTION_HDRS, json=body, timeout=10
            )
            if resp.status_code != 200:
                print('  [Notion/Filings] load_logged_accessions failed: ' + resp.text[:200])
                break
            data = resp.json()
            for page in data.get('results', []):
                props = page.get('properties', {})
                rt = props.get('AccessionKey', {}).get('rich_text', [])
                acc = rt[0]['text']['content'] if rt else ''
                if acc:
                    seen.add(acc)
            if not data.get('has_more'):
                break
            cursor = data.get('next_cursor')
    except Exception as e:
        print('  [Notion/Filings] load_logged_accessions exception: ' + str(e))
    return seen


def append_filing_to_notion(entry):
    if not NOTION_TOKEN or not NOTION_FILINGS_DB_ID:
        print('  [Notion/Filings] NOTION_FILINGS_DB_ID not set — skipping')
        return False
    props = {
        'Ticker':          _nt(entry['ticker']),
        'InsiderCik':      _np(entry.get('insider_cik', '')),
        'InsiderName':     _np(entry.get('insider_name', '')),
        'Role':            _np(entry.get('insider_role', '')),
        'Value':           _np('{:.2f}'.format(entry.get('value') or 0)),
        'FilingDate':      _np(str(entry.get('filing_date', ''))),
        'TransactionCode': _np(entry.get('transaction_code', 'P')),
        'Company':         _np(entry.get('company', '')),
        'Detected':        _np(datetime.now(timezone.utc).isoformat()),
        'AccessionKey':    _np(entry.get('accession', '')),
    }
    try:
        resp = requests.post(
            'https://api.notion.com/v1/pages',
            headers=NOTION_HDRS,
            json={'parent': {'database_id': NOTION_FILINGS_DB_ID}, 'properties': props},
            timeout=10
        )
        if resp.status_code == 200:
            return True
        print('  [Notion/Filings] failed ' + str(resp.status_code) + ': ' + resp.text[:300])
        return False
    except Exception as e:
        print('  [Notion/Filings] exception: ' + str(e))
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14,
                         help='How many days back to backfill (default 14, matches CLUSTER_DAYS)')
    args = parser.parse_args()

    if not NOTION_TOKEN or not NOTION_FILINGS_DB_ID:
        print('ERROR: NOTION_TOKEN and NOTION_FILINGS_DB_ID must be set in the environment.')
        sys.exit(1)

    print('=== backfill started, days_back=' + str(args.days) + ' ===')
    entries = fetch_entries(days_back=args.days)

    already_logged = load_logged_accessions()
    print('Already logged: ' + str(len(already_logged)) + ' filings')

    all_filings, no_xml, skipped = [], 0, 0
    for e in entries:
        _, xml_text = fetch_xml(e['index_url'])
        if not xml_text:
            no_xml += 1
            continue
        parsed = parse_xml(xml_text, accession=e.get('accession', ''))
        if not parsed:
            skipped += 1
            continue
        all_filings.extend(parsed)

    print('Qualifying P-transactions found: ' + str(len(all_filings)))

    newly_logged = 0
    for f in all_filings:
        acc = f.get('accession', '')
        if acc and acc in already_logged:
            continue
        if append_filing_to_notion(f):
            newly_logged += 1
            if acc:
                already_logged.add(acc)
            print('  [Notion/Filings] logged ' + f['ticker'] + ' — ' + f['insider_name']
                  + ' (' + f['insider_role'] + ') on ' + str(f['filing_date']))

    print('=== backfill done: ' + str(len(entries)) + ' entries, '
          + str(no_xml) + ' no-xml, ' + str(skipped) + ' no-P-txns, '
          + str(newly_logged) + ' newly persisted ===')


if __name__ == '__main__':
    main()
