"""Slow, resumable transaction collection; never writes payroll or kitchen data."""
from datetime import timedelta
import logging
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from live_dashboard_models import AdditionalPaymentDay, AdditionalPaymentControl, DashboardPaymentLink
from models import OrganizationStore
from live_dashboard_metrics import instant
from store_time import utc_now, utc_iso, local_day_bounds
from zoneinfo import ZoneInfo
from transaction_trigger import request_slot, Deferred, get_backoff
from poynt.client import PoyntSignalAPIError

logger = logging.getLogger(__name__)
MAX_RECORDS = 10000


def save_links(session,org,business,order):
    order_id=identity(order.get('id'))
    if not order_id:raise ValueError('Invalid order identity')
    session.execute(delete(DashboardPaymentLink).where(DashboardPaymentLink.organization_id==org,
        DashboardPaymentLink.business_id==business,DashboardPaymentLink.order_id==order_id))
    for transaction_id in {t['id'] for t in order.get('transactions') or [] if isinstance(t,dict) and identity(t.get('id'))}:
        session.add(DashboardPaymentLink(organization_id=org,business_id=business,order_id=order_id,transaction_id=transaction_id))


def identity(value):
    return value if isinstance(value,str) and 1 <= len(value) <= 100 else None


def normalize(raw):
    """Allowlist only reporting metadata; never persist raw payment/card data."""
    if not isinstance(raw,dict) or not identity(raw.get('id')):
        raise ValueError('Invalid transaction identity')
    created=instant(raw.get('createdAt'))
    context=raw.get('context') or {}
    amounts=raw.get('amounts') or {}
    clean={}
    for key in ('orderAmount','transactionAmount','tipAmount'):
        value=amounts.get(key)
        if value is not None and (type(value) is not int or abs(value)>10**12):
            raise ValueError('Invalid payment amount')
        clean[key]=value
    refs=raw.get('references') or []
    return {'id':raw['id'],'created_at':utc_iso(created),'updated_at':utc_iso(instant(raw.get('updatedAt') or raw['createdAt'])),
        'parent_id':identity(raw.get('parentId')),'store_id':identity(context.get('storeId')),
        'source':str(context.get('source') or '')[:30],
        'action':str(raw.get('action') or '')[:30],'status':str(raw.get('status') or '')[:30],
        'voided':bool(raw.get('voided')),'currency':str(amounts.get('currency') or '')[:10],
        'amounts':clean,
        'order_linked':any(isinstance(r,dict) and r.get('type')=='POYNT_ORDER' for r in refs),
        'order_ids':sorted({r['id'] for r in refs if isinstance(r,dict) and r.get('type')=='POYNT_ORDER' and identity(r.get('id'))}),
        'paylink':any(isinstance(r,dict) and r.get('customType')=='checkoutUrlId' for r in refs),
        'invoice':any(isinstance(r,dict) and str(r.get('customType','')).lower() in {'invoiceid','invoice_id'} for r in refs)}


def request_day(session,org,business,day,stores,now):
    bounds=[]
    for store in stores:
        try:bounds.append(local_day_bounds(day,ZoneInfo(store.timezone_name or '')))
        except (ValueError,KeyError):pass
    if not bounds:return None
    start,end=min(b[0] for b in bounds),max(b[1] for b in bounds)
    query=select(AdditionalPaymentDay).where(AdditionalPaymentDay.organization_id==org,
        AdditionalPaymentDay.business_id==business,AdditionalPaymentDay.report_date==day)
    row=session.scalar(query)
    if row is None:
        try:
            with session.begin_nested():
                row=AdditionalPaymentDay(organization_id=org,business_id=business,report_date=day,
                    start_at=start,end_at=end,requested_at=now,next_sync_at=now)
                session.add(row);session.flush()
        except IntegrityError:row=session.scalar(query)
    # Store timezone changes require a fresh complete snapshot of the new union.
    if row.start_at!=start or row.end_at!=end:
        row.start_at=start;row.end_at=end;row.cursor=0;row.phase='orders';row.seen_order_ids=[];row.staged=[];row.snapshot=[]
        row.loaded_at=None;row.scan_started_at=None;row.next_sync_at=now
    row.requested_at=now
    return row


def amounts(record):
    a=record['amounts'];tip=a.get('tipAmount') or 0
    base=a.get('orderAmount')
    total=a.get('transactionAmount')
    if base is None and total is not None:base=abs(total)-abs(tip)
    if base is None:return None
    if total is not None and abs(total)!=abs(base)+abs(tip):return None
    return abs(base),abs(tip)


