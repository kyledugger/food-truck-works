"""Explicitly register/list/remove a separate Poynt transaction probe hook.

Reads saved credentials with SELECT only. Register/remove change only the
separate provider webhook; existing order hooks and database are untouched.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, quote
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

EVENTS = ['TRANSACTION_CAPTURED', 'TRANSACTION_UPDATED']


def request(http, method, path, **kwargs):
    response = http.request(method, path, headers={'Poynt-Request-Id': str(uuid4())}, **kwargs)
    if not response.is_success:
        raise ValueError('Poynt ' + method + ' returned HTTP ' + str(response.status_code) + '; body omitted.')
    return response.json() if response.content else {}


def list_hooks(http, business):
    rows, seen = [], set()
    for offset in range(0, 2000, 100):
        data = request(http, 'GET', '/hooks', params={'businessId': business, 'limit': 100, 'startOffset': offset})
        batch = data.get('hooks')
        if batch is None and data.get('count') == 0:
            return rows
        if not isinstance(batch, list):
            raise ValueError('Unexpected hooks response. No webhook changes performed.')
        signature = tuple(x.get('id') for x in batch)
        if batch and signature in seen:
            raise ValueError('Provider repeated a hook page. No webhook changes performed.')
        seen.add(signature)
        rows.extend(batch)
        if len(batch) < 100 and not any(x.get('rel') == 'next' for x in data.get('links', [])):
            return rows
    raise ValueError('Hook list exceeded 2000 records. No webhook changes performed.')


def callback(base, organization_id):
    url = urlsplit(base)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/')):
        raise ValueError('--public-url must be an HTTPS origin, e.g. https://foodtruckworks.com')
    return base.rstrip('/') + '/webhooks/poynt/transaction-probe/' + str(organization_id)


def run(args):
    if not Path(args.dotenv).is_file():
        raise ValueError('Environment file not found.')
    load_dotenv('.env', override=False)
    load_dotenv(args.dotenv, override=True)
    app_id = os.environ['POYNT_APP_ID']
    secret = os.getenv('POYNT_TRANSACTION_PROBE_SECRET', '')
    if args.action == 'register' and len(secret) < 32:
        raise ValueError('Set POYNT_TRANSACTION_PROBE_SECRET to at least 32 characters first; do not share it.')
    db_url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(db_url, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        connection = db.execute('SELECT business_id, access_token, token_type, expires_at '
            'FROM poynt_connections WHERE organization_id = %s', (args.organization_id,)).fetchone()
    if not connection:
        raise ValueError('No saved connection for this organization.')
    expiry = connection['expires_at']
    if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Saved token expired. Open FTW to let it refresh, then rerun.')
    delivery_url = callback(args.public_url, args.organization_id)
    journal = Path(args.journal)
    with httpx.Client(base_url='https://services.poynt.net', timeout=30, follow_redirects=False,
            headers={'Accept': 'application/json', 'api-version': '1.2',
                     'Authorization': (connection['token_type'] or 'BEARER') + ' ' + connection['access_token']}) as http:
        hooks = list_hooks(http, connection['business_id'])
        matches = [h for h in hooks if h.get('applicationId') == app_id
                   and h.get('businessId') == connection['business_id'] and h.get('deliveryUrl') == delivery_url]
        if len(matches) > 1:
            raise ValueError('Multiple probe registrations match. No changes; review them first.')
        if args.action == 'list':
            print(json.dumps({'matching_probe_hooks': [{k: h.get(k) for k in ('id','active','eventTypes')}
                for h in matches], 'existing_order_hooks': sum(any(str(e).startswith('ORDER_')
                for e in h.get('eventTypes', [])) for h in hooks)}, indent=2))
            return
        if args.action == 'remove':
            if not journal.is_file():
                raise ValueError('Registration journal required for safe removal.')
            recorded = json.loads(journal.read_text())
            if (recorded.get('delivery_url') != delivery_url or recorded.get('business_id') != connection['business_id']
                    or recorded.get('organization_id') != args.organization_id or recorded.get('application_id') != app_id):
                raise ValueError('Journal does not match this probe. No changes.')
            if not matches:
                print('No matching probe remains. Order webhooks unchanged.')
                return
            if matches[0].get('id') != recorded.get('hook_id'):
                raise ValueError('Hook ID differs from journal. No changes.')
            if set(matches[0].get('eventTypes') or []) != set(EVENTS):
                raise ValueError('Hook has unexpected event types; will not remove it.')
            request(http, 'DELETE', '/hooks/' + quote(recorded['hook_id'], safe=''))
            print('Removed only the journaled transaction probe hook. Existing order webhooks unchanged.')
            return
        if matches:
            hook = matches[0]
            if set(hook.get('eventTypes') or []) != set(EVENTS) or hook.get('active') is False:
                raise ValueError('Existing probe has unexpected configuration. No changes.')
            print('Reusing existing probe; keep the same signing secret.')
        else:
            if journal.exists():
                raise ValueError('A previous journal exists but no matching hook was found. Review before registering again.')
            hook = request(http, 'POST', '/hooks', json={'applicationId': app_id,
                'businessId': connection['business_id'], 'deliveryUrl': delivery_url,
                'secret': secret, 'eventTypes': EVENTS})
        hook_id = hook.get('id')
        if not isinstance(hook_id, str) or not 1 <= len(hook_id) <= 100:
            raise ValueError('Registration response lacks a valid hook ID. List hooks before retrying.')
        record = {'hook_id': hook_id, 'business_id': connection['business_id'],
            'application_id': app_id, 'organization_id': args.organization_id, 'delivery_url': delivery_url}
        if journal.exists() and json.loads(journal.read_text()) != record:
            raise ValueError('Existing journal belongs to a different registration. Will not overwrite it.')
        journal.write_text(json.dumps(record, indent=2)+'\n')
        print('Set this on the FTW server, then restart/redeploy:')
        print('POYNT_TRANSACTION_PROBE_HOOK_ID=' + hook_id)
        print('Registration journal saved; contains no signing secret or access token.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'register', 'remove'])
    parser.add_argument('--public-url', required=True)
    parser.add_argument('--organization-id', type=int, default=1)
    parser.add_argument('--dotenv', default='.env.local-prod-db')
    parser.add_argument('--journal', default='transaction_probe_registration.json')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        print(str(error) if type(error) is ValueError else 'Stopped: ' + type(error).__name__ + '; sensitive details omitted.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
