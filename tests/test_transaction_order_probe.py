"""Bounded API timing experiment; mocked network and temporary local journals."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['DOTENV_FILE'] = '/nonexistent-ftw-probe-test-env'
import httpx
import transaction_order_probe as probe


def event(**changes):
    return {'id':'event', 'businessId':'business', 'hookId':'hook', 'resourceId':'tx',
            'storeId':'store', 'eventType':'TRANSACTION_CAPTURED',
            'createdAt':datetime.now(timezone.utc).isoformat(), **changes}


def order(eligible=True, **changes):
    return {'id':'order', 'orderNumber':'42', 'context':{'storeId':'store'},
            'createdAt':'2026-11-16T17:28:04Z', 'updatedAt':'2026-11-16T17:28:04Z',
            'statuses':{'status':'COMPLETED' if eligible else 'OPENED',
                        'transactionStatusSummary':'COMPLETED' if eligible else 'NONE'},
            'transactions':[{'id':'tx'}], 'items':[{'quantity':1}], **changes}


async def no_sleep(delay): pass


class LookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.creds = SimpleNamespace(business_id='business', access_token='PRIVATE_TOKEN',
            token_type='BEARER', expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
        self.calls = []

    async def series(self, handler):
        def record(request):
            self.calls.append(request)
            return handler(request)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(record), **kwargs)
        with patch.object(probe, 'emit') as logs:
            await probe.lookup_series(event(), 1, datetime.now(timezone.utc), 'test', 'store',
                credentials=self.creds, sleep=no_sleep, http_factory=factory)
        return [(c.args[0], c.kwargs) for c in logs.call_args_list]

    async def test_direct_reference_readable_before_eligible(self):
        reads = 0
        def handler(request):
            nonlocal reads
            if '/transactions/' in request.url.path:
                return httpx.Response(200,json={'id':'tx', 'references':[{'type':'POYNT_ORDER','id':'order'}]})
            reads += 1
            return httpx.Response(200,json=order(eligible=reads>=2))
        logs = await self.series(handler)
        self.assertEqual(len(self.calls),3)
        attempts = [data for name,data in logs if name=='order_lookup_attempt']
        self.assertFalse(attempts[0]['order']['eligible_order_content'])
        self.assertTrue(attempts[1]['order']['eligible_order_content'])
        self.assertEqual(logs[-1][1]['outcome'],'eligible_order_observed')

    async def test_fallback_count_only_then_matching_order(self):
        reads = 0
        def handler(request):
            nonlocal reads
            if '/transactions/' in request.url.path:
                return httpx.Response(200,json={'id':'tx','references':[]})
            reads += 1
            self.assertEqual(request.url.params['storeId'],'store')
            self.assertEqual(request.url.params['limit'],'100')
            self.assertIn('+00:00',request.url.params['startAt'])
            return httpx.Response(200,json={'count':0} if reads==1 else {'count':1,'orders':[order()]})
        logs = await self.series(handler)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(logs[-1][1]['outcome'],'eligible_order_observed')

    async def test_exact_request_budget_when_no_match(self):
        def handler(request):
            return httpx.Response(200,json={'id':'tx'} if '/transactions/' in request.url.path else {'count':0})
        logs = await self.series(handler)
        self.assertEqual(len(self.calls),12)
        self.assertEqual(logs[-1][1]['outcome'],'not_observed_within_probe')

    async def test_rate_limit_stops_without_retry(self):
        logs = await self.series(lambda request:httpx.Response(429,headers={'Retry-After':'60'}))
        self.assertEqual(len(self.calls),1)
        self.assertTrue(any(name=='lookup_http_error' and data['http_status']==429 for name,data in logs))
        self.assertEqual(logs[-1][1]['outcome'],'stopped_on_error')

    async def test_expired_saved_token_makes_no_requests(self):
        self.creds.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        logs=await self.series(lambda request: self.fail('No request expected'))
        self.assertEqual(self.calls,[])
        self.assertEqual(logs[-1][1]['reason'],'saved_token_expired')

    async def test_wrong_direct_reference_response_is_rejected(self):
        def handler(request):
            if '/transactions/' in request.url.path:
                return httpx.Response(200,json={'id':'tx','references':[{'type':'POYNT_ORDER','id':'expected'}]})
            return httpx.Response(200,json=order())
        logs=await self.series(handler)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(logs[-1][1]['outcome'],'stopped_on_error')

    async def test_other_store_or_transaction_cannot_match(self):
        self.assertFalse(probe.matching_order(order(context={'storeId':'other'}),'tx','store'))
        self.assertFalse(probe.matching_order(order(transactions=[{'id':'other'}]),'tx','store'))

    async def test_truncation_is_logged_and_not_called_absence(self):
        def handler(request):
            return httpx.Response(200,json={'id':'tx'} if '/transactions/' in request.url.path
                else {'count':101,'orders':[order(id=str(i),transactions=[]) for i in range(100)]})
        logs = await self.series(handler)
        self.assertTrue(all(d['search_truncated'] for n,d in logs if n=='order_lookup_attempt'))
        self.assertEqual(logs[-1][1]['outcome'],'not_observed_within_probe')

    async def test_sensitive_fields_are_not_logged(self):
        def handler(request):
            if '/transactions/' in request.url.path:
                return httpx.Response(200,json={'id':'tx','references':[{'type':'POYNT_ORDER','id':'order'}]})
            return httpx.Response(200,json=order(customer={'email':'PRIVATE_CUSTOMER'},
                                                fundingSource={'card':'PRIVATE_CARD'}))
        logs = await self.series(handler)
        self.assertNotIn('PRIVATE_',json.dumps(logs))

    async def test_disabled_wrong_store_updated_and_stale_do_not_lookup(self):
        env={'POYNT_TRANSACTION_LOOKUP_ENABLED':'1', 'POYNT_TRANSACTION_LOOKUP_RUN_ID':'test',
             'POYNT_TRANSACTION_LOOKUP_STORE_ID':'store'}
        with patch.dict(os.environ,env), patch.object(probe,'lookup_series',new_callable=AsyncMock) as look, \
             patch.object(probe,'reserve') as reserve:
            now=datetime.now(timezone.utc)
            await probe.run_probe(event(storeId='other'),1,now)
            await probe.run_probe(event(eventType='TRANSACTION_UPDATED'),1,now)
            await probe.run_probe(event(createdAt=(now-timedelta(minutes=5)).isoformat()),1,now)
            reserve.assert_not_called();look.assert_not_called()


class BudgetTests(unittest.TestCase):
    def test_cross_worker_reservation_deduplicates_and_caps_two(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal.sqlite3'
            # Initialize before concurrent calls, then exercise SQLite's lock.
            self.assertEqual(probe.reserve(event(),1,'test',path),'reserved')
            with ThreadPoolExecutor(max_workers=4) as executor:
                results=list(executor.map(lambda _:probe.reserve(event(),1,'test',path),range(4)))
            self.assertEqual(results,['already_reserved']*4)
            self.assertEqual(probe.reserve(event(resourceId='second'),1,'test',path),'reserved')
            self.assertEqual(probe.reserve(event(resourceId='third'),1,'test',path),'run_budget_exhausted')
            self.assertEqual(probe.reserve(event(),1,'another-run',path),'reserved')


if __name__=='__main__': unittest.main()
