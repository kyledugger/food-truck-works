"""Isolated SQLite/mocked API checks; never use live credentials or databases."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import os
import tempfile
import unittest
from zoneinfo import ZoneInfo
from sqlalchemy import select, create_engine, inspect, text
from test_live_dashboard import DatabaseAndRoutesTests as Fixture, sale
from models import OrganizationStore, OrganizationMember
from live_dashboard_models import AdditionalPaymentDay, AdditionalPaymentControl, DashboardPaymentLink, DashboardSync, TransactionSignalBudget
from live_dashboard_service import acquire, owned_state, save_order
from poynt.client import PoyntSignalAPIError
from store_time import utc_now, local_day_bounds
import additional_payments as payments


def transaction(at, identifier='paylink', amount=36754, tip=0, **changes):
    result={'id':identifier,'createdAt':at.isoformat(),'updatedAt':at.isoformat(),
        'action':'SALE','status':'CAPTURED','context':{'storeId':'truck'},
        'references':[{'type':'CUSTOM','customType':'checkoutUrlId','id':'checkout'}],
        'amounts':{'currency':'USD','orderAmount':amount,'tipAmount':tip,'transactionAmount':amount+tip}}
    result.update(changes)
    return result


class PaymentTests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        self.now=utc_now();self.zone=ZoneInfo('America/Phoenix');self.day=self.now.astimezone(self.zone).date()
        self.start,self.end=local_day_bounds(self.day,self.zone)
        self.claim=acquire(1)
        self.client.get('/test-login')

    def tearDown(self):Fixture.tearDown(self)

    def queue(self,records=(),loaded=True,day=None):
        with self.factory() as session:
            stores=session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id==1)).all()
            row=payments.request_day(session,1,'business',day or self.day,stores,self.now)
            if loaded:row.loaded_at=self.now;row.snapshot=[payments.normalize(r) for r in records]
            session.commit();return row.id

    def summary(self,row_id,order_sales=10000,store_id=1):
        with self.factory() as session:
            row=session.get(AdditionalPaymentDay,row_id);store=session.get(OrganizationStore,store_id)
            start,end=local_day_bounds(row.report_date,ZoneInfo(store.timezone_name))
            return payments.summarize(session,1,'business',store,start,end,row,order_sales,self.now)

    def run_page(self,api):
        client=SimpleNamespace(signal_get=AsyncMock(side_effect=api))
        asyncio.run(payments.process(self.factory,1,self.claim[0],self.claim[1],client,owned_state))
        return client

    def due(self,row_id):
        with self.factory() as session:
            session.get(AdditionalPaymentDay,row_id).next_sync_at=utc_now()-timedelta(seconds=1)
            control=session.get(AdditionalPaymentControl,1)
            if control:control.next_request_at=utc_now()-timedelta(seconds=1)
            session.commit()

    def test_known_paylink_and_invoice_with_individual_tips(self):
        row=self.queue([transaction(self.now),transaction(self.now,'invoice',24311,1200,references=[],
            context={'storeId':'truck','source':'INSTORE'})])
        summary=self.summary(row)
        self.assertEqual(summary['sales_cents'],61065)
        self.assertEqual(summary['combined_sales_cents'],71065)
        self.assertEqual([p['tip_cents'] for p in summary['payments']],[1200,0]) # sorted by stable ID
        self.assertNotIn('tips_cents',summary)
        self.assertEqual({p['label'] for p in summary['payments']},{'Pay link','Additional payment'})

    def test_exact_links_exclude_orders_on_any_day_without_amount_matching(self):
        raw=transaction(self.now,'linked')
        row=self.queue([raw,transaction(self.now,'referenced',references=[{'type':'POYNT_ORDER','id':'older-order'}]),
            transaction(self.now,'different-payment-same-amount')])
        with self.factory() as session:
            old=sale(self.now-timedelta(days=7),'old-order')
            old['transactions']=[{'id':'linked'}]
            save_order(session,1,'business',old);session.commit()
        summary=self.summary(row)
        self.assertEqual([p['id'] for p in summary['payments']],['different-payment-same-amount'])

    def test_refund_on_its_own_day_and_voids_pending_currency(self):
        original=transaction(self.now-timedelta(days=5),'original')
        refund=transaction(self.now,'refund',1000,100,action='REFUND',status='REFUNDED',parentId='original',references=[])
        row=self.queue([original,refund,transaction(self.now,'void',voided=True),
            transaction(self.now,'pending',status='AUTHORIZED'),
            transaction(self.now,'euro',amounts={'currency':'EUR','orderAmount':100,'transactionAmount':100,'tipAmount':0})])
        summary=self.summary(row)
        self.assertEqual(summary['sales_cents'],-1000)
        self.assertEqual(summary['payments'][0]['tip_cents'],-100)
        self.assertTrue(summary['currency_warning'])
        self.assertEqual(summary['combined_sales_cents'],9000)

    def test_missing_parent_and_amount_mismatch_are_excluded(self):
        row=self.queue([transaction(self.now,'refund',action='REFUND',status='REFUNDED',parentId='missing'),
            transaction(self.now,'bad',amounts={'currency':'USD','orderAmount':10,'transactionAmount':25,'tipAmount':5}),
            transaction(self.now,'negative',amounts={'currency':'USD','orderAmount':-10,'transactionAmount':-10,'tipAmount':0})])
        self.assertEqual(self.summary(row)['review_count'],3)
        self.assertEqual(self.summary(row)['sales_cents'],0)

    def test_no_funding_card_customer_or_free_text_is_cached(self):
        normalized=payments.normalize(transaction(self.now,customer={'name':'PRIVATE'},fundingSource={'card':'PRIVATE'},
            notes='PRIVATE',receiptEmailAddress='PRIVATE',processorResponse={'approvalCode':'PRIVATE'}))
        self.assertNotIn('PRIVATE',str(normalized))

    def test_local_midnight_and_timezone_union_including_dst(self):
        with self.factory() as session:
            session.add(OrganizationStore(organization_id=1,store_id='ny',poynt_name='New York',timezone_name='America/New_York'))
            session.commit()
        for day,hours in [(date(2026,3,8),23),(date(2026,11,1),25)]:
            start,end=local_day_bounds(day,ZoneInfo('America/New_York'))
            self.assertEqual((end-start).total_seconds(),hours*3600)
            row=self.queue([transaction(start-timedelta(microseconds=1),'before'),transaction(start,'inside'),transaction(end,'after')],day=day)
            with self.factory() as session:
                store=session.scalar(select(OrganizationStore).where(OrganizationStore.store_id=='ny'))
                cached=session.get(AdditionalPaymentDay,row)
                self.assertLessEqual(cached.start_at,start);self.assertGreaterEqual(cached.end_at,end)
                # Same instant can belong to different store-local dates.
                for record in cached.snapshot:record['store_id']='ny'
                summary=payments.summarize(session,1,'business',store,start,end,cached,0,self.now)
                self.assertEqual([p['id'] for p in summary['payments']],['inside'])

    def test_resumable_collection_only_publishes_after_complete(self):
        row=self.queue([transaction(self.now,'saved',100)],loaded=True)
        client=self.run_page(lambda *a,**kw:{'count':0})
        self.assertEqual(client.signal_get.call_args.args[0],'orders')
        self.assertEqual(self.summary(row)['sales_cents'],100)
        self.due(row)
        page=[transaction(self.now,'p'+str(i),1) for i in range(100)]
        self.run_page(lambda *a,**kw:{'count':101,'transactions':page})
        self.assertEqual(self.summary(row)['sales_cents'],100)
        self.due(row)
        self.run_page(lambda *a,**kw:{'count':101,'transactions':[transaction(self.now,'last',7)]})
        summary=self.summary(row)
        self.assertEqual(summary['sales_cents'],107)
        self.assertEqual(len(summary['payments']),101)
        with self.factory() as session:
            cached=session.get(AdditionalPaymentDay,row)
            self.assertGreaterEqual(cached.next_sync_at-cached.loaded_at,timedelta(minutes=15))

    def test_throttle_and_429_preserve_saved_snapshot(self):
        row=self.queue([transaction(self.now,'saved',100)])
        self.run_page(lambda *a,**kw:{'count':0})
        client=self.run_page(lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Must throttle')))
        client.signal_get.assert_not_awaited()
        self.due(row)
        self.run_page(lambda *a,**kw:(_ for _ in ()).throw(PoyntSignalAPIError(429,137)))
        summary=self.summary(row)
        self.assertEqual(summary['sales_cents'],100)
        self.assertTrue(summary['error'])
        with self.factory() as session:
            self.assertGreater(session.get(TransactionSignalBudget,1).backoff_until,utc_now()+timedelta(seconds=130))

    def test_refund_parent_lookup_is_bounded_and_persisted(self):
        row=self.queue(loaded=False)
        self.run_page(lambda *a,**kw:{'count':0});self.due(row)
        refund=transaction(self.now,'refund',500,50,action='REFUND',status='REFUNDED',parentId='original',references=[])
        self.run_page(lambda *a,**kw:{'transactions':[refund],'count':1});self.due(row)
        self.assertTrue(self.summary(row)['loading'])
        client=self.run_page(lambda *a,**kw:transaction(self.now-timedelta(days=1),'original',1000,100))
        self.assertEqual(client.signal_get.call_args.args,('transactions','original'))
        self.assertEqual(self.summary(row)['sales_cents'],-500)

    def test_dashboard_preserves_order_metric_and_shows_additional_total(self):
        self.queue([transaction(self.now)])
        with self.factory() as session:
            save_order(session,1,'business',sale(self.now,amount=1000))
            state=session.get(DashboardSync,1);state.last_reconciled_at=self.now
            session.commit()
        response=self.client.get('/dashboard/data')
        self.assertEqual(response.status_code,200,response.text)
        store=response.json()['stores'][0]
        self.assertEqual(store['sales_cents'],1000)
        self.assertEqual(store['combined_sales_cents'],37754)
        self.assertEqual(store['additional_payments']['sales_cents'],36754)
        with self.factory() as session:
            member=session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1))
            member.role='member';session.commit()
        self.assertEqual(self.client.get('/dashboard/data').status_code,403)

    def test_collection_indexes_orders_before_counting_payments(self):
        row=self.queue(loaded=False)
        raw=sale(self.now);raw['transactions']=[{'id':'linked'}]
        self.run_page(lambda *a,**kw:{'count':1,'orders':[raw]});self.due(row)
        self.run_page(lambda *a,**kw:{'count':1,'transactions':[transaction(self.now,'linked')]})
        self.assertEqual(self.summary(row)['sales_cents'],0)

    def test_shared_backoff_applies_when_kitchen_triggers_are_disabled(self):
        from live_dashboard_service import process_organization
        until=utc_now()+timedelta(minutes=2)
        with self.factory() as session:
            session.add(TransactionSignalBudget(organization_id=1,business_id='business',
                window_started_at=utc_now(),request_count=1,backoff_until=until))
            session.commit()
        with patch.dict(os.environ,{'POYNT_TRANSACTION_TRIGGER_ENABLED':'0'}),patch(
                'poynt.client.PoyntClient.get_recent_orders',new=AsyncMock()) as orders:
            asyncio.run(process_organization(1,self.claim[0],self.claim[1]))
            orders.assert_not_awaited()

    def test_repeated_pages_never_replace_saved_payments(self):
        row=self.queue([transaction(self.now,'saved',500)])
        self.run_page(lambda *a,**kw:{'count':0});self.due(row)
        batch=[transaction(self.now,'p'+str(i),1) for i in range(100)]
        self.run_page(lambda *a,**kw:{'count':200,'transactions':batch});self.due(row)
        self.run_page(lambda *a,**kw:{'count':200,'transactions':batch})
        summary=self.summary(row)
        self.assertEqual(summary['sales_cents'],500)
        self.assertTrue(summary['error'])

    def test_foreign_tenant_and_order_payments_do_not_leak_into_totals(self):
        row=self.queue([transaction(self.now,'terminal',references=[{'type':'POYNT_ORDER','id':'terminal-order'}],context={'storeId':'truck','source':'INSTORE'}),
            transaction(self.now,'other-store',context={'storeId':'foreign-store'})])
        self.assertEqual(self.summary(row)['sales_cents'],0)
        self.due(row);self.run_page(lambda *a,**kw:{'count':0});self.due(row)
        self.run_page(lambda *a,**kw:{'count':1,'transactions':[transaction(self.now,
            context={'storeId':'truck','businessId':'foreign-business'})]})
        self.assertTrue(self.summary(row)['error'])


class MigrationTests(unittest.TestCase):
    def test_preflight_and_actual_local_upgrade(self):
        from apply_additional_payments_migration import run
        with tempfile.TemporaryDirectory() as directory:
            url='sqlite:///'+str(Path(directory)/'migration.sqlite');engine=create_engine(url)
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE organizations (id INTEGER PRIMARY KEY)'))
                connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
                connection.execute(text("INSERT INTO alembic_version VALUES ('b13d82e69a04')"))
            env=Path(directory)/'migration.env';env.write_text('DATABASE_URL='+url+'\n')
            with patch.dict(os.environ,{'DATABASE_URL':url}):
                run(SimpleNamespace(dotenv=str(env),apply=False))
                self.assertNotIn('additional_payment_days',inspect(engine).get_table_names())
                run(SimpleNamespace(dotenv=str(env),apply=True))
            self.assertIn('additional_payment_days',inspect(engine).get_table_names())
            engine.dispose()


if __name__=='__main__':unittest.main()
