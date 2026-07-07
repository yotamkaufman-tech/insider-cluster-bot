from pathlib import Path
code = r'''#!/usr/bin/env python3
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
from decimal import Decimal, InvalidOperation

# ── CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
NOTION_TOKEN     = os.environ.get('NOTION_TOKEN', '')
NOTION_DB_ID     = os.environ.get('NOTION_DATABASE_ID', '')
NOTION_REJECTED_DB_ID = os.environ.get('NOTION_REJECTED_DB_ID', '')
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
                 'pres, ceo','pres, chief executive','pres. & ceo',
                 'chairman of the board','exec chair'],
    'CFO':      ['chief financial','cfo','svp finance','evp finance',
                 'exec vp, cfo','treasurer and cfo','finance officer'],
    'COO':      ['chief operating','coo','evp operations','svp operations',
                 'president of operations','vp upstream','vp operations'],
    'Chairman': ['chairman','chair of the board','exec chair',
                 'executive chairman','exec. chairman','cob'],
}

SECONDARY_EXEC_PATTERNS = [
    'vp', 'vice president', 'senior vice president', 'evp', 'svp', 'chief'
]

NYSE_HOLIDAYS = {
    date(2026,1,1),  date(2026,1,19), date(2026,2,16),
    date(2026,4,3),  date(2026,5,25), date(2026,7,3),
    date(2026,9,7),  date(2026,11,26),date(2026,12,25),
}


# ── HELPERS ───────────────────────────────────────────────────────────────

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


def parse_trade_value(entry):
    val = safe_num(entry.get('value'))
    if val is not None:
        return val
    shares = safe_num(entry.get('shares'))
    price = safe_num(entry.get('stock_price'))
    if shares is not None and price is not None:
        return round(shares * price, 2)
    return None


def safe_entry_from_raw(raw):
    e = dict(raw)
    e['stock_price'] = safe_num(raw.get('stock_price'))
    e['shares'] = safe_num(raw.get('shares'))
    e['value'] = parse_trade_value(raw)
    e['role'] = classify_role(raw.get('role_raw', ''), raw.get('company', ''))
    return e


def qualifies_form4(entry):
    reasons = []
    if (entry.get('transaction_code') or '').upper() != 'P':
        reasons.append('not_code_p')
    if entry.get('role') == 'Other':
        reasons.append('wrong_role')
    if (entry.get('value') or 0) < MIN_PURCHASE:
        reasons.append('under_50k')
    if (entry.get('stock_price') or 0) < 5.0:
        reasons.append('price_below_5')
    return reasons


def next_trading_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5 or d in NYSE_HOLIDAYS:
        d += timedelta(days=1)
    return d


# ── TELEGRAM ──────────────────────────────────────────────────────────────

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print('  [TG] TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — skipping')
        return
    try:
        url  = 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage'
        resp = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown'
        }, timeout=10)
        if resp.status_code == 200:
            print('  [TG] message sent OK')
        else:
            print('  [TG] failed ' + str(resp.status_code) + ': ' + resp.text[:200])
    except Exception as e:
        print('  [TG] exception: ' + str(e))


# ── NOTION ────────────────────────────────────────────────────────────────

def _np(text):
    return {'rich_text': [{'text': {'content': str(text)}}]}

def _nt(text):
    return {'title': [{'text': {'content': str(text)}}]}

def _ns(text):
    return {'select': {'name': str(text)}} if text else {'select': None}


def append_to_notion(row, db_id=None):
    if not NOTION_TOKEN:
        print('  [Notion] NOTION_TOKEN not set — skipping')
        return False
    target_db = db_id or NOTION_DB_ID
    if not target_db:
        print('  [Notion] database id not set — skipping')
        return False
    props = {
        'Ticker':    _nt(row[0]),
        'Type':      _np(row[1]),
        'I1 Name':   _np(row[2]),
        'I1 Role':   _np(row[3]),
        'I1 Value':  _np(row[4]),
        'I2 Name':   _np(row[5]),
        'I2 Role':   _np(row[6]),
        'I2 Value':  _np(row[7]),
        'Date1':     _np(row[8]),
        'Date2':     _np(row[9]),
        'Combined':  _np(row[10]),
        'Entry':     _np(row[11]),
        'Exit By':   _np(row[12]),
        'Status':    _np(row[13]),
        'Reason':    _np(row[14]),
        'Detected':  _np(row[15]),
    }
    try:
        resp = requests.post(
            'https://api.notion.com/v1/pages',
            headers=NOTION_HDRS,
            json={'parent': {'database_id': target_db}, 'properties': props},
            timeout=10
        )
        if resp.status_code == 200:
            print('  [Notion] row added OK')
            return True
        else:
            print('  [Notion] failed ' + str(resp.status_code) + ': ' + resp.text[:300])
            return False
    except Exception as e:
        print('  [Notion] exception: ' + str(e))
        return False


def load_seen():
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return set()
    seen = set()
    try:
        cursor = None
        while True:
            body = {'page_size': 100}
            if cursor:
                body['start_cursor'] = cursor
            resp = requests.post(
                'https://api.notion.com/v1/databases/' + NOTION_DB_ID + '/query',
                headers=NOTION_HDRS, json=body, timeout=10
            )
            if resp.status_code != 200:
                print('  [Notion] load_seen failed: ' + resp.text[:200])
                break
            data = resp.json()
            for page in data.get('results', []):
                props = page.get('properties', {})
                def gp(k):
                    rt = props.get(k, {}).get('rich_text', [])
                    return rt[0]['text']['content'] if rt else ''
                title_arr = props.get('Ticker', {}).get('title', [])
                ticker = title_arr[0]['text']['content'] if title_arr else ''
                key = ticker + '|' + gp('Date1') + '|' + gp('Date2')
                seen.add(key)
            if not data.get('has_more'):
                break
            cursor = data.get('next_cursor')
    except Exception as e:
        print('  [Notion] load_seen exception: ' + str(e))
    print('  [Notion] loaded ' + str(len(seen)) + ' seen signals')
    return seen


# ── PRICE CHECKS ──────────────────────────────────────────────────────────

def get_market_cap(ticker):
    try:
        import yfinance as yf
        return float(yf.Ticker(ticker).info.get('marketCap') or 0)
    except Exception:
        return 0.0


def get_gap_pct(ticker):
    try:
        import yfinance as yf
        h = yf.download(ticker, period='2d', interval='1d',
                        progress=False, auto_adjust=True)
        if len(h) < 2:
            return None
        prior = float(h['Close'].iloc[-2])
        opn   = float(h['Open'].iloc[-1])
        return (opn - prior) / prior if prior else None
    except Exception:
        return None


# ── EDGAR ─────────────────────────────────────────────────────────────────

def fetch_entries(days_back=1):
    today  = date.today().isoformat()
    start  = (date.today() - timedelta(days=days_back)).isoformat()
    results, seen = [], set()
    for page in range(6):
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
                results.append({'index_url':
                    'https://www.sec.gov/Archives/edgar/data/' + cik +
                    '/' + nd + '/' + acc + '-index.htm'})
            if len(results) >= total:
                break
            time.sleep(0.1)
        except Exception as e:
            print('fetch_entries page ' + str(page) + ' error: ' + str(e))
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


def parse_xml(xml_text):
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
        amended  = (ft('documentType') or '').endswith('/A')
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
            role = classify_role(role_raw, ticker)
            if role == 'Other':
                continue
            results.append({
                'ticker': ticker, 'insider_name': name,
                'insider_cik': cik, 'insider_role': role,
                'value': value, 'filing_date': fd, 'amended': amended,
                'role_raw': role_raw, 'stock_price': px, 'shares': shares,
                'transaction_code': 'P', 'company': ticker
            })
    except Exception as e:
        print('parse_xml error: ' + str(e))
    return results


# ── CLUSTER DETECTION ─────────────────────────────────────────────────────

def detect_clusters(filings):
    by_ticker = defaultdict(list)
    for f in filings:
        by_ticker[f['ticker']].append(f)

    signals = []
    cutoff  = date.today() - timedelta(days=CLUSTER_DAYS)

    for ticker, rows in by_ticker.items():
        seen_ciks = {}
        for r in sorted(rows, key=lambda x: x['filing_date']):
            if r['filing_date'] < cutoff:
                continue
            if r['insider_cik'] not in seen_ciks:
                seen_ciks[r['insider_cik']] = r
        unique = sorted(seen_ciks.values(), key=lambda x: x['filing_date'])
        if len(unique) < 2:
            continue
        i1, i2 = unique[0], unique[1]
        ctype  = 'A' if i1['filing_date'] == i2['filing_date'] else 'B'
        entry  = next_trading_day(i2['filing_date'])
        signals.append({
            'ticker':  ticker,
            'ctype':   ctype,
            'i1_name': i1['insider_name'],
            'i1_role': i1['insider_role'],
            'i1_val':  i1['value'],
            'i2_name': i2['insider_name'],
            'i2_role': i2['insider_role'],
            'i2_val':  i2['value'],
            'date1':   i1['filing_date'],
            'date2':   i2['filing_date'],
            'combined': i1['value'] + i2['value'],
            'entry':   entry,
            'exit_by': entry + timedelta(days=HOLD_DAYS),
            'detected': datetime.now(timezone.utc).isoformat(),
        })
    return signals


# ── FILTERS ───────────────────────────────────────────────────────────────

def apply_filters(s):
    mcap = get_market_cap(s['ticker'])
    if mcap and mcap < MIN_MARKET_CAP:
        return False, 'mktcap $' + str(int(mcap))
    if s['entry'] == date.today():
        gap = get_gap_pct(s['ticker'])
        if gap is not None and gap > MAX_GAP_PCT:
            return False, 'gap+' + str(round(gap * 100, 1)) + '%'
    return True, ''


# ── FORMAT ALERT ──────────────────────────────────────────────────────────

def format_alert(s):
    nl = chr(10)
    return (
        '*CLUSTER — ' + s['ticker'] + '* (Type ' + s['ctype'] + ')' + nl + nl
        + '*' + s['i1_role'] + ':* ' + s['i1_name'] + nl
        + '   $' + '{:,.0f}'.format(s['i1_val']) + '  on ' + str(s['date1']) + nl + nl
        + '*' + s['i2_role'] + ':* ' + s['i2_name'] + nl
        + '   $' + '{:,.0f}'.format(s['i2_val']) + '  on ' + str(s['date2']) + nl + nl
        + '*Combined:* $' + '{:,.0f}'.format(s['combined']) + nl
        + '*Entry:* ' + str(s['entry']) + '   *Exit by:* ' + str(s['exit_by']) + nl + nl
        + 'https://www.tradingview.com/chart/?symbol=' + s['ticker'] + nl
        + 'https://openinsider.com/' + s['ticker']
    )


def make_row(s, status, reason):
    return [
        s['ticker'], s['ctype'],
        s['i1_name'], s['i1_role'], '${:,.0f}'.format(s['i1_val']),
        s['i2_name'], s['i2_role'], '${:,.0f}'.format(s['i2_val']),
        str(s['date1']), str(s['date2']),
        '${:,.0f}'.format(s['combined']),
        str(s['entry']), str(s['exit_by']),
        status, reason, s['detected'],
    ]


# ── SMOKE TEST ────────────────────────────────────────────────────────────

def run_smoke_test():
    print('=== SMOKE TEST MODE ===')
    print('Sending fake signal to verify Notion + Telegram connections...')
    fake = {
        'ticker':   'TEST',
        'ctype':    'A',
        'i1_name':  'John Smith',
        'i1_role':  'CEO',
        'i1_val':   250000,
        'i2_name':  'Jane Doe',
        'i2_role':  'CFO',
        'i2_val':   180000,
        'date1':    date.today(),
        'date2':    date.today(),
        'combined': 430000,
        'entry':    date.today(),
        'exit_by':  date.today() + timedelta(days=5),
        'detected': datetime.now(timezone.utc).isoformat(),
    }
    row = make_row(fake, 'TEST', 'smoke test')
    notion_ok = append_to_notion(row)
    send_telegram(format_alert(fake))
    if notion_ok:
        print('=== SMOKE TEST PASSED: check Notion and Telegram ===')
    else:
        print('=== SMOKE TEST FAILED: Notion write failed — check secrets ===')


# ── MAIN ──────────────────────────────────────────────────────────────────

def main():
    print('=== insider_bot started ' + datetime.now(timezone.utc).isoformat() + ' ===')

    entries = fetch_entries(days_back=3)
    all_filings, no_xml, skipped = [], 0, 0

    for e in entries:
        _, xml_text = fetch_xml(e['index_url'])
        if not xml_text:
            no_xml += 1
            continue
        parsed = parse_xml(xml_text)
        if not parsed:
            skipped += 1
            continue
        all_filings.extend(parsed)

    print('Qualifying P-transactions: ' + str(len(all_filings)))
    signals = detect_clusters(all_filings)
    print('Clusters detected: ' + str(len(signals)))

    if not signals:
        print('No clusters today — done.')
        return

    seen      = load_seen()
    new_count = 0

    for s in signals:
        key = s['ticker'] + '|' + str(s['date1']) + '|' + str(s['date2'])
        if key in seen:
            print('  SKIP (already logged): ' + s['ticker'])
            continue

        passed, reason = apply_filters(s)
        status = 'PENDING' if passed else 'REJECTED'
        print('  ' + status + ': ' + s['ticker'] + ('  — ' + reason if reason else ''))

        append_to_notion(make_row(s, status, reason))

        if passed:
            send_telegram(format_alert(s))
            new_count += 1

    print('Done: ' + str(len(entries)) + ' entries, '
          + str(no_xml) + ' no-xml, '
          + str(skipped) + ' no-P-txns, '
          + str(len(signals)) + ' clusters, '
          + str(new_count) + ' alerts sent.')


if __name__ == '__main__':
    if TEST_MODE:
        run_smoke_test()
    else:
        main()
'''
Path('/root/insider_bot_patched.py').write_text(code)
print('Wrote /root/insider_bot_patched.py')
print(code[:1200])
