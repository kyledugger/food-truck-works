"""Passive webhook probe checks with fake sessions; no live DB/API access."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['DOTENV_FILE'] = '/nonexistent-ftw-probe-test-env'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import transaction_signal_probe as probe


SECRET = 'probe-test-secret-not-a-real-key-123456'
ENV = {'POYNT_TRANSACTION_PROBE_ENABLED': '1', 'POYNT_TRANSACTION_PROBE_ORGANIZATION_ID': '1',
       'POYNT_TRANSACTION_PROBE_SECRET': SECRET, 'POYNT_APP_ID': 'app',
       'POYNT_TRANSACTION_PROBE_HOOK_ID': 'probe-hook'}


def notification(**changes):
    return {'id': 'notification', 'businessId': 'business', 'resourceId': 'transaction',
            'hookId': 'probe-hook', 'storeId': 'store', 'applicationId': 'app',
            'resource': '/transactions', 'eventType': 'TRANSACTION_CAPTURED',
            'createdAt': '2026-11-16T04:00:00Z', **changes}


def sign(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha1).digest()).decode()


class ReadOnlySession:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def scalar(self, query):
        params = query.compile().params
        if params.get('business_id_1') == 'business' and params.get('organization_id_1') == 1:
            return SimpleNamespace(organization_id=1, business_id='business')
        return None


class TransactionProbeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {**ENV, 'POYNT_TRANSACTION_LOOKUP_ENABLED':'0'})
        self.env.start()
        self.db = patch.object(probe, 'SessionLocal', ReadOnlySession)
        self.db.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.db.stop)
        probe._seen.clear()
        app = FastAPI()
        app.include_router(probe.router)
        self.client = TestClient(app)

    def send(self, data=None, path='/webhooks/poynt/transaction-probe/1', signature=None):
        body = json.dumps(data or notification()).encode()
        return self.client.post(path, content=body,
            headers={'Poynt-Webhook-Signature': signature if signature is not None else sign(body)})

    def test_valid_event_is_logged_and_only_observed(self):
        data = notification(customer={'email': 'PRIVATE'}, fundingSource={'card': 'PRIVATE'},
                            links=[{'href': 'http://do-not-follow.invalid'}])
        with self.assertLogs(probe.logger, level='INFO') as logs:
            response = self.send(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'accepted': True, 'mode': 'observe_only'})
        self.assertIn('transaction_probe_notification', str(logs.output))
        self.assertNotIn('PRIVATE', str(logs.output))
        self.assertNotIn('do-not-follow', str(logs.output))

    def test_disabled(self):
        with patch.dict(os.environ, {'POYNT_TRANSACTION_PROBE_ENABLED': '0'}):
            self.assertEqual(self.send().status_code, 404)

    def test_active_mode_schedules_probe_after_response_body(self):
        import asyncio
        import transaction_order_probe
        response_finished = []
        called = []
        app = FastAPI()
        app.include_router(probe.router)
        async def lookup(*args):
            self.assertTrue(response_finished)
            called.append(args)
        async def observed_app(scope, receive, send):
            async def observed_send(message):
                await send(message)
                if message['type']=='http.response.body' and not message.get('more_body'):
                    response_finished.append(True)
            await app(scope, receive, observed_send)
        body=json.dumps(notification()).encode()
        with patch.dict(os.environ, {'POYNT_TRANSACTION_LOOKUP_ENABLED':'1'}), \
             patch.object(transaction_order_probe, 'run_probe', lookup):
            with TestClient(observed_app) as client:
                response=client.post('/webhooks/poynt/transaction-probe/1', content=body,
                    headers={'Poynt-Webhook-Signature':sign(body)})
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(called),1)

    def test_bad_signature(self):
        self.assertEqual(self.send(signature='wrong').status_code, 401)

    def test_wrong_organization_business_application_or_hook(self):
        self.assertEqual(self.send(path='/webhooks/poynt/transaction-probe/2').status_code, 403)
        for field, value in [('businessId','other'), ('applicationId','other'), ('hookId','order-hook')]:
            with self.subTest(field=field):
                self.assertEqual(self.send(notification(**{field:value})).status_code, 403)

    def test_order_event_is_not_accepted_as_transaction(self):
        self.assertEqual(self.send(notification(resource='/orders', eventType='ORDER_COMPLETED')).status_code, 400)

    def test_transaction_updated_and_missing_store_are_observable(self):
        data = notification(eventType='TRANSACTION_UPDATED')
        del data['storeId']
        self.assertEqual(self.send(data).status_code, 200)

    def test_body_limit_and_invalid_json(self):
        body = b'x' * 65537
        r = self.client.post('/webhooks/poynt/transaction-probe/1', content=body,
                             headers={'Poynt-Webhook-Signature': sign(body)})
        self.assertEqual(r.status_code, 413)
        body = b'{bad json'
        r = self.client.post('/webhooks/poynt/transaction-probe/1', content=body,
                             headers={'Poynt-Webhook-Signature': sign(body)})
        self.assertEqual(r.status_code, 400)

    def test_missing_config(self):
        with patch.dict(os.environ, {'POYNT_TRANSACTION_PROBE_HOOK_ID': ''}):
            self.assertEqual(self.send().status_code, 503)

    def test_event_age_uses_offsets_and_does_not_expose_arbitrary_fields(self):
        now = datetime.fromisoformat('2026-11-16T04:00:01+00:00')
        row = probe.evidence(notification(createdAt='2026-11-15T21:00:00-07:00'), 1, now)
        self.assertEqual(row['provider_event_age_ms'], 1000)
        self.assertFalse(row['duplicate_in_this_process'])
        self.assertTrue(probe.evidence(notification(), 1, now)['duplicate_in_this_process'])
        self.assertIsNone(probe.evidence(notification(createdAt='2026-11-16T04:00:00'), 1, now)['provider_event_age_ms'])
        body = b'not-json'
        r = self.client.post('/webhooks/poynt/transaction-probe/1', content=body,
                             headers={'Poynt-Webhook-Signature': sign(body)})
        self.assertEqual(r.status_code, 400)

    def test_duplicates_and_timing_metadata(self):
        now = datetime(2026,11,16,4,0,2,tzinfo=timezone.utc)
        first = probe.evidence(notification(), 1, now)
        second = probe.evidence(notification(), 1, now)
        self.assertEqual(first['provider_event_age_ms'], 2000)
        self.assertFalse(first['duplicate_in_this_process'])
        self.assertTrue(second['duplicate_in_this_process'])


if __name__ == '__main__':
    unittest.main()
