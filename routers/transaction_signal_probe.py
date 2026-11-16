"""Transaction webhook observer with an optional bounded read-only API probe."""
import base64
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from database import SessionLocal
from models import PoyntConnection

router = APIRouter()
logger = logging.getLogger('transaction_signal_probe')
EVENTS = {'TRANSACTION_CAPTURED', 'TRANSACTION_UPDATED'}
_seen = OrderedDict()


def validate_notification(body, signature, secret, app_id, hook_id):
    if not secret or len(secret) < 32 or not app_id or not hook_id:
        raise HTTPException(503, 'Transaction probe is not configured.')
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha1).digest()).decode()
    if not hmac.compare_digest(expected, signature or ''):
        raise HTTPException(401, 'Invalid webhook signature.')
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError()
        for key in ('id', 'businessId', 'resourceId', 'hookId'):
            if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 100:
                raise ValueError()
        if data.get('storeId') is not None and (not isinstance(data['storeId'], str) or not 1 <= len(data['storeId']) <= 100):
            raise ValueError()
        if data.get('applicationId') != app_id or data['hookId'] != hook_id:
            raise HTTPException(403, 'Unexpected transaction probe registration.')
        if data.get('resource') != '/transactions' or data.get('eventType') not in EVENTS:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, 'Invalid transaction notification.') from None
    return data


def evidence(data, organization_id, now):
    # Provider event creation time is not necessarily cashier completion time.
    lag = None
    created = data.get('createdAt')
    if isinstance(created, str):
        try:
            at = datetime.fromisoformat(created.replace('Z', '+00:00'))
            if at.tzinfo is not None:
                lag = round((now - at).total_seconds() * 1000)
        except ValueError:
            pass
    key = (organization_id, data['hookId'], data['id'])
    duplicate = key in _seen
    _seen[key] = True
    _seen.move_to_end(key)
    while len(_seen) > 5000:
        _seen.popitem(last=False)
    return {'event': 'transaction_probe_notification', 'organization_id': organization_id,
            'event_type': data['eventType'], 'notification_id': data['id'],
            'transaction_id': data['resourceId'], 'store_id': data.get('storeId'),
            'received_at': now.isoformat(), 'event_created_at': created if lag is not None else None,
            'provider_event_age_ms': lag, 'duplicate_in_this_process': duplicate}


@router.post('/webhooks/poynt/transaction-probe/{organization_id:int}', include_in_schema=False)
async def transaction_probe(request: Request, organization_id: int, background_tasks: BackgroundTasks):
    if os.getenv('POYNT_TRANSACTION_PROBE_ENABLED') != '1':
        raise HTTPException(404, 'Transaction probe is disabled.')
    try:
        configured_org = int(os.getenv('POYNT_TRANSACTION_PROBE_ORGANIZATION_ID', '1'))
    except ValueError:
        raise HTTPException(503, 'Transaction probe is not configured.') from None
    if organization_id != configured_org:
        raise HTTPException(403, 'Unexpected organization.')
    received = datetime.now(timezone.utc)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, 'Notification too large.')
    data = validate_notification(bytes(body), request.headers.get('poynt-webhook-signature'),
        os.getenv('POYNT_TRANSACTION_PROBE_SECRET'), os.getenv('POYNT_APP_ID'),
        os.getenv('POYNT_TRANSACTION_PROBE_HOOK_ID'))
    with SessionLocal() as session:
        connection = session.scalar(select(PoyntConnection).where(
            PoyntConnection.organization_id == organization_id,
            PoyntConnection.business_id == data['businessId']))
        if connection is None:
            raise HTTPException(403, 'Notification does not match the active Poynt connection.')
    # Log only allowlisted correlation metadata. Never trust or follow payload URLs.
    logger.info('TRANSACTION_PROBE %s', json.dumps(evidence(data, organization_id, received), separators=(',', ':')))
    if os.getenv('POYNT_TRANSACTION_LOOKUP_ENABLED') == '1':
        from transaction_order_probe import run_probe
        background_tasks.add_task(run_probe, data, organization_id, received)
    return {'accepted': True, 'mode': 'observe_only'}
