"""Isolated integration checks; no live Postmark calls or production DB."""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, select, DateTime, Column, Integer, Boolean
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
class Base(DeclarativeBase): pass
class TestUser(Base):
    __tablename__='test_users'
    id=Column(Integer,primary_key=True)
    is_active=Column(Boolean,default=True)
models=types.ModuleType('models'); models.User=TestUser; sys.modules['models']=models
engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Session = sessionmaker(engine)
db = types.ModuleType('database'); db.Base = Base; db.SessionLocal = Session
sys.modules['database'] = db
# SQLite drops timezone info. This test adapter restores it; production uses UTCInstant.
class TestInstant(TypeDecorator):
    impl = DateTime
    cache_ok = True
    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value else None
inst = types.ModuleType('instant_type'); inst.UTCInstant = TestInstant
sys.modules['instant_type'] = inst
mail = types.ModuleType('email_service')
class EmailDeliveryError(Exception): pass
mail.EmailDeliveryError = EmailDeliveryError; mail._send_email = lambda *args: None
sys.modules['email_service'] = mail
from launch_models import LaunchSubscriber
spec=importlib.util.spec_from_file_location('launch_route_test', ROOT/'routers/launch_updates.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
r.templates.env.loader.searchpath=[str(ROOT/'templates'), str(ROOT.parent/'app/templates')]
app=FastAPI(); app.add_middleware(SessionMiddleware,secret_key='test-secret'); app.include_router(r.router)

@app.get('/test-session/{uid}')
def test_session(request: r.Request, uid: int):
    request.session['user_id']=uid
    return {'ok':True}

class Tests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
        self.env=patch.dict(os.environ, {'SESSION_SECRET':'test', 'APP_BASE_URL':'https://example.com', 'TURNSTILE_SITE_KEY':'site', 'TURNSTILE_SECRET_KEY':'secret', 'POSTMARK_BROADCAST_STREAM':'broadcasts', 'POSTMARK_WEBHOOK_USERNAME':'hook', 'POSTMARK_WEBHOOK_PASSWORD':'secret'})
        self.env.start(); self.client=TestClient(app)
    def tearDown(self): self.env.stop()
    def form(self):
        import re
        resp=self.client.get('/launch-updates')
        self.assertEqual(resp.headers['cache-control'],'no-store')
        return {'email':'owner@example.com', 'feedback':'Need scheduling', 'consent':'yes', 'csrf':re.search('name="csrf" value="([^"]+)"',resp.text)[1], 'cf-turnstile-response':'token'}
    def post(self, data, success=True):
        response=types.SimpleNamespace(raise_for_status=lambda: None,json=lambda:{'success':success,'hostname':'example.com','action':'launch-signup'})
        with patch.object(r.httpx,'post',return_value=response),patch.object(r,'send_launch_confirmation') as sender:
            out=self.client.post('/launch-updates',data=data)
            return out,sender.call_args
    def test_consent_captcha_csrf(self):
        data=self.form(); data['consent']=''; self.assertEqual(self.post(data)[0].status_code,400)
        data['consent']='yes'; self.assertEqual(self.post(data,False)[0].status_code,400)
        data['csrf']='bad'; self.assertEqual(self.post(data)[0].status_code,403)
        with Session() as db: self.assertEqual(len(db.execute(select(LaunchSubscriber)).scalars().all()),0)
    def test_confirmation_feedback_and_duplicate(self):
        data=self.form(); out,call=self.post(data); self.assertEqual(out.status_code,200)
        token=call.args[1]
        with Session() as db:
            row=db.execute(select(LaunchSubscriber)).scalar_one(); self.assertIsNone(row.confirmed_at); self.assertEqual(row.feedback,'Need scheduling'); self.assertNotEqual(row.token_hash,token)
        self.client.get('/launch-updates/confirm/'+token)
        with Session() as db: self.assertIsNone(db.execute(select(LaunchSubscriber)).scalar_one().confirmed_at)
        self.assertEqual(self.client.post('/launch-updates/confirm/'+token,data={'csrf':data['csrf']}).status_code,200)
        self.assertIsNone(self.post(data)[1])
        self.assertEqual(self.client.post('/launch-updates/confirm/'+token,data={'csrf':data['csrf']}).status_code,400)
    def test_suppression_and_auth(self):
        data=self.form(); _,call=self.post(data)
        payload={'RecordType':'SubscriptionChange','MessageStream':'broadcasts','Recipient':'owner@example.com','ChangedAt':datetime.now(timezone.utc).isoformat(),'SuppressSending':True,'SuppressionReason':'SpamComplaint'}
        self.assertEqual(self.client.post('/launch-updates/postmark',json=payload).status_code,401)
        self.assertEqual(self.client.post('/launch-updates/postmark',json=payload,auth=('hook','secret')).status_code,200)
        with Session() as db: self.assertTrue(db.execute(select(LaunchSubscriber)).scalar_one().suppressed)
        self.assertIsNone(self.post(data)[1])
        self.assertEqual(self.client.post('/launch-updates/confirm/'+call.args[1],data={'csrf':data['csrf']}).status_code,400)
        self.assertEqual(self.client.get('/admin/launch-updates.csv').status_code,401)
    def test_expired_and_throttle(self):
        data=self.form(); _,call=self.post(data)
        with Session() as db:
            row=db.execute(select(LaunchSubscriber)).scalar_one(); row.token_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); db.commit()
        self.assertEqual(self.client.post('/launch-updates/confirm/'+call.args[1],data={'csrf':data['csrf']}).status_code,400)
        for _ in range(9): self.post(data)
        self.assertEqual(self.post(data)[0].status_code,429)
    def test_admin_allowlist_and_escaping(self):
        self.client.get('/test-session/7')
        with Session() as db:
            db.add(TestUser(id=7,is_active=True))
            db.add(LaunchSubscriber(email='a@example.com',feedback='<script>alert(1)</script>',consent_version='v1',created_at=datetime.now(timezone.utc),suppressed=False))
            db.commit()
        self.assertEqual(self.client.get('/admin/launch-updates').status_code,403)
        with patch.dict(os.environ,{'LAUNCH_ADMIN_USER_IDS':'7'}):
            resp=self.client.get('/admin/launch-updates'); self.assertEqual(resp.status_code,200); self.assertIn('&lt;script&gt;',resp.text)
            self.assertEqual(self.client.get('/admin/launch-updates.csv').status_code,200)
            with Session() as db:
                db.get(TestUser,7).is_active=False; db.commit()
            self.assertEqual(self.client.get('/admin/launch-updates').status_code,403)

    def test_broadcast_stream_and_csv(self):
        from launch_email import build_broadcast_message
        with patch.dict(os.environ,{'POSTMARK_BROADCAST_STREAM':'','LAUNCH_EMAIL_FROM':'updates@example.com'}):
            with self.assertRaises(RuntimeError): build_broadcast_message('a@example.com','s','t','h')
        with patch.dict(os.environ,{'LAUNCH_EMAIL_FROM':'updates@example.com'}):
            msg=build_broadcast_message('a@example.com','s','t','h'); self.assertEqual(msg['MessageStream'],'broadcasts'); self.assertIn('{{pm:unsubscribe}}',msg['HtmlBody'])
        self.assertEqual(r.safe_csv(' =HYPERLINK("bad")')[0],"'")

if __name__=='__main__': unittest.main()
