"""Read-only Poynt evidence for two Truck payments; never starts FTW."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from trace_offline_orders import summarize

EXAMPLES = [('paylink', '2026-11-02', 36754, '25665'),
            ('invoice', '2026-09-12', 24311, '000060')]


def transaction_summary(t):
    amounts = t.get('amounts') or {}
    # Export structural evidence, not funding source, processor responses,
    # customer details, free-form notes, or arbitrary reference text.
    refs = []
    for r in t.get('references') or []:
        value = str(r.get('id') or '')
        if re.fullmatch(r'[0-9a-fA-F-]{8,100}|[0-9]{1,20}', value):
            refs.append({k: r.get(k) for k in ('type', 'customType', 'id')})
    return {'id': t.get('id'), 'parentId': t.get('parentId'),
            'createdAt': t.get('createdAt'), 'updatedAt': t.get('updatedAt'),
            'action': t.get('action'), 'status': t.get('status'),
            'settled': t.get('settled'), 'voided': t.get('voided'),
            'storeId': (t.get('context') or {}).get('storeId'),
            'topLevelFields': sorted(t), 'references': refs,
            'amounts': {k: amounts.get(k) for k in
                        ('currency', 'orderAmount', 'transactionAmount', 'tipAmount', 'taxAmount')}}


def fetch_collection(http, path, params, key, transform):
    output, pages = [], []
    expected = None
    for offset in range(0, 5000, 100):
        response = http.get(path, params={**params, 'limit': 100, 'startOffset': offset})
        if not response.is_success:
            return {'http_status': response.status_code, 'complete': False,
                    'pages': pages, 'records': output}
        data = response.json()
        batch = data.get(key)
        # Poynt can omit the collection entirely for an empty result.
        if batch is None and data.get('count') == 0:
            batch = []
        if not isinstance(batch, list):
            return {'http_status': response.status_code, 'complete': False,
                    'error': 'Unexpected collection shape', 'topLevelFields': sorted(data),
                    'provider_count': data.get('count'),
                    'pages': pages, 'records': output}
        if offset == 0 and isinstance(data.get('count'), int) and data['count'] > 0:
            expected = data['count']
        pages.append({'offset': offset, 'returned': len(batch), 'count': data.get('count')})
        output.extend(transform(x) for x in batch)
        if not batch or (expected is not None and len(output) >= expected) or (expected is None and len(batch) < 100):
            break
    else:
        return {'http_status': 200, 'complete': False, 'error': '5000 record safety limit',
                'pages': pages, 'records': output}
    counts = Counter(x.get('id') for x in output)
    return {'http_status': 200, 'complete': expected is None or len(output) >= expected,
            'expected_count': expected, 'returned': len(output),
            'duplicate_ids': [k for k, v in counts.items() if v > 1],
            'pages': pages, 'records': output}


def run(args):
    if Path(args.output).exists():
        raise ValueError('Output already exists; choose another --output filename.')
    if not Path(args.dotenv).is_file():
        raise ValueError('Environment file not found.')
    load_dotenv(args.dotenv, override=True)
    url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(url, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        connection = db.execute('SELECT business_id, access_token, token_type, expires_at '
            'FROM poynt_connections WHERE organization_id = %s', (args.organization_id,)).fetchone()
        stores = db.execute('SELECT store_id, display_name, poynt_name, timezone_name '
            'FROM organization_stores WHERE organization_id = %s', (args.organization_id,)).fetchall()
    if not connection:
        raise ValueError('No saved Poynt connection.')
    expiry = connection['expires_at']
    if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Saved token expired. Open FTW so it refreshes, then rerun.')
    headers = {'Accept': 'application/json', 'api-version': '1.2',
               'Authorization': (connection['token_type'] or 'BEARER') + ' ' + connection['access_token']}
    prefix = '/businesses/' + quote(connection['business_id'], safe='')
    report = {'organization_id': args.organization_id, 'stores': stores, 'examples': []}
    with httpx.Client(base_url='https://services.poynt.net', headers=headers,
                      timeout=60, follow_redirects=False) as http:
        for label, day_string, cents, number in EXAMPLES:
            day = date.fromisoformat(day_string)
            zone = ZoneInfo('America/Phoenix')
            start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
            end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
            params = {'timeType': 'createdAt', 'startAt': start.isoformat(), 'endAt': end.isoformat()}
            # Query the business to detect unexpected/missing store assignments too.
            orders = fetch_collection(http, prefix + '/orders', params, 'orders', summarize)
            payments = fetch_collection(http, prefix + '/transactions', params, 'transactions', transaction_summary)
            linked = {t.get('id') for o in orders['records'] for t in o.get('transactions', [])}
            for t in payments['records']:
                t['attached_to_order_in_this_window'] = (t['id'] in linked if orders['complete'] else None)
            report['examples'].append({'type': label, 'date': day_string, 'expected_amount_cents': cents,
                'displayed_document_number': number, 'expected_store': 'Ice Cream Crush Truck',
                'query': params, 'orders': orders, 'transactions': payments,
                'candidate_transaction_ids': [t['id'] for t in payments['records']
                    if cents in (t['amounts'].get('orderAmount'), t['amounts'].get('transactionAmount'))]})
            print(label + ': orders HTTP ' + str(orders['http_status']) + ', transactions HTTP ' + str(payments['http_status']))
    with Path(args.output).open('x', encoding='utf-8') as out:
        json.dump(report, out, indent=2, default=str)
    print('Saved ' + args.output + '. No database or provider writes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotenv', default='.env.local-prod-db')
    parser.add_argument('--organization-id', type=int, default=1)
    parser.add_argument('--output', default='external_payments_trace.json')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        print(str(error) if type(error) is ValueError else
              'Stopped: ' + type(error).__name__ + '. Credentials and response bodies omitted.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
