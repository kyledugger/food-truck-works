"""Read-only trace of a POS customer-name field through orders/transactions/customers."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote
import httpx
from dotenv import load_dotenv
from check_poynt_products import CheckError, read_connection

BASE = 'https://services.poynt.net'


def key_name(value):
    return value.replace('_', '').lower()


def evidence(document, expected):
    """Keep names/references and test-string paths; never export card/contact objects."""
    names, references, matches = [], [], []
    def visit(value, path='$', customer=False):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = key_name(key)
                if normalized in {'card','cards','fundingsource','processorresponse','emvtags',
                        'email','emails','phone','phones','address','addresses','billingaddress','shippingaddress'}:
                    continue
                here = path + '.' + key
                customer_field = normalized in {'customer', 'customerinfo', 'customerdetails'}
                if normalized in {'customeruserid', 'customerid'} or (customer and normalized == 'id'):
                    if isinstance(child, (str, int)) and not isinstance(child, bool) and str(child).strip():
                        references.append({'path':here, 'id':str(child)})
                if normalized in {'customername', 'firstname', 'lastname', 'displayname', 'fullname'} or (customer and normalized == 'name'):
                    if isinstance(child, str):
                        names.append({'path':here, 'value':child})
                visit(child, here, customer or customer_field)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{path}[{index}]', customer)
        elif isinstance(value, str) and expected.casefold() in value.casefold():
            # Report the path, not arbitrary provider text containing customer data.
            matches.append(path)
    visit(document)
    return {'top_level_keys':list(document), 'name_fields':names,
            'customer_references':references, 'expected_name_found_at':matches}


def get(http, path, params=None):
    response = http.get(BASE + path, params=params)
    if not response.is_success:
        return None, {'http_status':response.status_code,
            'request_id':response.headers.get('Poynt-Request-Id') or response.headers.get('request-id')}
    try:
        data = response.json()
    except ValueError:
        raise CheckError('Provider returned invalid JSON; body omitted') from None
    if not isinstance(data, dict):
        raise CheckError('Unexpected provider response shape; body omitted')
    return data, {'http_status':response.status_code}


def recent_orders(http, business, hours):
    path = '/businesses/' + quote(business, safe='') + '/orders'
    now = datetime.now(timezone.utc)
    params = {'timeType':'createdAt', 'startAt':(now-timedelta(hours=hours)).isoformat(),
              'endAt':now.isoformat(), 'limit':1}
    first, status = get(http, path, params)
    if first is None:
        raise CheckError(f'Order list returned HTTP {status["http_status"]}')
    count = first.get('count')
    if not isinstance(count, int) or count < 0:
        raise CheckError('Order list has no valid count; use --order-id instead')
    if not count:
        return []
    page, status = get(http, path, {**params, 'limit':20, 'startOffset':max(0,count-20)})
    if page is None or not isinstance(page.get('orders'), list):
        raise CheckError('Could not read recent order list; use --order-id instead')
    return page['orders']


def trace(http, business, order_id, expected):
    prefix = '/businesses/' + quote(business, safe='')
    order, status = get(http, prefix+'/orders/'+quote(order_id,safe=''))
    if order is None:
        raise CheckError(f'Order fetch returned HTTP {status["http_status"]}')
    if str(order.get('id')) != order_id:
        raise CheckError('Order response ID did not match request')
    result = {'order_id':order_id, 'order_number':order.get('orderNumber'),
              'created_at':order.get('createdAt'), 'order':evidence(order,expected),
              'transactions':[], 'customers':[]}
    ids = {ref['id'] for ref in result['order']['customer_references']}
    transaction_ids = list(dict.fromkeys(str(t['id']) for t in order.get('transactions',[])
        if isinstance(t,dict) and t.get('id')))
    if len(transaction_ids)>10:
        raise CheckError('More than 10 transactions; diagnostic stopped to bound requests')
    for tid in transaction_ids:
        transaction, status = get(http,prefix+'/transactions/'+quote(tid,safe=''))
        item = {'id':tid, **status}
        if transaction is not None:
            item['evidence'] = evidence(transaction,expected)
            ids.update(ref['id'] for ref in item['evidence']['customer_references'])
        result['transactions'].append(item)
    if len(ids)>20:
        raise CheckError('More than 20 customer references; diagnostic stopped to bound requests')
    for cid in sorted(ids):
        customer,status = get(http,prefix+'/customers/'+quote(cid,safe=''))
        item = {'requested_customer_id':cid, **status}
        if customer is not None:
            # Root customer names may use name rather than customerName.
            item['evidence'] = evidence({'customer':customer},expected)
            item['created_at'] = customer.get('createdAt')
            item['updated_at'] = customer.get('updatedAt')
            item['returned_customer_id'] = customer.get('id')
        result['customers'].append(item)
    result['interpretation'] = ('A matching name supports this lookup path, but a current customer record does not prove when it was updated. '
        'HTTP 401/403 means access could not be tested; 404 may mean a different customer namespace. '
        'No match does not prove the POS discarded the name; Commerce may expose different data.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotenv',required=True)
    parser.add_argument('--organization-id',required=True,type=int)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--order-id')
    selection.add_argument('--order-number')
    parser.add_argument('--hours',type=int,default=24,choices=range(1,73),metavar='1..72')
    parser.add_argument('--expected-name',default='Kyle DuggerTest')
    parser.add_argument('--output',default='poynt_customer_trace.json')
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        if not Path(args.dotenv).is_file():
            raise CheckError('Environment file not found')
        if not args.expected_name.strip():
            raise CheckError('Expected name must not be empty')
        if (args.order_id or args.order_number) and Path(args.output).exists():
            raise CheckError('Output already exists; choose another --output filename')
        load_dotenv(args.dotenv,override=True)
        connection=read_connection(os.environ['DATABASE_URL'],args.organization_id)
        if not connection or not connection.get('access_token') or not connection.get('business_id'):
            raise CheckError('No saved Poynt connection')
        expiry=connection.get('expires_at')
        if isinstance(expiry,str):expiry=datetime.fromisoformat(expiry.replace('Z','+00:00'))
        if expiry and (expiry.tzinfo is None or expiry<=datetime.now(timezone.utc)):
            raise CheckError('Saved token expired. Let FTW refresh it, then rerun')
        with httpx.Client(timeout=30,follow_redirects=False,headers={'api-version':'1.2',
                'Authorization':f"{connection.get('token_type') or 'BEARER'} {connection['access_token']}"}) as http:
            oid=args.order_id
            if not oid:
                orders=recent_orders(http,connection['business_id'],args.hours)
                if not args.order_number:
                    print('Latest up to 20 orders in the selected window (no customer lookups):')
                    for order in orders:
                        print(json.dumps({k:order.get(k) for k in ('orderNumber','id','createdAt')}|
                            {'storeId':(order.get('context') or {}).get('storeId')}))
                    print('Rerun with --order-id ID (preferred), or --order-number NUMBER to trace the test order.')
                    return 0
                matching=[o for o in orders if str(o.get('orderNumber'))==args.order_number]
                if len(matching)!=1:
                    raise CheckError('Order number was not unique among the latest 20 orders. Use --order-id from the list or Orders report')
                oid=str(matching[0]['id'])
            report={'organization_id':args.organization_id,'business_id':connection['business_id'],
                'retrieved_at':datetime.now(timezone.utc).isoformat(),'expected_name':args.expected_name,
                **trace(http,connection['business_id'],oid,args.expected_name)}
        with Path(args.output).open('x',encoding='utf-8') as output:
            output.write(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print(f'Saved customer-name evidence to {args.output}. No database/provider writes performed.')
    except (CheckError,KeyError,ValueError,OSError,httpx.HTTPError) as error:
        print(str(error) if isinstance(error,CheckError) else f'Stopped: {type(error).__name__}. Credentials and provider bodies omitted.')
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
