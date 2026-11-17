"""Read-only invoice investigation. No app startup, token refresh or DB writes."""
import argparse
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


def timestamp(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError('Invalid timestamp')
    return value.astimezone(timezone.utc)


def safe_record(raw, cached=False):
    """Return a fixed allowlist; exclude customer/card/contact/notes/processor data."""
    if cached:
        record={key:raw.get(key) for key in ('id','created_at','updated_at','store_id','parent_id',
            'action','status','voided','currency','amounts','order_ids','order_linked','paylink','invoice')}
        record['amounts']={key:(raw.get('amounts') or {}).get(key) for key in ('orderAmount','transactionAmount','tipAmount')}
        return record
    context=raw.get('context') or {};amounts=raw.get('amounts') or {};refs=raw.get('references') or []
    return {'id':raw.get('id'),'created_at':raw.get('createdAt'),'updated_at':raw.get('updatedAt'),
        'store_id':context.get('storeId'),'parent_id':raw.get('parentId'),'action':raw.get('action'),
        'status':raw.get('status'),'voided':raw.get('voided'),'currency':amounts.get('currency'),
        'amounts':{k:amounts.get(k) for k in ('orderAmount','transactionAmount','tipAmount')},
        'order_ids':[r.get('id') for r in refs if r.get('type')=='POYNT_ORDER'],
        'order_linked':any(r.get('type')=='POYNT_ORDER' for r in refs),
        'paylink':any(r.get('customType')=='checkoutUrlId' for r in refs),
        'invoice':any(str(r.get('customType','')).lower() in {'invoiceid','invoice_id'} for r in refs)}


def explain(record,records,links,stores,day):
    chain=[];seen=set();current=record
    while current and current.get('id') not in seen:
        seen.add(current.get('id'));chain.append(current);current=records.get(current.get('parent_id'))
    assigned=record.get('store_id') or next((r.get('store_id') for r in chain if r.get('store_id')),None)
    reasons=[];store=next((s for s in stores if s['store_id']==assigned),None)
    if not assigned:reasons.append('no_store_assignment')
    elif not store:reasons.append('store_not_configured_in_this_FTW_business')
    elif not store['is_active']:reasons.append('store_inactive_in_FTW')
    if store:
        try:
            local=timestamp(record['created_at']).astimezone(ZoneInfo(store['timezone_name']))
            if local.date()!=day:reasons.append('payment_timestamp_is_outside_selected_store_day')
        except (ValueError,TypeError,KeyError):reasons.append('missing_or_invalid_timestamp_or_store_timezone')
    if any(r.get('store_id') and r['store_id']!=assigned for r in chain):reasons.append('parent_store_conflict')
    if any(r.get('order_linked') or r.get('order_ids') or r.get('id') in links for r in chain):reasons.append('already_linked_to_an_order')
    if any(r.get('voided') for r in chain):reasons.append('voided_payment')
    refund=record.get('action')=='REFUND' and record.get('status')=='REFUNDED'
    sale=record.get('action') in {'SALE','CAPTURE'} and record.get('status') in {'CAPTURED','REFUNDED','PARTIALLY_REFUNDED'}
    if not sale and not refund:reasons.append('action_or_status_not_supported_as_a_paid_payment')
    if record.get('currency')!='USD':reasons.append('not_USD_or_missing_currency')
    a=record.get('amounts') or {};base=a.get('orderAmount');total=a.get('transactionAmount');tip=a.get('tipAmount') or 0
    try:
        if any(type(v) is not int for v in (base,total,tip) if v is not None):raise ValueError()
        if base is None and total is not None:base=abs(total)-abs(tip)
        if base is None or (total is not None and abs(total)!=abs(base)+abs(tip)):raise ValueError()
        if sale and any(v is not None and v<0 for v in (base,total,tip)):raise ValueError()
    except (TypeError,ValueError):reasons.append('amounts_missing_invalid_or_do_not_balance')
    if refund and not any(r.get('action') in {'SALE','CAPTURE'} for r in chain[1:]):reasons.append('original_refunded_payment_not_available')
    if any(r.get('parent_id')==record.get('id') and r.get('action')=='CAPTURE' and r.get('status')=='CAPTURED' and not r.get('voided') for r in records.values()):
        reasons.append('superseded_by_capture')
    return {'payment':record,'assigned_store_name':(store.get('display_name') or store.get('poynt_name')) if store else None,
        'possible_exclusion_reasons':reasons,'linked_order_ids':links.get(record.get('id'),[])}


def fetch(http,business,start,end,max_pages):
    records=[];seen=set()
    for offset in range(0,max_pages*100,100):
        response=http.get('/businesses/'+quote(business,safe='')+'/transactions',params={
            'timeType':'createdAt','startAt':start.isoformat(),'endAt':end.isoformat(),'limit':100,'startOffset':offset})
        if not response.is_success:return {'http_status':response.status_code,'complete':False,'records':records}
        data=response.json();batch=data.get('transactions',[] if data.get('count')==0 else None)
        if not isinstance(batch,list):return {'http_status':200,'complete':False,'error':'unexpected_response_shape','records':records}
        ids=[t.get('id') for t in batch]
        if len(set(ids))!=len(ids) or set(ids)&seen:return {'http_status':200,'complete':False,'error':'duplicate_page','records':records}
        seen.update(ids);records.extend(safe_record(t) for t in batch)
        count=data.get('count')
        if not batch or (type(count) is int and offset+len(batch)>=count) or (count is None and len(batch)<100):
            return {'http_status':200,'complete':True,'records':records}
    return {'http_status':200,'complete':False,'error':'page_limit','records':records}


def run(args):
    output=Path(args.output)
    if output.exists():raise ValueError('Output exists; choose a new --output filename.')
    if not Path(args.dotenv).is_file():raise ValueError('Selected environment file does not exist.')
    load_dotenv(args.dotenv,override=True)
    url=os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    days=[date.fromisoformat(d) for d in args.dates]
    with psycopg.connect(url,row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        connection=db.execute('SELECT business_id,access_token,token_type,expires_at FROM poynt_connections WHERE organization_id=%s',
            (args.organization_id,)).fetchone()
        if not connection:raise ValueError('No Poynt connection for this FTW business.')
        business=connection['business_id']
        stores=db.execute('SELECT store_id,display_name,poynt_name,timezone_name,is_active FROM organization_stores WHERE organization_id=%s',
            (args.organization_id,)).fetchall()
        cached=db.execute('SELECT report_date,start_at,end_at,requested_at,next_sync_at,loaded_at,phase,cursor,error,staged,snapshot '
            'FROM additional_payment_days WHERE organization_id=%s AND business_id=%s AND report_date=ANY(%s)',
            (args.organization_id,business,days)).fetchall()
        control=db.execute('SELECT next_request_at FROM additional_payment_controls WHERE organization_id=%s AND business_id=%s',
            (args.organization_id,business)).fetchone()
        budget=db.execute('SELECT backoff_until,request_count,window_started_at FROM transaction_signal_budgets WHERE organization_id=%s AND business_id=%s',
            (args.organization_id,business)).fetchone()
        report={'organization_id':args.organization_id,'business_id':business,'stores':stores,
            'collection_control':control,'shared_lookup_budget':budget,'days':[]}
        now=datetime.now(timezone.utc)
        expires=connection['expires_at']
        provider_available=not args.cache_only and expires is not None and timestamp(expires)>now
        if budget and budget.get('backoff_until') and timestamp(budget['backoff_until'])>now:provider_available=False
        client=httpx.Client(base_url='https://services.poynt.net',timeout=15,follow_redirects=False,headers={
            'Accept':'application/json','api-version':'1.2',
            'Authorization':(connection['token_type'] or 'BEARER')+' '+connection['access_token']}) if provider_available else nullcontext(None)
        with client as http:
            for day in days:
                row=next((r for r in cached if r['report_date']==day),None)
                bounds=[]
                for store in stores:
                    if not store['is_active']:continue
                    try:
                        zone=ZoneInfo(store['timezone_name']);start=datetime.combine(day,datetime.min.time(),zone).astimezone(timezone.utc)
                        end=datetime.combine(day+timedelta(days=1),datetime.min.time(),zone).astimezone(timezone.utc)
                        bounds.append((start,end))
                    except (ValueError,TypeError,KeyError):pass
                if not bounds:raise ValueError('No configured active store timezones.')
                start,end=min(b[0] for b in bounds),max(b[1] for b in bounds)
                provider=fetch(http,business,start,end,args.max_pages) if provider_available else {
                    'complete':False,'records':[],'skipped':'cache_only_or_saved_token_expired_or_prior_rate_limit'}
                if provider.get('http_status')==429:provider_available=False
                cached_records=[safe_record(r,True) for r in (row['snapshot'] if row else [])]
                staged_records=[safe_record(r,True) for r in (row['staged'] if row else [])]
                all_records={r['id']:r for r in cached_records+staged_records+provider['records']}
                linked=db.execute('SELECT transaction_id,order_id FROM dashboard_payment_links WHERE organization_id=%s AND business_id=%s AND transaction_id=ANY(%s)',
                    (args.organization_id,business,list(all_records))).fetchall()
                links={}
                for link in linked:links.setdefault(link['transaction_id'],[]).append(link['order_id'])
                describe=lambda records:[explain(r,all_records,links,stores,day) for r in records]
                entry={'date':day.isoformat(),'query_start_at':start,'query_end_at':end,
                    'cache_state':{k:row[k] for k in ('requested_at','next_sync_at','loaded_at','phase','cursor','error')} if row else 'not_queued',
                    'cached_payments':describe(cached_records),'staged_payments':describe(staged_records),
                    'provider':{**provider,'records':describe(provider['records'])}}
                report['days'].append(entry)
                print(day.isoformat()+': cache '+('not queued' if not row else 'loaded' if row['loaded_at'] else 'pending')+
                    '; provider HTTP '+str(provider.get('http_status','skipped'))+'; '+str(len(provider['records']))+' transaction(s).')
    output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    print('Saved '+output.name+'. Read-only database and GET requests only. No migration or redeploy needed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotenv',required=True)
    parser.add_argument('--organization-id',type=int,default=1)
    parser.add_argument('--dates',nargs='+',default=['2026-09-03','2026-09-09'])
    parser.add_argument('--max-pages',type=int,choices=range(1,6),default=3)
    parser.add_argument('--cache-only',action='store_true')
    parser.add_argument('--output',default='invoice_payment_diagnostic.json')
    try:run(parser.parse_args())
    except Exception as exc:
        print(str(exc) if type(exc) is ValueError else 'Stopped: '+type(exc).__name__+'. Sensitive details omitted.')
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
