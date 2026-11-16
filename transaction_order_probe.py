"""Bounded, read-only API experiment. Never saves orders or creates tickets."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import quote

import httpx
from live_dashboard_metrics import completed, number, order_store
from poynt.connection import get_poynt_credentials

logger = logging.getLogger('transaction_signal_probe')
OFFSETS = (0, 2, 4, 6, 9, 14)
MAX_TRANSACTIONS = 2


def settings():
    if os.getenv('POYNT_TRANSACTION_LOOKUP_ENABLED') != '1':
        return None
    run_id = os.getenv('POYNT_TRANSACTION_LOOKUP_RUN_ID', '')
    store = os.getenv('POYNT_TRANSACTION_LOOKUP_STORE_ID', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id) or not 1 <= len(store) <= 100:
        raise ValueError('Lookup probe requires a run ID and target store ID.')
    return run_id, store


def emit(event, **fields):
    logger.info('TRANSACTION_ORDER_PROBE %s', json.dumps(
        {'event': event, 'at': datetime.now(timezone.utc).isoformat(), **fields}, separators=(',', ':')))


def reserve(data, organization_id, run_id, path=None):
    """One two-transaction budget per run/hook across workers on ONE host.

    SQLite is a local diagnostic journal, not FTW's application database.
    Reservations remain consumed after a crash, preferring bounded API use.
    """
    scope = hashlib.sha256(json.dumps([organization_id, data['businessId'],
        data['hookId'], run_id]).encode()).hexdigest()
    if path is None:
        path = Path(tempfile.gettempdir()) / ('ftw-order-probe-' + scope + '.sqlite3')
    path = Path(path)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass
    with sqlite3.connect(str(path), timeout=0.25) as db:
        db.execute('CREATE TABLE IF NOT EXISTS reservations '
                   '(scope TEXT NOT NULL, transaction_id TEXT NOT NULL, '
                   'PRIMARY KEY(scope, transaction_id))')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM reservations WHERE scope=? AND transaction_id=?',
                      (scope, data['resourceId'])).fetchone():
            return 'already_reserved'
        if db.execute('SELECT COUNT(*) FROM reservations WHERE scope=?', (scope,)).fetchone()[0] >= MAX_TRANSACTIONS:
            return 'run_budget_exhausted'
        db.execute('INSERT INTO reservations VALUES (?,?)', (scope, data['resourceId']))
    return 'reserved'


def matching_order(order, transaction_id, store_id, reference=None):
    if order_store(order) != store_id:
        return False
    if reference and str(order.get('id')) == reference:
        return True
    return any(str(t.get('id')) == transaction_id for t in order.get('transactions') or [])


def order_evidence(order):
    items = order.get('items') or []
    preparable = sum(i.get('status') not in {'RETURNED', 'CANCELLED'}
                     and number(i.get('quantity')) > 0 for i in items)
    statuses = order.get('statuses') or {}
    return {'order_id': str(order.get('id') or ''), 'order_number': str(order.get('orderNumber') or ''),
            'order_created_at': order.get('createdAt'), 'order_updated_at': order.get('updatedAt'),
            'order_status': statuses.get('status'),
            'payment_summary': statuses.get('transactionStatusSummary'),
            'item_rows': len(items), 'preparable_rows': preparable,
            'sale_eligible': completed(order),
            'eligible_order_content': completed(order) and preparable > 0}


class StopProbe(Exception):
    pass


async def lookup_series(data, organization_id, received_at, run_id, store_id, *,
                        http_factory=httpx.AsyncClient, sleep=asyncio.sleep,
                        monotonic=time.monotonic, credentials=None):
    transaction_id = data['resourceId']
    common = {'run_id': run_id, 'organization_id': organization_id,
              'transaction_id': transaction_id, 'store_id': store_id,
              'trigger_received_at': received_at.isoformat()}
    started = monotonic()
    creds = credentials or await asyncio.to_thread(get_poynt_credentials, organization_id)
    if not creds or creds.business_id != data['businessId']:
        emit('lookup_stopped', **common, reason='connection_changed', requests=0)
        return
    # No token refresh: this diagnostic never writes the FTW database.
    expiry = creds.expires_at
    if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        emit('lookup_stopped', **common, reason='saved_token_expired', requests=0)
        return
    prefix = '/businesses/' + quote(creds.business_id, safe='')
    headers = {'Accept': 'application/json', 'api-version': '1.2',
               'Authorization': (creds.token_type or 'BEARER') + ' ' + creds.access_token}
    request_count, known_reference = 0, None
    first_match, first_eligible, failed = None, None, False
    async with http_factory(base_url='https://services.poynt.net', headers=headers,
                            timeout=3, follow_redirects=False) as http:
        async def get(path, params=None):
            nonlocal request_count
            request_count += 1
            response = await http.get(path, params=params)
            if response.status_code == 404:
                return None
            if not response.is_success:
                emit('lookup_http_error', **common, http_status=response.status_code,
                     request_number=request_count)
                raise StopProbe()
            payload = response.json()
            if not isinstance(payload, dict):
                raise StopProbe()
            return payload

        try:
            for attempt, offset in enumerate(OFFSETS, 1):
                await sleep(max(0, started + offset - monotonic()))
                attempt_start = datetime.now(timezone.utc)
                orders, truncated, source = [], False, 'direct_order'
                if known_reference:
                    order = await get(prefix + '/orders/' + quote(known_reference, safe=''))
                    if order is not None:
                        if str(order.get('id')) != known_reference:
                            raise StopProbe()
                        orders = [order]
                    else:
                        known_reference = None
                else:
                    transaction = await get(prefix + '/transactions/' + quote(transaction_id, safe=''))
                    if transaction is not None:
                        if str(transaction.get('id')) != transaction_id:
                            raise StopProbe()
                        tx_store = (transaction.get('context') or {}).get('storeId')
                        if tx_store and tx_store != store_id:
                            raise StopProbe()
                        refs = {str(r['id']) for r in transaction.get('references') or []
                                if r.get('type') == 'POYNT_ORDER' and isinstance(r.get('id'), str)
                                and 1 <= len(r['id']) <= 100}
                        if len(refs) == 1:
                            known_reference = next(iter(refs))
                    if known_reference:
                        order = await get(prefix + '/orders/' + quote(known_reference, safe=''))
                        if order is not None:
                            if str(order.get('id')) != known_reference:
                                raise StopProbe()
                            orders = [order]
                        else:
                            known_reference = None
                    else:
                        source = 'recent_order_window'
                        # Bounded discovery query, not a full-day download or count+page pair.
                        payload = await get(prefix + '/orders', params={
                            'timeType': 'createdAt', 'storeId': store_id, 'limit': 100, 'startOffset': 0,
                            'startAt': (received_at - timedelta(minutes=15)).isoformat(),
                            'endAt': (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()})
                        if payload is not None:
                            orders = payload.get('orders')
                            if orders is None and payload.get('count') == 0:
                                orders = []
                            if not isinstance(orders, list):
                                raise StopProbe()
                            truncated = len(orders) >= 100 or (isinstance(payload.get('count'), int)
                                and payload['count'] > len(orders))
                matches = [o for o in orders if matching_order(o, transaction_id, store_id,
                           known_reference if source == 'direct_order' else None)]
                found = order_evidence(matches[0]) if len(matches) == 1 else None
                finished = datetime.now(timezone.utc)
                elapsed = round((finished - received_at).total_seconds() * 1000)
                if found and first_match is None:
                    first_match = finished.isoformat()
                if found and found['eligible_order_content'] and first_eligible is None:
                    first_eligible = finished.isoformat()
                emit('order_lookup_attempt', **common, attempt=attempt,
                     scheduled_offset_seconds=offset, lookup_started_at=attempt_start.isoformat(),
                     lookup_finished_at=finished.isoformat(), elapsed_from_trigger_ms=elapsed,
                     requests_so_far=request_count, source=source, examined_orders=len(orders),
                     search_truncated=truncated, match_count=len(matches), order=found)
                if first_eligible:
                    break
        except StopProbe:
            failed = True
            emit('lookup_stopped', **common, reason='http_or_response_validation', requests=request_count)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            failed = True
            # Never log arbitrary provider bodies, URLs, headers, or exception text.
            emit('lookup_stopped', **common, reason=type(error).__name__, requests=request_count)
    emit('order_lookup_summary', **common, requests=request_count,
         first_matching_order_observed_at=first_match,
         first_eligible_order_observed_at=first_eligible,
         outcome='eligible_order_observed' if first_eligible else 'stopped_on_error' if failed else 'not_observed_within_probe')


async def run_probe(data, organization_id, received_at):
    """Runs after webhook acknowledgement via FastAPI BackgroundTasks."""
    try:
        config = settings()
        if config is None:
            return
        run_id, store_id = config
        if data.get('storeId') != store_id:
            return
        # Start one bounded series on capture. Updates/retries remain visible
        # in passive logs, but must not start another series for the same sale.
        if data.get('eventType') != 'TRANSACTION_CAPTURED':
            return
        event_at = datetime.fromisoformat(str(data.get('createdAt', '')).replace('Z', '+00:00'))
        age = (received_at - event_at).total_seconds() if event_at.tzinfo else None
        if age is None or not -10 <= age <= 60:
            emit('lookup_skipped', run_id=run_id, transaction_id=data['resourceId'], reason='stale_or_unusable_event_time')
            return
        result = await asyncio.to_thread(reserve, data, organization_id, run_id)
        if result != 'reserved':
            emit('lookup_skipped', run_id=run_id, transaction_id=data['resourceId'], reason=result)
            return
        emit('lookup_started', run_id=run_id, transaction_id=data['resourceId'],
             trigger_type=data['eventType'], trigger_received_at=received_at.isoformat())
        await asyncio.wait_for(lookup_series(data, organization_id, received_at, run_id, store_id), timeout=35)
    except asyncio.TimeoutError:
        emit('lookup_stopped', transaction_id=data['resourceId'], reason='35_second_deadline')
    except Exception as error:
        emit('lookup_stopped', transaction_id=data['resourceId'], reason=type(error).__name__)
