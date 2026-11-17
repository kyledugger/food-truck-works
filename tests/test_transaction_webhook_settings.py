"""No network or production DB. Registration, tenant isolation and signed delivery."""
import asyncio
import base64
from datetime import timedelta
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy import create_engine, text, inspect
from test_live_dashboard import DatabaseAndRoutesTests as Fixture
from models import OrganizationStore, OrganizationMember
from live_dashboard_models import TransactionWebhook, TransactionStoreSetting, TransactionSignal
from transaction_webhooks import configure, registration, reconcile, selected_stores, callback, EVENTS
from integrations.crypto import decrypt
from store_time import utc_now
from routers import transaction_signal_probe, integrations


class SettingsTests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        self.extra = patch.dict(os.environ,{'INTEGRATION_TOKEN_KEYS':Fernet.generate_key().decode(),
            'APP_BASE_URL':'https://ftw.example','POYNT_TRANSACTION_TRIGGER_ENABLED':'1',
            'POYNT_TRANSACTION_TRIGGER_STORE_IDS':'truck', 'POYNT_TRANSACTION_PROBE_ORGANIZATION_ID':'1',
            'POYNT_TRANSACTION_PROBE_SECRET':'','POYNT_TRANSACTION_PROBE_HOOK_ID':''})
        self.extra.start()
        self.addCleanup(self.extra.stop)
        for module in (integrations,transaction_signal_probe):
            p = patch.object(module,'SessionLocal',self.factory)
            p.start();self.addCleanup(p.stop)
        self.client.app.include_router(integrations.router)
        self.client.app.include_router(transaction_signal_probe.router)

    def tearDown(self):
        Fixture.tearDown(self)

    def setup(self, selected=None):
        with self.factory() as session:
            row = configure(session,1,'business',selected)
            session.commit()
            return row.id

    def provider(self):
        return SimpleNamespace(list_transaction_hooks=AsyncMock(return_value=[]),
            create_transaction_hook=AsyncMock(return_value={'id':'managed-hook','active':True,'eventTypes':EVENTS}))

    def run_setup(self, provider):
        asyncio.run(reconcile(self.factory,1,'business',provider,force=True))

    def send(self, secret, **changes):
        data = {'id':'notification','businessId':'business','resourceId':'tx','hookId':'managed-hook',
            'storeId':'truck','applicationId':'app','resource':'/transactions',
            'eventType':'TRANSACTION_CAPTURED','createdAt':utc_now().isoformat(),**changes}
        body = json.dumps(data).encode()
        signature = base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha1).digest()).decode()
        return self.client.post('/webhooks/poynt/transactions/1',content=body,
            headers={'Poynt-Webhook-Signature':signature})

    def test_multistore_preferences_and_tenant_isolation(self):
        with self.factory() as session:
            session.add(OrganizationStore(organization_id=1,store_id='popup',poynt_name='Popup',is_active=True))
            session.commit()
        self.setup({'truck','popup'})
        with self.factory() as session:
            self.assertEqual(selected_stores(session,1,'business'),{'truck','popup'})
            self.assertEqual(selected_stores(session,2,'business'),set())
            self.assertEqual(selected_stores(session,1,'other-business'),set())
            with self.assertRaises(ValueError): configure(session,1,'business',{'foreign-store'})
        self.setup(set())
        with self.factory() as session:
            self.assertEqual(selected_stores(session,1,'business'),set())

    def test_callback_uses_existing_app_base_url(self):
        with patch.dict(os.environ,{'APP_BASE_URL':'https://foodtruckworks.com/',
                'PUBLIC_BASE_URL':'https://obsolete.example',
                'POYNT_REDIRECT_URI':'https://oauth.example/oauth/callback'}):
            self.assertEqual(callback(7),'https://foodtruckworks.com/webhooks/poynt/transactions/7')
        with patch.dict(os.environ,{'APP_BASE_URL':'',
                'POYNT_REDIRECT_URI':'https://fallback.example/oauth/callback'}):
            self.assertEqual(callback(7),'https://fallback.example/webhooks/poynt/transactions/7')
        with patch.dict(os.environ,{'APP_BASE_URL':'http://unsafe.example'}):
            with self.assertRaises(ValueError):callback(7)

    def test_registration_encrypts_secret_and_reuses_hook(self):
        row_id = self.setup({'truck'})
        provider = self.provider()
        self.run_setup(provider)
        args = provider.create_transaction_hook.call_args.args
        with self.factory() as session:
            row = session.get(TransactionWebhook,row_id)
            self.assertEqual(row.status,'ready')
            self.assertEqual(decrypt(row.secret_encrypted),args[1])
            self.assertNotIn(args[1],row.secret_encrypted)
            self.assertEqual(row.delivery_url,'https://ftw.example/webhooks/poynt/transactions/1')
        provider.list_transaction_hooks.return_value = [{'applicationId':'app','businessId':'business',
            'deliveryUrl':args[0],'id':'managed-hook','active':True,'eventTypes':EVENTS}]
        self.setup(None);self.run_setup(provider)
        self.assertEqual(provider.create_transaction_hook.await_count,1)

    def test_timeout_recovery_uses_same_secret_and_does_not_duplicate(self):
        self.setup({'truck'})
        provider = self.provider()
        provider.create_transaction_hook.side_effect = TimeoutError('sensitive provider body')
        self.run_setup(provider)
        args = provider.create_transaction_hook.call_args.args
        provider.list_transaction_hooks.return_value = [{'applicationId':'app','businessId':'business',
            'deliveryUrl':args[0],'id':'managed-hook','active':True,'eventTypes':EVENTS}]
        self.run_setup(provider)
        self.assertEqual(provider.create_transaction_hook.await_count,1)
        with self.factory() as session:
            row = registration(session,1,'business')
            self.assertEqual(row.status,'ready')
            self.assertIsNone(row.error)
            self.assertEqual(decrypt(row.secret_encrypted),args[1])

    def test_valid_delivery_queues_dedupes_and_reports_health(self):
        self.setup({'truck'});provider = self.provider();self.run_setup(provider)
        secret = provider.create_transaction_hook.call_args.args[1]
        self.assertEqual(self.send(secret).json()['outcome'],'queued')
        self.assertEqual(self.send(secret).json()['outcome'],'duplicate_delivery')
        self.assertEqual(self.send('wrong-secret').status_code,401)
        self.assertEqual(self.send(secret,hookId='other-hook').status_code,403)
        self.assertEqual(self.send(secret,businessId='foreign-business').status_code,403)
        with self.factory() as session:
            self.assertIsNotNone(registration(session,1,'business').last_received_at)
            self.assertEqual(len(session.scalars(select(TransactionSignal)).all()),1)
        self.setup(set())
        self.assertEqual(self.send(secret,id='event-2').json()['outcome'],'not_enabled_for_store')

    def test_registration_lease_and_duplicates_fail_closed(self):
        row_id = self.setup({'truck'});provider = self.provider()
        with self.factory() as session:
            row=session.get(TransactionWebhook,row_id);row.lease_until=utc_now()+timedelta(minutes=1)
            session.commit()
        self.run_setup(provider)
        provider.list_transaction_hooks.assert_not_awaited()
        with self.factory() as session:
            row=session.get(TransactionWebhook,row_id);row.lease_until=None
            url=row.delivery_url;session.commit()
        hook={'applicationId':'app','businessId':'business','deliveryUrl':url,'id':'hook'}
        provider.list_transaction_hooks.return_value=[hook,hook]
        self.run_setup(provider)
        provider.create_transaction_hook.assert_not_awaited()
        with self.factory() as session:self.assertEqual(registration(session,1,'business').status,'error')

    def test_pilot_adoption_works_without_environment_credentials_afterwards(self):
        with patch.dict(os.environ,{'POYNT_TRANSACTION_PROBE_SECRET':'p'*40,
                'POYNT_TRANSACTION_PROBE_HOOK_ID':'managed-hook'}):
            self.setup(None)
        with self.factory() as session:
            row=registration(session,1,'business')
            self.assertEqual(decrypt(row.secret_encrypted),'p'*40)
            self.assertTrue(row.delivery_url.endswith('/transaction-probe/1'))
            self.assertEqual(selected_stores(session,1,'business'),{'truck'})
        # Reuse new route sender by replacing only its target path in the test client.
        original_post=self.client.post
        with patch.object(self.client,'post',side_effect=lambda path,**kw:original_post(
                '/webhooks/poynt/transaction-probe/1',**kw)):
            self.assertEqual(self.send('p'*40).json()['outcome'],'queued')

    def test_integration_page_and_post_require_owner_and_csrf(self):
        self.assertEqual(self.client.get('/integrations').status_code,401)
        self.client.get('/test-login')
        response=self.client.get('/integrations')
        self.assertEqual(response.status_code,200)
        self.assertIn('GoDaddy kitchen notifications',response.text)
        self.assertNotIn('secret_encrypted',response.text)
        self.assertEqual(self.client.post('/integrations/poynt/transactions',
            data={'csrf_token':'bad','stores':'truck'}).status_code,403)
        with self.factory() as session:
            member=session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1))
            member.role='member';session.commit()
        self.assertEqual(self.client.get('/integrations').status_code,403)

    def test_authorized_settings_save_calls_automatic_registration(self):
        import re
        self.client.get('/test-login')
        page=self.client.get('/integrations')
        csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
        provider=self.provider()
        with patch('poynt.client.PoyntClient',return_value=provider):
            response=self.client.post('/integrations/poynt/transactions',data={
                'csrf_token':csrf,'stores':'truck'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        provider.create_transaction_hook.assert_awaited_once()
        with self.factory() as session:
            self.assertEqual(selected_stores(session,1,'business'),{'truck'})


class MigrationTests(unittest.TestCase):
    def test_actual_alembic_upgrade_and_read_only_preflight(self):
        from apply_transaction_webhook_settings_migration import run
        with tempfile.TemporaryDirectory() as directory:
            url='sqlite:///'+str(Path(directory)/'migration.sqlite')
            engine=create_engine(url)
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE organizations (id INTEGER PRIMARY KEY)'))
                connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
                connection.execute(text("INSERT INTO alembic_version VALUES ('f74c28a610be')"))
            env=Path(directory)/'migration.env'
            env.write_text('DATABASE_URL='+url+'\n')
            with patch.dict(os.environ,{'DATABASE_URL':url}):
                from alembic.config import Config
                from alembic.script import ScriptDirectory
                from alembic import command
                config=Config('alembic.ini')
                scripts=ScriptDirectory.from_config(config)
                if scripts.get_heads()!=['b13d82e69a04']:
                    # An old guarded helper must refuse a newer source package.
                    with self.assertRaisesRegex(ValueError,'Source migration heads'):
                        run(SimpleNamespace(dotenv=str(env),apply=False))
                    self.assertNotIn('poynt_transaction_webhooks',inspect(engine).get_table_names())
                    command.upgrade(config,'b13d82e69a04')
                else:
                    run(SimpleNamespace(dotenv=str(env),apply=False))
                    self.assertNotIn('poynt_transaction_webhooks',inspect(engine).get_table_names())
                    run(SimpleNamespace(dotenv=str(env),apply=True))
            self.assertIn('poynt_transaction_webhooks',inspect(engine).get_table_names())
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text('SELECT version_num FROM alembic_version')),'b13d82e69a04')
            engine.dispose()


if __name__ == '__main__': unittest.main()