def summarize(session,org,business,store,start,end,row,order_sales,now):
    result={'loading':row is None or row.loaded_at is None,'error':row.error if row else None,
        'checked_at':utc_iso(row.loaded_at) if row else None,'sales_cents':0,'payments':[],
        'combined_sales_cents':None,'currency_warning':False,'review_count':0}
    if result['loading']:return result
    records={r['id']:r for r in row.snapshot}
    linked=set(session.scalars(select(DashboardPaymentLink.transaction_id).where(
        DashboardPaymentLink.organization_id==org,DashboardPaymentLink.business_id==business,
        DashboardPaymentLink.transaction_id.in_(list(records)))))
    captured_parents={r['parent_id'] for r in records.values() if r['action']=='CAPTURE' and not r['voided'] and r['status']=='CAPTURED'}
    def related(record):
        chain=[];seen=set()
        while record and record['id'] not in seen:
            seen.add(record['id']);chain.append(record)
            record=records.get(record.get('parent_id'))
        return chain
    for record in row.snapshot:
        if not start<=instant(record['created_at'])<end:continue
        chain=related(record)
        assigned=record['store_id'] or next((r['store_id'] for r in chain if r['store_id']),None)
        if assigned!=store.store_id:continue
        if any(r['store_id'] and r['store_id']!=assigned for r in chain):
            result['review_count']+=1;continue
        if any(r.get('order_linked') or r['order_ids'] or r['id'] in linked for r in chain):continue
        if any(r['voided'] for r in chain):continue
        kind='Pay link' if any(r['paylink'] for r in chain) else 'Invoice payment' if any(r['invoice'] for r in chain) else 'Additional payment'
        refund=record['action']=='REFUND' and record['status']=='REFUNDED'
        sale=record['action'] in {'SALE','CAPTURE'} and record['status'] in {'CAPTURED','REFUNDED','PARTIALLY_REFUNDED'}
        if not refund and not sale:continue
        if record['id'] in captured_parents:continue
        if record['currency']!='USD':result['currency_warning']=True;continue
        value=amounts(record)
        if sale and any(v is not None and v<0 for v in record['amounts'].values()):value=None
        # A refund with no resolvable original payment has unknown attribution.
        original=any(r['action'] in {'SALE','CAPTURE'} and r['status'] in {'CAPTURED','REFUNDED','PARTIALLY_REFUNDED'} for r in chain[1:])
        if value is None or (refund and (not original or any(r['currency']!='USD' for r in chain))):
            result['review_count']+=1;continue
        sales,tip=value
        sign=-1 if refund else 1
        result['payments'].append({'id':record['id'],'at':record['created_at'],
            'label':kind+(' refund' if refund else ''),'sales_cents':sign*sales,'tip_cents':sign*tip})
    result['payments'].sort(key=lambda p:(p['at'],p['id']))
    result['sales_cents']=sum(p['sales_cents'] for p in result['payments'])
    result['combined_sales_cents']=order_sales+result['sales_cents']
    result['stale']=row.next_sync_at<=now
    return result


