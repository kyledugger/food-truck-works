"""SQLite integration tests; no live DB, POS, or network."""
import asyncio
from datetime import timedelta, datetime, timezone
import importlib.util
import json
import os
import base64
import hashlib
import hmac
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock

from sqlalchemy import select, func, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_live_dashboard import DatabaseAndRoutesTests, sale
from live_dashboard_models import TransactionSignal, TransactionSignalReceipt, TransactionSignalBudget, DashboardSync, DashboardOrder
from live_dashboard_service import acquire, release, owned_state, save_order, kitchen_sync_deadline, process_organization
from kitchen_models import KitchenTicket, KitchenSession
from poynt.client import PoyntSignalAPIError
from store_time import utc_now
import transaction_trigger as trigger


class TriggerTests(unittest.TestCase):
    def setUp(self):
        DatabaseAndRoutesTests.setUp(self)
        self.extra_env=patch.dict(os.environ,{'POYNT_TRANSACTION_TRIGGER_ENABLED':'1',
            'POYNT_TRANSACTION_TRIGGER_STORE_IDS':'truck','POYNT_TRANSACTION_TRIGGER_REQUESTS_PER_MINUTE':'30'})
        self.extra_env.start()
        self.now=utc_now()
        self.client.get('/test-login')
        self.kpatch=patch('routers.kitchen.SessionLocal',self.factory);self.kpatch.start()
        self.claim=acquire(1)

    def tearDown(self):
        self.kpatch.stop();self.extra_env.stop()
        DatabaseAndRoutesTests.tearDown(self)

    def event(self, **changes):
        return {'id':'event1','resourceId':'tx','businessId':'business','storeId':'truck',
            'eventType':'TRANSACTION_CAPTURED','createdAt':self.now.isoformat(),**changes}

    def enqueue(self, **changes):
        with self.factory() as session:
            result=trigger.enqueue(session,1,self.event(**changes),self.now)
            session.commit()
        return result

    def run_jobs(self, api):
        client=type('Client',(),{'signal_get':AsyncMock(side_effect=api)})()
        result=asyncio.run(trigger.process_signals(self.factory,1,self.claim[0],self.claim[1],
            client,owned_state,save_order))
        return client,result

    def test_direct_order_creates_real_ticket_and_dedupes_retries(self):
        self.assertEqual(self.enqueue(),'queued')
        self.assertEqual(self.enqueue(),'duplicate_delivery')
        payload=sale(self.now,'order',store='truck');payload['transactions']=[{'id':'tx'}]
        def api(resource,identifier=None,params=None):
            return {'id':'tx','references':[{'type':'POYNT_ORDER','id':'order'}]} if resource=='transactions' else payload
        client,_=self.run_jobs(api)
        self.assertEqual(client.signal_get.await_count,2)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'done')
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)
            self.assertEqual(session.scalar(select(DashboardOrder)).order_id,'order')
            self.assertEqual(session.get(TransactionSignalBudget,1).request_count,2)
        # No-op repeat workers cannot refetch completed work.
        self.run_jobs(api)
        self.assertEqual(self.enqueue(),'duplicate_delivery')

    def test_cash_waits_then_matches_reference(self):
        self.enqueue()
        self.run_jobs(lambda resource,identifier=None,params=None:{'id':'tx'} if resource=='transactions' else {'count':0})
        with self.factory() as session:
            job=session.scalar(select(TransactionSignal));self.assertEqual(job.attempts,1)
            self.assertEqual(job.state,'pending');job.next_attempt_at=self.now-timedelta(seconds=1);session.commit()
        payload=sale(self.now,'cash');payload['transactions']=[{'id':'tx'}]
        self.run_jobs(lambda resource,identifier=None,params=None:{'id':'tx','references':[{'type':'POYNT_ORDER','id':'cash'}]} if resource=='transactions' else payload)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'done')
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)

    def test_updated_event_coalesces_and_known_reference_uses_one_call(self):
        self.enqueue();self.enqueue(id='updated',eventType='TRANSACTION_UPDATED')
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(TransactionSignal.id))),1)
            self.assertEqual(session.scalar(select(func.count(TransactionSignalReceipt.id))),2)
            job=session.scalar(select(TransactionSignal));job.order_id='order';session.commit()
        client,_=self.run_jobs(lambda *a,**kw:sale(self.now,'order'))
        self.assertEqual(client.signal_get.await_count,1)

    def test_request_budget_defers_without_network(self):
        self.enqueue()
        with self.factory() as session:
            session.add(TransactionSignalBudget(organization_id=1,business_id='business',
                window_started_at=self.now,request_count=30));session.commit()
        client,_=self.run_jobs(lambda *a,**kw:self.fail('Network must not be called'))
        client.signal_get.assert_not_awaited()
        with self.factory() as session:
            job=session.scalar(select(TransactionSignal));self.assertEqual(job.attempts,0)
            self.assertGreater(job.next_attempt_at,self.now+timedelta(seconds=50))

    def test_429_persists_backoff_and_blocks_full_day_polling(self):
        self.enqueue()
        def api(*a,**kw):raise PoyntSignalAPIError(429,120)
        client,blocked=self.run_jobs(api)
        self.assertTrue(blocked);self.assertEqual(client.signal_get.await_count,1)
        with self.factory() as session:
            state=session.get(DashboardSync,1)
            self.assertGreater(state.next_reconcile_at,self.now+timedelta(seconds=110))
            self.assertIsNotNone(state.error)
            kitchen_sync_deadline(session,1,'business',self.now);session.commit()
            self.assertGreater(session.get(DashboardSync,1).next_reconcile_at,self.now+timedelta(seconds=110))
        with patch('live_dashboard_service.PoyntClient') as cls:
            asyncio.run(process_organization(1,'business',self.claim[1]))
            cls.return_value.get_recent_orders.assert_not_called()

    def test_wrong_store_and_online_paylink_never_create_ticket(self):
        self.assertEqual(self.enqueue(storeId='other'),'not_enabled_for_store')
        self.enqueue()
        client,_=self.run_jobs(lambda *a,**kw:{'id':'tx','references':[{'customType':'checkoutUrlId','id':'link'}]})
        self.assertEqual(client.signal_get.await_count,1)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'ignored')
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),0)

    def test_state_is_durable_and_worker_requires_lease(self):
        self.enqueue()
        release(1,self.claim[1])
        client,_=self.run_jobs(lambda *a,**kw:self.fail('Lease lost'))
        client.signal_get.assert_not_awaited()
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'pending')

    def test_updated_during_fetch_is_not_lost_and_other_tenant_not_admitted(self):
        self.enqueue()
        payload=sale(self.now,'order');payload['transactions']=[{'id':'tx'}]
        def api(resource,identifier=None,params=None):
            if resource=='transactions':
                self.enqueue(id='during-fetch',eventType='TRANSACTION_UPDATED')
                return {'id':'tx','references':[{'type':'POYNT_ORDER','id':'order'}]}
            return payload
        self.run_jobs(api)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'pending')
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)
            job=session.scalar(select(TransactionSignal));job.next_attempt_at=self.now-timedelta(seconds=1);session.commit()
        self.run_jobs(lambda *a,**kw:payload)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'done')
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)
        self.enqueue(id='other-store',resourceId='other-tx')
        self.run_jobs(lambda resource,identifier=None,params=None:
            {'id':'other-tx','references':[{'type':'POYNT_ORDER','id':'foreign'}]} if resource=='transactions'
            else sale(self.now,'foreign',store='unconfigured-other-store'))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)

    def test_stale_events_and_stopped_kitchen_do_not_trigger_api(self):
        self.assertEqual(self.enqueue(createdAt=(self.now-timedelta(minutes=10)).isoformat()),'stale_event')
        self.enqueue()
        with self.factory() as session:
            session.get(KitchenSession,1).active=False;session.commit()
        client,_=self.run_jobs(lambda *a,**kw:self.fail('Stopped kitchen'))
        client.signal_get.assert_not_awaited()

    def test_existing_order_hook_and_signal_do_not_duplicate_or_resurrect_ticket(self):
        payload=sale(self.now,'order');payload['transactions']=[{'id':'tx'}]
        with self.factory() as session:
            save_order(session,1,'business',payload);session.commit()
            ticket=session.scalar(select(KitchenTicket));ticket.state='archived';session.commit()
        self.enqueue()
        self.run_jobs(lambda resource,identifier=None,params=None:
            {'id':'tx','references':[{'type':'POYNT_ORDER','id':'order'}]} if resource=='transactions' else payload)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))),1)
            self.assertEqual(session.scalar(select(KitchenTicket)).state,'archived')

    def test_retry_limit_stops_endless_unmatched_payments(self):
        self.enqueue()
        for _ in range(6):
            self.run_jobs(lambda resource,identifier=None,params=None:{'id':'tx'} if resource=='transactions' else {'count':0})
            with self.factory() as session:
                session.scalar(select(TransactionSignal)).next_attempt_at=self.now-timedelta(seconds=1);session.commit()
        with self.factory() as session:
            self.assertEqual(session.scalar(select(TransactionSignal)).state,'exhausted')
            self.assertEqual(session.get(TransactionSignalBudget,1).request_count,12)
        client,_=self.run_jobs(lambda *a,**kw:self.fail('Exhausted'))
        client.signal_get.assert_not_awaited()

    def test_heartbeat_shortens_idle_schedule_but_not_errors(self):
        with self.factory() as session:
            state=session.get(DashboardSync,1);state.last_reconciled_at=self.now-timedelta(seconds=60)
            state.next_reconcile_at=self.now+timedelta(minutes=4);session.commit()
        response=self.client.get('/dashboard/stores/1/kitchen')
        self.assertEqual(response.status_code,200)
        with self.factory() as session:
            self.assertLess(session.get(DashboardSync,1).next_reconcile_at,self.now+timedelta(seconds=2))
            state=session.get(DashboardSync,1);state.error='backoff';state.next_reconcile_at=self.now+timedelta(minutes=2);session.commit()
        self.client.get('/dashboard/stores/1/kitchen')
        with self.factory() as session:
            self.assertGreater(session.get(DashboardSync,1).next_reconcile_at,self.now+timedelta(seconds=110))

    def test_readonly_and_stale_sessions_do_not_force_active_polling(self):
        self.client.get('/dashboard/stores/1/kitchen-watch')
        with self.factory() as session:
            original=session.get(DashboardSync,1).next_reconcile_at
            session.get(KitchenSession,1).last_seen_at=self.now-timedelta(minutes=16);session.commit()
        self.assertTrue(self.client.get('/dashboard/stores/1/kitchen').json()['session']['needs_resume'])
        with self.factory() as session:
            self.assertEqual(session.get(DashboardSync,1).next_reconcile_at,original)
        self.assertEqual(self.enqueue(),'kitchen_inactive_or_needs_resume')

    def test_migration_upgrade_and_downgrade_on_isolated_schema(self):
        # Exercise only this migration against the local test DB, not Alembic env.
        for table in (TransactionSignal.__table__,TransactionSignalReceipt.__table__,TransactionSignalBudget.__table__):
            table.drop(self.engine)
        path=Path('alembic/versions/f74c28a610be_transaction_order_signals.py')
        spec=importlib.util.spec_from_file_location('signal_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                self.assertIn('transaction_order_signals',inspect(connection).get_table_names())
                module.downgrade()
                self.assertNotIn('transaction_order_signals',inspect(connection).get_table_names())

    def test_signed_callback_durably_enqueues_without_probe_lookups(self):
        from routers import transaction_signal_probe
        self.client.app.include_router(transaction_signal_probe.router)
        secret='integration-test-secret-not-a-real-secret-123'
        data={**self.event(),'hookId':'sideband-hook','applicationId':'app','resource':'/transactions'}
        body=json.dumps(data).encode()
        signature=base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha1).digest()).decode()
        with patch('routers.transaction_signal_probe.SessionLocal',self.factory),patch.dict(os.environ,{
            'POYNT_TRANSACTION_PROBE_ENABLED':'1','POYNT_TRANSACTION_PROBE_SECRET':secret,
            'POYNT_TRANSACTION_PROBE_HOOK_ID':'sideband-hook','POYNT_TRANSACTION_PROBE_ORGANIZATION_ID':'1',
            'POYNT_TRANSACTION_LOOKUP_ENABLED':'1'}),patch('transaction_order_probe.run_probe',new_callable=AsyncMock) as experimental:
            response=self.client.post('/webhooks/poynt/transaction-probe/1',content=body,
                headers={'Poynt-Webhook-Signature':signature})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['mode'],'transaction_trigger')
            experimental.assert_not_awaited()
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(TransactionSignal.id))),1)

    def test_missing_migration_does_not_stop_normal_order_polling(self):
        for table in (TransactionSignal.__table__,TransactionSignalReceipt.__table__,TransactionSignalBudget.__table__):
            table.drop(self.engine)
        with self.factory() as session:
            session.get(DashboardSync,1).next_reconcile_at=self.now-timedelta(seconds=1);session.commit()
        with patch('live_dashboard_service.PoyntClient') as cls:
            cls.return_value.get_recent_orders=AsyncMock(return_value=[])
            asyncio.run(process_organization(1,'business',self.claim[1]))
            self.assertTrue(cls.return_value.get_recent_orders.await_count)

    def test_existing_order_webhook_keeps_priority_over_sideband(self):
        from live_dashboard_models import DashboardNotification
        self.enqueue()
        with self.factory() as session:
            session.add(DashboardNotification(organization_id=1,business_id='business',
                notification_id='old-hook-event',order_id='old-order'));session.commit()
        calls=[]
        def old_order(identifier):
            calls.append('order_hook');return sale(self.now,identifier)
        def signal(resource,identifier=None,params=None):
            calls.append('signal_'+resource)
            return {'id':'tx','references':[{'type':'POYNT_ORDER','id':'new-order'}]} if resource=='transactions' else sale(self.now,'new-order')
        with patch('live_dashboard_service.PoyntClient') as cls:
            cls.return_value.get_order=AsyncMock(side_effect=old_order)
            cls.return_value.signal_get=AsyncMock(side_effect=signal)
            cls.return_value.get_recent_orders=AsyncMock(return_value=[])
            asyncio.run(process_organization(1,'business',self.claim[1]))
        self.assertEqual(calls[0],'order_hook')
        self.assertIn('signal_orders',calls)


if __name__=='__main__':unittest.main()
