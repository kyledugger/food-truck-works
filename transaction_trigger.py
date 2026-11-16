"""Durable transaction sideband; existing organization lease serializes work."""
from datetime import timedelta
import logging
import os

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from models import OrganizationStore
from kitchen_models import KitchenSession
from live_dashboard_models import TransactionSignal, TransactionSignalReceipt, TransactionSignalBudget
from live_dashboard_metrics import instant, order_store, completed, number
from poynt.client import PoyntSignalAPIError
from store_time import utc_now, utc_iso

logger = logging.getLogger(__name__)
RETRY_SECONDS = (2, 2, 2, 3, 5)


def enabled():
    return os.getenv('POYNT_TRANSACTION_TRIGGER_ENABLED') == '1'


def allowed_stores():
    return {s.strip() for s in os.getenv('POYNT_TRANSACTION_TRIGGER_STORE_IDS', '').split(',') if s.strip()}


def active_kitchen(session, org, business, store_id, now):
    return session.scalar(select(OrganizationStore.id).join(KitchenSession,
        KitchenSession.store_id == OrganizationStore.id).where(
        OrganizationStore.organization_id == org, OrganizationStore.store_id == store_id,
        OrganizationStore.is_active.is_(True), KitchenSession.business_id == business,
        KitchenSession.active.is_(True), KitchenSession.last_seen_at >= now-timedelta(minutes=15))) is not None


def enqueue(session, org, data, now=None):
    """Called only AFTER signature, hook, application and tenant validation."""
    now = now or utc_now()
    store, business = data.get('storeId'), data['businessId']
    if not enabled() or store not in allowed_stores():
        return 'not_enabled_for_store'
    try:
        if not -30 <= (now-instant(data.get('createdAt'))).total_seconds() <= 300:
            return 'stale_event'
    except (ValueError, TypeError):
        return 'invalid_event_time'
    if not active_kitchen(session, org, business, store, now):
        return 'kitchen_inactive_or_needs_resume'
    try:
        with session.begin_nested():
            session.add(TransactionSignalReceipt(organization_id=org, business_id=business,
                notification_id=data['id'], received_at=now))
            session.flush()
    except IntegrityError:
        return 'duplicate_delivery'
    query = select(TransactionSignal).where(TransactionSignal.organization_id == org,
        TransactionSignal.business_id == business, TransactionSignal.transaction_id == data['resourceId'])
    job = session.scalar(query.with_for_update())
    fresh = False
    if job is None:
        try:
            with session.begin_nested():
                job = TransactionSignal(organization_id=org, business_id=business, store_id=store,
                    transaction_id=data['resourceId'], state='pending', attempts=0, generation=1,
                    created_at=now, cycle_started_at=now, last_received_at=now, next_attempt_at=now)
                session.add(job);session.flush()
                fresh = True
        except IntegrityError:
            job = session.scalar(query.with_for_update())
    if not fresh:
        if job.store_id != store:
            return 'store_conflict'
        job.generation += 1
        if job.state == 'done' or (job.state in {'exhausted','ignored'} and now-job.cycle_started_at > timedelta(minutes=1)):
            job.state = 'pending';job.attempts = 0;job.cycle_started_at = now;job.last_error = None
        if job.state == 'pending':
            earliest = max(now, (job.last_attempt_at+timedelta(seconds=1)) if job.last_attempt_at else now)
            job.next_attempt_at = min(job.next_attempt_at, earliest)
        job.last_received_at = now
    logger.info('TRANSACTION_TRIGGER queued org=%s store=%s transaction=%s generation=%s',org,store,data['resourceId'],job.generation)
    return 'queued' if job.state == 'pending' else job.state


def get_backoff(session, org, business, now):
    row = session.get(TransactionSignalBudget, org)
    return row.backoff_until if row and row.business_id == business and row.backoff_until and row.backoff_until > now else None


class Deferred(Exception):
    def __init__(self, until): self.until = until


def request_slot(factory, org, business, token, owns):
    now = utc_now()
    with factory() as session:
        if not owns(session, org, business, token):
            raise RuntimeError('Signal lease lost')
        row = session.get(TransactionSignalBudget, org)
        if row is None:
            row = TransactionSignalBudget(organization_id=org,business_id=business,
                window_started_at=now,request_count=0)
            session.add(row)
        if row.business_id != business:
            row.business_id=business;row.window_started_at=now;row.request_count=0;row.backoff_until=None
        if row.backoff_until and row.backoff_until > now:
            raise Deferred(row.backoff_until)
        if now >= row.window_started_at+timedelta(minutes=1):
            row.window_started_at=now;row.request_count=0
        limit = max(2,min(120,int(os.getenv('POYNT_TRANSACTION_TRIGGER_REQUESTS_PER_MINUTE','30'))))
        if row.request_count >= limit:
            raise Deferred(row.window_started_at+timedelta(minutes=1))
        row.request_count += 1
        session.commit()