async def process(factory,org,business,token,client,owns):
    """One GET per turn, at most four/minute/business, sharing the org lease.

    Staged pages never replace the last complete snapshot until the scan finishes.
    """
    now=utc_now()
    with factory() as session:
        if not owns(session,org,business,token) or get_backoff(session,org,business,now):return
        control=session.get(AdditionalPaymentControl,org)
        if control and control.business_id==business and control.next_request_at>now:return
        row=session.scalar(select(AdditionalPaymentDay).where(AdditionalPaymentDay.organization_id==org,
            AdditionalPaymentDay.business_id==business,AdditionalPaymentDay.next_sync_at<=now,
            AdditionalPaymentDay.requested_at>=now-timedelta(minutes=30))
            .order_by(AdditionalPaymentDay.next_sync_at,AdditionalPaymentDay.id).limit(1))
        if row is None:return
        if control is None:
            control=AdditionalPaymentControl(organization_id=org,business_id=business)
            session.add(control)
        control.business_id=business;control.next_request_at=now+timedelta(seconds=15)
        if row.scan_started_at is None:
            row.cursor=0;row.phase='orders';row.seen_order_ids=[];row.staged=[];row.scan_started_at=now
        row_id,cursor,start,end,staged=row.id,row.cursor,row.start_at,row.end_at,list(row.staged)
        phase,seen_orders=row.phase,list(row.seen_order_ids)
        session.commit()
    problem=None;finished=False;defer=None
    try:
        request_slot(factory,org,business,token,owns)
        if phase=='orders':
            page=await client.signal_get('orders',params={'timeType':'createdAt',
                'startAt':utc_iso(start),'endAt':utc_iso(end),'startOffset':cursor,'limit':100})
            batch=page.get('orders',[] if page.get('count')==0 else None)
            if not isinstance(batch,list) or len(batch)>100:raise ValueError('Invalid order page')
            if any((order.get('context') or {}).get('businessId') not in {None,business} for order in batch):
                raise ValueError('Unexpected order business')
            ids=[identity(order.get('id')) for order in batch]
            if None in ids or len(set(ids))!=len(ids) or set(ids)&set(seen_orders):raise ValueError('Duplicate order page')
            seen_orders+=ids
            if len(seen_orders)>MAX_RECORDS:raise ValueError('Order scan too large')
            with factory() as session:
                if not owns(session,org,business,token):return
                for order in batch:save_links(session,org,business,order)
                session.commit()
            count=page.get('count')
            has_next=any(link.get('rel')=='next' for link in page.get('links') or [])
            if (type(count) is int and count>cursor+len(batch)) or has_next or (len(batch)==100 and count is None):
                if not batch:raise ValueError('Incomplete order page')
                cursor+=len(batch)
            else:phase='transactions';cursor=0
        elif cursor>=0:
            page=await client.signal_get('transactions',params={'timeType':'createdAt',
                'startAt':utc_iso(start),'endAt':utc_iso(end),'startOffset':cursor,'limit':100})
            batch=page.get('transactions',[] if page.get('count')==0 else None)
            if not isinstance(batch,list) or len(batch)>100:raise ValueError('Invalid transaction page')
            if any((t.get('context') or {}).get('businessId') not in {None,business} for t in batch):
                raise ValueError('Unexpected transaction business')
            normalized=[normalize(t) for t in batch]
            ids={r['id'] for r in staged}
            if len({r['id'] for r in normalized})!=len(normalized) or any(r['id'] in ids for r in normalized):
                raise ValueError('Duplicate transaction page')
            staged+=normalized
            if len(staged)>MAX_RECORDS:raise ValueError('Payment scan too large')
            count=page.get('count')
            has_next=any(link.get('rel')=='next' for link in page.get('links') or [])
            if (type(count) is int and count>cursor+len(batch)) or has_next or (len(batch)==100 and count is None):
                if not batch:raise ValueError('Incomplete transaction page')
                cursor+=len(batch)
            else:cursor=-1
        else:
            known={r['id'] for r in staged}
            missing=next((r['parent_id'] for r in staged if r['action']=='REFUND' and r['parent_id'] and r['parent_id'] not in known),None)
            if missing:
                raw_parent=await client.signal_get('transactions',missing)
                if (raw_parent.get('context') or {}).get('businessId') not in {None,business}:
                    raise ValueError('Unexpected refund business')
                parent=normalize(raw_parent)
                if parent['id']!=missing:raise ValueError('Unexpected refund parent')
                if len(staged)>=MAX_RECORDS:raise ValueError('Payment scan too large')
                staged.append(parent)
            else:
                # No provider request was needed; the reserved slot is conservative.
                finished=True
        if cursor==-1:
            known={r['id'] for r in staged}
            finished=not any(r['action']=='REFUND' and r['parent_id'] and r['parent_id'] not in known for r in staged)
    except Deferred as exc:defer=exc.until
    except PoyntSignalAPIError as exc:
        problem='Payments could not load. Retrying shortly.'
        defer=utc_now()+timedelta(seconds=max(60,exc.retry_after))
        if exc.status_code==429:
            from live_dashboard_models import TransactionSignalBudget
            with factory() as session:
                if owns(session,org,business,token):
                    budget=session.get(TransactionSignalBudget,org)
                    if budget:budget.backoff_until=defer
                    session.commit()
    except Exception:problem='Payments could not be verified. Retrying shortly.'
    with factory() as session:
        if not owns(session,org,business,token):return
        row=session.get(AdditionalPaymentDay,row_id)
        if row is None or row.start_at!=start or row.end_at!=end:return
        row.error=problem
        current=utc_now()
        if problem:
            row.staged=[];row.cursor=0;row.phase='orders';row.seen_order_ids=[];row.scan_started_at=None
            row.next_sync_at=defer or current+timedelta(minutes=1)
        elif defer:row.next_sync_at=defer
        elif finished:
            row.snapshot=staged;row.staged=[];row.cursor=0;row.phase='orders';row.seen_order_ids=[];row.scan_started_at=None;row.loaded_at=current
            stores=session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id==org,
                OrganizationStore.is_active.is_(True))).all()
            live=False
            for store in stores:
                try:live|=row.report_date==current.astimezone(ZoneInfo(store.timezone_name or '')).date()
                except (ValueError,KeyError):pass
            row.next_sync_at=current+timedelta(minutes=15 if live else 1440)
        else:
            row.staged=staged;row.cursor=cursor;row.phase=phase;row.seen_order_ids=seen_orders;row.next_sync_at=current+timedelta(seconds=15)
        session.execute(delete(AdditionalPaymentDay).where(AdditionalPaymentDay.organization_id==org,
            AdditionalPaymentDay.end_at<current-timedelta(days=91)))
        session.commit()
    logger.info('ADDITIONAL_PAYMENTS sync organization=%s date_id=%s complete=%s error=%s',org,row_id,finished,bool(problem))
