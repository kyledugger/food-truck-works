"""Read-only FTW/Poynt order evidence. Does not import or start the app."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


def summarize(order):
    statuses = order.get('statuses') or {}
    amounts = order.get('amounts') or {}
    return {
        'id': order.get('id'), 'orderNumber': order.get('orderNumber'),
        'createdAt': order.get('createdAt'), 'updatedAt': order.get('updatedAt'),
        'topLevelFields': sorted(order),
        'storeId': (order.get('context') or {}).get('storeId'),
        'status': statuses.get('status'),
        'transactionStatusSummary': statuses.get('transactionStatusSummary'),
        'netTotal': amounts.get('netTotal'), 'orderAmount': amounts.get('orderAmount'),
        'transactions': [{
            'id': t.get('id'), 'createdAt': t.get('createdAt'),
            'updatedAt': t.get('updatedAt'),
            'storeId': (t.get('context') or {}).get('storeId'),
            'status': t.get('status'),
        } for t in order.get('transactions') or []],
        'items': [{k: item.get(k) for k in ('name', 'quantity', 'unitPrice', 'discount', 'status')}
                  for item in order.get('items') or []],
    }


def run(args):
    if Path(args.output).exists():
        raise ValueError('Output already exists; choose another --output filename.')
    if not Path(args.dotenv).is_file():
        raise ValueError('Environment file not found.')
    load_dotenv(args.dotenv, override=True)
    url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(url, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        connection = db.execute(
            'SELECT business_id, access_token, token_type, expires_at FROM poynt_connections '
            'WHERE organization_id = %s', (args.organization_id,)).fetchone()
        stores = db.execute(
            'SELECT store_id, display_name, poynt_name, timezone_name FROM organization_stores '
            'WHERE organization_id = %s', (args.organization_id,)).fetchall()
    if not connection:
        raise ValueError('No saved Poynt connection for this organization.')
    expiry = connection['expires_at']
    if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Saved token expired. Open FTW so it refreshes, then rerun.')
    day = date.fromisoformat(args.date)
    zone = ZoneInfo(args.timezone)
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    path = '/businesses/' + quote(connection['business_id'], safe='') + '/orders'
    params = {'timeType': 'createdAt', 'startAt': start.isoformat(), 'endAt': end.isoformat()}
    headers = {'Accept': 'application/json', 'api-version': '1.2',
               'Authorization': (connection['token_type'] or 'BEARER') + ' ' + connection['access_token']}
    pages, orders = [], []
    with httpx.Client(base_url='https://services.poynt.net', headers=headers,
                      timeout=60, follow_redirects=False) as http:
        def get(query):
            response = http.get(path, params=query)
            if not response.is_success:
                raise ValueError('Poynt returned HTTP ' + str(response.status_code) + '; response omitted.')
            return response.json()
        count = int(get({**params, 'limit': 1}).get('count', 0))
        if not 0 <= count <= 5000:
            raise ValueError('Unexpected order count; diagnostic bounded to 5000 orders.')
        for offset in range(0, count, 100):
            data = get({**params, 'limit': min(100, count - offset), 'startOffset': offset})
            batch = data.get('orders')
            if not isinstance(batch, list):
                raise ValueError('Unexpected orders response shape.')
            pages.append({'offset': offset, 'returned': len(batch), 'count': data.get('count')})
            orders.extend(summarize(o) for o in batch)
    ids = [o['id'] for o in orders]
    report = {'organization_id': args.organization_id, 'date': args.date,
              'timezone': args.timezone, 'query': params, 'stores': stores,
              'provider_count': count, 'returned': len(orders), 'unique_ids': len(set(ids)),
              'duplicate_ids': [oid for oid, n in Counter(ids).items() if n > 1],
              'pages': pages, 'orders': orders}
    with Path(args.output).open('x', encoding='utf-8') as out:
        json.dump(report, out, indent=2, default=str)
    print('Saved ' + args.output + ': ' + str(len(orders)) + ' returned orders. No database or provider writes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotenv', default='.env.local-prod-db')
    parser.add_argument('--organization-id', type=int, default=1)
    parser.add_argument('--date', default='2026-11-14')
    parser.add_argument('--timezone', default='America/Phoenix')
    parser.add_argument('--output', default='offline_orders_2026-11-14.json')
    args = parser.parse_args()
    try:
        run(args)
    except ValueError as error:
        # Only deliberate diagnostic messages are surfaced; omit library exceptions.
        if type(error) is ValueError:
            print(str(error))
        else:
            print('Stopped: invalid provider data; details omitted.')
        return 1
    except Exception as error:
        print('Stopped: ' + type(error).__name__ + '. Credentials and response bodies omitted.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
