"""Isolated SQLite tests: no application/production database is used."""
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('POYNT_REDIRECT_URI', 'http://test/oauth')
os.environ.setdefault('POYNT_APP_ID', 'test')
os.environ.setdefault('POYNT_AUTHORIZE_URL', 'http://test/auth')

from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from database import Base
from instant_type import UTCInstant
from models import User, Organization, OrganizationMember, OrganizationStore, StoreAssignment, Employee
from tip_submission_model import TipSubmission, TipStoreSettings, TipOrderClaim, TipEmployeePayout
from claim_tip_access import claim_stores, require_claim_store
from routers import claim_tips, poynt
from store_time import utc_iso, as_utc


class ClaimTipsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        # SQLite strips offsets. Adapt only this isolated test database.
        self.patches = [patch.object(UTCInstant, 'process_result_value', lambda _, value, dialect: as_utc(value) if value else None),
            patch.object(claim_tips, 'SessionLocal', self.factory), patch.object(poynt, 'SessionLocal', self.factory)]
        for value in self.patches: value.start()
        with self.factory() as s:
            s.add_all([Organization(id=1,name='Test'), Organization(id=2,name='Other'),
                User(id=1,email='manager@test',password_hash='test'), User(id=2,email='staff@test',password_hash='test'),
                User(id=3,account_type='store_display',password_hash='test')])
            s.flush()
            s.add_all([OrganizationMember(id=1,organization_id=1,user_id=1,role='manager'),
                OrganizationMember(id=2,organization_id=1,user_id=2,role='member'),
                OrganizationMember(id=3,organization_id=1,user_id=3,role='member',display_username='screen'),
                OrganizationStore(id=1,organization_id=1,store_id='store-a',poynt_name='Truck',timezone_name='America/Phoenix'),
                OrganizationStore(id=2,organization_id=1,store_id='store-b',poynt_name='Booth',timezone_name='America/Phoenix'),
                OrganizationStore(id=3,organization_id=2,store_id='store-c',poynt_name='Other',timezone_name='America/New_York'),
                Employee(id=11,organization_id=1,first_name='Abbey',last_name='Test',email='a@test'),
                Employee(id=17,organization_id=1,first_name='Tessa',last_name='Test',email='t@test')])
            s.flush()
            s.add_all([StoreAssignment(id=1,organization_member_id=2,organization_store_id=1,role='staff'),
                StoreAssignment(id=2,organization_member_id=3,organization_store_id=1,role='store_display',session_version=1),
                TipStoreSettings(organization_id=1,store_id='store-a',tip_allocation_start_at=datetime(2026,1,1,tzinfo=timezone.utc))])
            s.commit()
        self.launch=datetime.now(timezone.utc).replace(second=0,microsecond=0)
        self.start=self.launch-timedelta(hours=1)
        self.session={'user_id':1,'organization_id':1,'tip_claim':dict(token='token',user_id=1,
            organization_id=1,launched=utc_iso(self.launch),store_id='store-a',start=utc_iso(self.start),end=utc_iso(self.launch))}
        self.request=Request({'type':'http','method':'POST','path':'/poynt/claim-tips/load',
            'headers':[],'session':self.session,'query_string':b''})
        self.orders=[self.order('start',self.start,101),self.order('middle',self.start+timedelta(minutes=30),201),
            self.order('end',self.launch,999),self.order('other',self.start+timedelta(minutes=10),999,'store-b')]

    def tearDown(self):
        for value in reversed(self.patches): value.stop()
        self.engine.dispose()

    def order(self, id, moment, cents, store='store-a'):
        return dict(id=id,createdAt=utc_iso(moment),context={'storeId':store},
            statuses={'transactionStatusSummary':'COMPLETED'},amounts={'capturedTotals':{'tipAmount':cents}})

    def test_store_authority_and_display_lock(self):
        with self.factory() as s:
            self.assertEqual(len(claim_stores(s,self.request,1)[0]),2)
            self.session['user_id']=2
            self.assertEqual([x.store_id for x in claim_stores(s,self.request,1)[0]],['store-a'])
            with self.assertRaises(HTTPException): require_claim_store(s,self.request,1,'store-b')
            self.session.update(user_id=3,store_assignment_id=2,store_session_version=1)
            self.assertTrue(require_claim_store(s,self.request,1,'store-a')[2])
            with self.assertRaises(HTTPException): require_claim_store(s,self.request,1,'store-b')
            self.session['store_session_version']=0
            with self.assertRaises(HTTPException): require_claim_store(s,self.request,1,'store-a')

    def test_half_open_allocation_and_remainder(self):
        ranges=[dict(start=utc_iso(self.start),end=utc_iso(self.launch),total_tip_cents=302,employees=[dict(id=11),dict(id=17)])]
        claims, amounts=poynt._allocation_orders(self.orders[:3],ranges)
        self.assertEqual([o['id'] for o,_ in claims],['start','middle'])
        self.assertEqual(amounts,{11:151,17:151})

    def submit(self, total=302, token='token'):
        ranges=[dict(start=utc_iso(self.start),end=utc_iso(self.launch),total_tip_cents=total,employees=[dict(id=11),dict(id=17)])]
        return asyncio.run(poynt.submit_tip_record(self.request,'store-a','Untrusted name',utc_iso(self.start),
            utc_iso(self.launch),total,'{"11":"paycheck","17":"paycheck"}',json.dumps({'ranges':ranges}),token))

    def test_commit_atomicity_and_replay(self):
        with (patch.object(poynt,'get_current_organization_id',return_value=1), patch.object(poynt,'get_organization_role',return_value='manager'),
             patch.object(poynt,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')), patch.object(poynt,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders))):
            result=self.submit()
            self.assertEqual(result.status_code,303)
            with self.factory() as s:
                self.assertEqual(len(s.scalars(select(TipOrderClaim)).all()),2)
                self.assertEqual(len(s.scalars(select(TipEmployeePayout)).all()),2)
                self.assertEqual(s.scalar(select(TipSubmission)).store_name,'Truck')
            with self.assertRaises(HTTPException): self.submit()

    def test_forged_session_rejected_before_poynt(self):
        with patch.object(poynt,'get_current_organization_id',return_value=1), patch.object(poynt,'get_organization_role',return_value='manager'), patch.object(poynt,'fetch_poynt_orders',new=AsyncMock()) as fetch:
            with self.assertRaises(HTTPException): self.submit(token='wrong')
            fetch.assert_not_called()

    def test_loading_excludes_existing_claims_and_end_minute(self):
        # Legacy submission with a broad future end, as in the reported bug.
        with self.factory() as s:
            old=TipSubmission(organization_id=1,submitted_by_user_id=1,store_id='store-a',store_name='Truck',
                report_start_at=self.start,report_end_at=self.launch+timedelta(hours=2),
                total_tip_cents=201,payout_method='paycheck',processing_status='paid',submission_data='{}')
            s.add(old); s.flush()
            s.add(TipOrderClaim(organization_id=1,submission_id=old.id,poynt_business_id='business',
                poynt_order_id='middle',store_id='store-a',created_at=self.start+timedelta(minutes=30),tip_cents=201))
            s.commit()
        local=self.start.astimezone(__import__('zoneinfo').ZoneInfo('America/Phoenix'))
        with (patch.object(claim_tips,'get_current_organization_id',return_value=1),
              patch.object(claim_tips,'get_organization_role',return_value='manager'),
              patch.object(claim_tips,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')),
              patch.object(claim_tips,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders)),
              patch.object(claim_tips.templates,'TemplateResponse',side_effect=lambda **kw: kw)):
            result=asyncio.run(claim_tips.load_claim(self.request,'store-a','token',local.date().isoformat(),local.strftime('%H:%M')))
            context=result['context']
            self.assertEqual(context['total_tips'],3.02)
            self.assertEqual(context['claimed_tips'],2.01)
            self.assertEqual(context['available_tips'],1.01)

    def test_display_submission_returns_to_dashboard(self):
        self.session.update(user_id=3,store_assignment_id=2,store_session_version=1)
        self.session['tip_claim']['user_id']=3
        with (patch.object(poynt,'get_current_organization_id',return_value=1),
              patch.object(poynt,'get_organization_role',return_value='member'),
              patch.object(poynt,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')),
              patch.object(poynt,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders))):
            self.assertEqual(self.submit().headers['location'],'/dashboard')

    def test_concurrent_allocation_requires_reload(self):
        with self.factory() as s:
            old=TipSubmission(organization_id=1,submitted_by_user_id=1,store_id='store-a',store_name='Truck',
                report_start_at=self.start,report_end_at=self.launch,
                total_tip_cents=201,payout_method='paycheck',processing_status='pending',submission_data='{}')
            s.add(old); s.flush()
            s.add(TipOrderClaim(organization_id=1,submission_id=old.id,poynt_business_id='business',
                poynt_order_id='middle',store_id='store-a',created_at=self.start+timedelta(minutes=30),tip_cents=201))
            s.commit()
        with (patch.object(poynt,'get_current_organization_id',return_value=1), patch.object(poynt,'get_organization_role',return_value='manager'),
              patch.object(poynt,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')),
              patch.object(poynt,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders))):
            with self.assertRaises(HTTPException) as error: self.submit()
            self.assertEqual(error.exception.status_code,409)
            with self.factory() as s:
                self.assertEqual(len(s.scalars(select(TipSubmission)).all()),1)


if __name__ == '__main__': unittest.main()
