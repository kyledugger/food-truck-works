"""Probe registration safeguards with mocked provider and read-only SQL."""
import importlib.util
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('probe_manager', Path(__file__).resolve().parents[1] / 'manage_transaction_probe.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class DB:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, *args):
        assert sql.startswith(('SET TRANSACTION READ ONLY', 'SELECT '))
        return self
    def fetchone(self):
        return {'business_id':'business', 'access_token':'SECRET_TOKEN', 'token_type':'BEARER',
                'expires_at':datetime.now(timezone.utc)+timedelta(hours=1)}


class HTTP:
    hooks = []
    requests = []
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def request(self, method, path, **kwargs):
        self.requests.append((method, path))
        if method == 'GET': data = {'hooks':self.hooks}
        elif method == 'POST':
            data = {'id':'probe-id', 'active':True, **kwargs['json']}
            self.hooks.append(data)
        else:
            assert method == 'DELETE' and path == '/hooks/probe-id'
            self.hooks[:] = [h for h in self.hooks if h['id'] != 'probe-id']
            data = {}
        return SimpleNamespace(is_success=True, content=b'json', json=lambda:data)


class ProbeManagerTests(unittest.TestCase):
    def test_register_is_separate_idempotent_and_removal_is_guarded(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d)/'.env';env.touch()
            args = SimpleNamespace(dotenv=str(env), organization_id=1,
                public_url='https://ftw.example', journal=str(Path(d)/'journal.json'), action='register')
            HTTP.hooks = [{'id':'existing-order-hook', 'businessId':'business', 'applicationId':'app',
                'deliveryUrl':'https://ftw.example/webhooks/poynt/orders/1', 'eventTypes':['ORDER_COMPLETED']}]
            HTTP.requests = []
            with patch.dict(os.environ, {'POYNT_APP_ID':'app', 'DATABASE_URL':'postgresql://unused',
                    'POYNT_TRANSACTION_PROBE_SECRET':'test-signing-key-long-enough-123456'}), \
                 patch.object(manager, 'load_dotenv'), patch.object(manager.psycopg, 'connect', return_value=DB()), \
                 patch.object(manager.httpx, 'Client', HTTP):
                manager.run(args)
                manager.run(args)
                self.assertEqual(sum(method=='POST' for method,_ in HTTP.requests),1)
                self.assertNotIn('SECRET_TOKEN',Path(args.journal).read_text())
                args.action='remove'
                manager.run(args)
                self.assertEqual([h['id'] for h in HTTP.hooks],['existing-order-hook'])

    def test_callback_must_be_https_origin(self):
        for url in ['http://ftw.example','https://user:pass@ftw.example', 'https://ftw.example/x', 'https://ftw.example/?secret=x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                manager.callback(url,1)


if __name__ == '__main__': unittest.main()