async def process_signals(factory, org, business, token, client, owns, save_order):
    now = utc_now()
    with factory() as session:
        if get_backoff(session, org, business, now):
            return True
        jobs = [(j.id,j.transaction_id,j.store_id,j.order_id,j.attempts,j.generation,j.cycle_started_at)
            for j in session.scalars(select(TransactionSignal).where(
                TransactionSignal.organization_id==org,TransactionSignal.business_id==business,
                TransactionSignal.state=='pending',TransactionSignal.next_attempt_at<=now)
                .order_by(TransactionSignal.next_attempt_at,TransactionSignal.id).limit(4)).all()]
    for job_id, transaction_id, store, reference, attempts, generation, cycle in jobs:
        matches=[];problem=None;defer=None;used=0;stop_all=False;ignored=False
        with factory() as session:
            eligible_store=store in allowed_stores() and active_kitchen(session,org,business,store,utc_now())
        if not eligible_store or utc_now()-cycle>timedelta(minutes=2) or attempts>=6:
            ignored=True
        else:
            async def get(resource, identifier=None, params=None):
                nonlocal used
                request_slot(factory,org,business,token,owns)
                used+=1
                return await client.signal_get(resource,identifier,params)
            try:
                if not reference:
                    tx=await get('transactions',transaction_id)
                    if str(tx.get('id'))!=transaction_id:
                        raise ValueError('Unexpected transaction ID')
                    tx_store=(tx.get('context') or {}).get('storeId')
                    if tx_store and tx_store!=store:
                        raise ValueError('Unexpected transaction store')
                    if any(r.get('customType')=='checkoutUrlId' for r in tx.get('references') or []):
                        ignored=True
                    refs={r['id'] for r in tx.get('references') or [] if r.get('type')=='POYNT_ORDER'
                        and isinstance(r.get('id'),str) and 1<=len(r['id'])<=100}
                    reference=next(iter(refs)) if len(refs)==1 else None
                if not ignored:
                    if reference:
                        order=await get('orders',reference)
                        if str(order.get('id'))!=reference:
                            raise ValueError('Unexpected order ID')
                        candidates=[order]
                    else:
                        page=await get('orders',params={'storeId':store,'timeType':'createdAt','limit':100,'startOffset':0,
                            'startAt':utc_iso(cycle-timedelta(minutes=15)), 'endAt':utc_iso(utc_now()+timedelta(seconds=1))})
                        candidates=page.get('orders', [] if page.get('count')==0 else None)
                        if not isinstance(candidates,list):
                            raise ValueError('Unexpected order page')
                        if len(candidates)>=100 or (isinstance(page.get('count'),int) and page['count']>len(candidates)):
                            logger.warning('TRANSACTION_TRIGGER search_truncated org=%s store=%s transaction=%s',org,store,transaction_id)
                    matches=[o for o in candidates if order_store(o)==store and
                        ((reference and str(o.get('id'))==reference) or
                         any(str(t.get('id'))==transaction_id for t in o.get('transactions') or []))]
                    if len(matches)>1:
                        matches=[];problem='ambiguous_order_match'
            except Deferred as exc:
                defer=exc.until;problem='local_request_budget'
            except PoyntSignalAPIError as exc:
                problem='http_'+str(exc.status_code)
                if exc.status_code==429:
                    defer=utc_now()+timedelta(seconds=exc.retry_after)
                    stop_all=True
            except Exception as exc:
                problem=type(exc).__name__
        with factory() as session:
            state=owns(session,org,business,token)
            if not state:
                return True
            job=session.scalar(select(TransactionSignal).where(TransactionSignal.id==job_id).with_for_update())
            if job is None:
                continue
            current=utc_now()
            if stop_all:
                budget=session.get(TransactionSignalBudget,org)
                budget.backoff_until=defer
                state.error='Poynt rate-limited. Waiting before retrying.'
                state.next_reconcile_at=max(state.next_reconcile_at,defer)
            ready=False
            saved_order_id=None
            if matches and active_kitchen(session,org,business,store,current):
                for order in matches:
                    save_order(session,org,business,order)
                    ready=completed(order) and any(i.get('status') not in {'RETURNED','CANCELLED'}
                        and number(i.get('quantity'))>0 for i in order.get('items') or [])
                saved_order_id=matches[0].get('id')
            if reference:
                job.order_id=reference
            job.last_error=problem
            if used:
                job.attempts=max(job.attempts,attempts+1);job.last_attempt_at=current
            if ignored:
                job.state='ignored'
            elif ready and job.generation==generation:
                job.state='done'
            elif job.attempts>=6:
                job.state='exhausted'
            elif defer:
                job.next_attempt_at=defer
            else:
                job.next_attempt_at=current+timedelta(seconds=RETRY_SECONDS[max(0,job.attempts-1)])
            session.execute(delete(TransactionSignalReceipt).where(TransactionSignalReceipt.organization_id==org,
                TransactionSignalReceipt.business_id==business,TransactionSignalReceipt.received_at<current-timedelta(days=3)))
            session.execute(delete(TransactionSignal).where(TransactionSignal.organization_id==org,
                TransactionSignal.business_id==business,TransactionSignal.last_received_at<current-timedelta(days=3)))
            session.commit()
            if saved_order_id is not None:
                logger.info('TRANSACTION_TRIGGER order_saved org=%s store=%s transaction=%s order=%s ready=%s',
                    org,store,transaction_id,saved_order_id,ready)
            logger.info('TRANSACTION_TRIGGER attempt org=%s transaction=%s attempt=%s requests=%s state=%s error=%s',
                org,transaction_id,job.attempts,used,job.state,problem)
        if stop_all:
            return True
    return False
