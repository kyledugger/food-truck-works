"""Isolated role, allocation, approval and history tests."""
import asyncio
import json
import unittest
import importlib.util
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from starlette.requests import Request
from fastapi import HTTPException
from sqlalchemy import select
from test_claim_tips import ClaimTipsTests
from models import Employee, User
from tip_submission_model import TipSubmission, TipEmployeePayout, TipStoreSettings
from tip_history import history_context
from tip_approval import verify_approver, throttle, split_digest
from routers import poynt
from store_time import utc_iso
from auth import hash_password
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, create_engine, text


class TipHistoryApprovalTests(unittest.TestCase):
    setUp=ClaimTipsTests.setUp
    tearDown=ClaimTipsTests.tearDown
    order=ClaimTipsTests.order

    def add_submission(self,store,amount,employee=11,shared=True):
        ranges=[{"start":utc_iso(self.start),"end":utc_iso(self.launch),"total_tip_cents":amount*2 if shared else amount,
            "employees":[{"id":employee,"name":"Abbey"}]+([{"id":17,"name":"Tessa"}] if shared else [])}]
        with self.factory() as s:
            row=TipSubmission(organization_id=1,submitted_by_user_id=1,store_id=store,store_name=store,
                report_start_at=self.start,report_end_at=self.launch,total_tip_cents=amount*2 if shared else amount,
                payout_method="paycheck",processing_status="pending",submission_data=json.dumps({"ranges":ranges}),submitted_at=self.launch)
            s.add(row);s.flush();s.add(TipEmployeePayout(submission_id=row.id,employee_id=employee,employee_name="Abbey",
                amount_cents=amount,payout_method="paycheck",status="pending"))
            if shared:s.add(TipEmployeePayout(submission_id=row.id,employee_id=17,employee_name="Tessa",amount_cents=amount,payout_method="cash",status="pending"))
            s.commit()

    def history(self,user=1,role="manager",store="",start=None,**extra):
        session={"user_id":user,"organization_id":1,**extra}
        request=Request({"type":"http","method":"GET","path":"/poynt/tip-submissions","headers":[],"session":session,"query_string":b''})
        day=self.start.astimezone(__import__('zoneinfo').ZoneInfo("America/Phoenix")).date().isoformat()
        with self.factory() as s:
            return history_context(s,request,1,role,start or day,start or day,store,"","",
                (poynt._get_tip_report_date_bounds,poynt._tip_submission_display,poynt._tip_range_totals))

    def test_employee_shifts_and_personal_aggregate_only(self):
        with self.factory() as s:s.get(Employee,11).user_id=2;s.commit()
        self.add_submission('store-a',151);self.add_submission('store-a',99,employee=17,shared=False)
        context=self.history(user=2,role="member")
        self.assertEqual(len(context['submissions']),1);self.assertEqual(context['tip_personal_total_cents'],151)
        self.assertEqual(len(context['submissions'][0]['shifts'][0]['participants']),2)
        self.assertEqual([p['employee_id'] for p in context['submissions'][0]['payouts']],[11])
        self.assertFalse(context['tip_report_can_process'])

    def test_all_stores_aggregate_and_specific_store(self):
        self.add_submission('store-a',151);self.add_submission('store-b',101)
        context=self.history(role="payroll")
        self.assertEqual(len(context['submissions']),2)
        self.assertEqual(context['tip_range_totals'][0]['paycheck_pending_cents'],252)
        self.assertEqual(len(self.history(store="store-a")['submissions']),1)
        with self.assertRaises(HTTPException):self.history(store="unknown")

    def test_store_manager_scope_and_payroll_filters(self):
        from models import StoreAssignment
        self.add_submission('store-a',151);self.add_submission('store-b',101)
        with self.factory() as s:s.get(StoreAssignment,1).role='store_manager';s.commit()
        context=self.history(user=2,role='member')
        self.assertTrue(context['tip_report_can_process']);self.assertEqual(len(context['submissions']),1)
        with self.assertRaises(HTTPException):self.history(user=2,role='member',store='store-b')
        request=Request({'type':'http','method':'GET','path':'/poynt/tip-submissions','headers':[],'session':{'user_id':1,'organization_id':1},'query_string':b''})
        day=self.start.astimezone(__import__('zoneinfo').ZoneInfo('America/Phoenix')).date().isoformat()
        with self.factory() as s:
            context=history_context(s,request,1,'payroll',day,day,'all','paycheck','pending',(poynt._get_tip_report_date_bounds,poynt._tip_submission_display,poynt._tip_range_totals))
        self.assertEqual(len(context['tip_range_totals']),1);self.assertEqual(context['tip_range_totals'][0]['paycheck_pending_cents'],252)

    def test_terminal_store_today_and_no_controls(self):
        self.add_submission('store-a',151);self.add_submission('store-b',101)
        context=self.history(user=3,role="member",store="store-a",store_assignment_id=2,store_session_version=1)
        self.assertTrue(context['tip_report_terminal']);self.assertFalse(context['tip_report_can_select_date_range'])
        self.assertFalse(context['tip_report_can_process']);self.assertEqual(len(context['submissions']),1)
        self.assertFalse(any(p['can_confirm'] for p in context['submissions'][0]['payouts']))
        with self.assertRaises(HTTPException):self.history(user=3,role="member",store="store-b",store_assignment_id=2,store_session_version=1)
        with self.assertRaises(HTTPException):self.history(user=3,role="member",start="2020-01-01",store_assignment_id=2,store_session_version=1)

    def test_member_cannot_mark_paid_or_reject(self):
        with patch.object(poynt,'get_current_organization_id',return_value=1),patch.object(poynt,'get_organization_role',return_value='member'):
            with self.assertRaises(HTTPException):asyncio.run(poynt.mark_tip_payout_paid(1,self.request))
            with self.assertRaises(HTTPException):asyncio.run(poynt.reject_new_tip_submission(1,self.request))

    def test_authorizer_identity_and_throttle_and_digest(self):
        with self.factory() as s:s.get(User,1).password_hash=hash_password('A-valid-long-password!');s.commit()
        with self.factory() as s:
            setting=s.scalar(select(TipStoreSettings))
            self.assertEqual(verify_approver(s,1,setting,'manager@test','A-valid-long-password!'),1)
            with self.assertRaises(HTTPException):verify_approver(s,2,setting,'manager@test','A-valid-long-password!')
        for _ in range(6):throttle(self.factory,1,1)
        with self.assertRaises(HTTPException) as error:throttle(self.factory,1,1)
        self.assertEqual(error.exception.status_code,429)
        self.assertNotEqual(split_digest('s','a','b',[],{11:1},{11:'cash'},False),split_digest('s','a','b',[],{11:2},{11:'cash'},False))

    def submit_approved(self,cash_paid):
        ranges=[dict(start=utc_iso(self.start),end=utc_iso(self.launch),total_tip_cents=302,employees=[dict(id=11),dict(id=17)])]
        return asyncio.run(poynt.submit_tip_record(self.request,'store-a','Truck',utc_iso(self.start),utc_iso(self.launch),302,
            '{"11":"cash","17":"cash"}',json.dumps({'ranges':ranges}),'token','manager@test','A-valid-long-password!',cash_paid))

    def test_approval_record_and_explicit_cash_payment(self):
        with self.factory() as s:
            s.get(User,1).password_hash=hash_password('A-valid-long-password!');s.scalar(select(TipStoreSettings)).require_split_approval=True;s.commit()
        with patch.object(poynt,'get_current_organization_id',return_value=1),patch.object(poynt,'get_organization_role',return_value='manager'),patch.object(poynt,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')),patch.object(poynt,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders)):
            self.submit_approved(True)
        with self.factory() as s:
            row=s.scalar(select(TipSubmission));self.assertEqual(row.approved_by_user_id,1);self.assertEqual(len(row.approval_digest),64)
            self.assertEqual(row.processing_status,'paid');self.assertTrue(all(p.status=='paid' for p in s.scalars(select(TipEmployeePayout))))

    def test_required_approval_rejected_without_credentials_before_fetch(self):
        with self.factory() as s:s.scalar(select(TipStoreSettings)).require_split_approval=True;s.commit()
        with patch.object(poynt,'get_current_organization_id',return_value=1),patch.object(poynt,'get_organization_role',return_value='manager'),patch.object(poynt,'fetch_poynt_orders',new=AsyncMock()) as fetch:
            with self.assertRaises(HTTPException):ClaimTipsTests.submit(self)
            fetch.assert_not_awaited()

    def test_approval_does_not_implicitly_mark_cash_paid(self):
        with self.factory() as s:
            s.get(User,1).password_hash=hash_password('A-valid-long-password!');s.scalar(select(TipStoreSettings)).require_split_approval=True;s.commit()
        with patch.object(poynt,'get_current_organization_id',return_value=1),patch.object(poynt,'get_organization_role',return_value='manager'),patch.object(poynt,'get_poynt_credentials',return_value=SimpleNamespace(business_id='business')),patch.object(poynt,'fetch_poynt_orders',new=AsyncMock(return_value=self.orders)):
            self.submit_approved(False)
        with self.factory() as s:
            self.assertEqual(s.scalar(select(TipSubmission)).processing_status,'pending')
            self.assertTrue(all(p.status=='pending' for p in s.scalars(select(TipEmployeePayout))))

    def test_migration_roundtrip_preserves_existing_submission(self):
        spec=importlib.util.spec_from_file_location('tip_approval_migration','alembic/versions/f9c4ea60b238_tip_history_approval.py')
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE tip_store_settings (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE tip_submissions (id INTEGER PRIMARY KEY, total_tip_cents INTEGER)')
            connection.exec_driver_sql('INSERT INTO tip_submissions VALUES (1,302)')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade();migration.downgrade();migration.upgrade()
                self.assertIn('approved_at',{c['name'] for c in inspect(connection).get_columns('tip_submissions')})
            self.assertEqual(connection.scalar(text('SELECT total_tip_cents FROM tip_submissions')),302)

    def test_filtered_totals_match_list_and_render_read_only_views(self):
        from jinja2 import Environment, FileSystemLoader
        self.add_submission('store-a',151)
        with self.factory() as s:s.get(Employee,11).user_id=2;s.commit()
        env=Environment(loader=FileSystemLoader('templates'))
        terminal=self.history(user=3,role='member',store_assignment_id=2,store_session_version=1)
        html=env.get_template('tip_submissions.html').render(**terminal)
        self.assertNotIn('Confirm paid',html);self.assertNotIn('Reject unpaid',html);self.assertNotIn('id="tip-report-store"',html)
        self.assertNotIn('id="tip-report-start"',html)
        employee=self.history(user=2,role='member');html=env.get_template('employee_tip_history.html').render(**employee)
        self.assertIn('Tessa',html);self.assertNotIn('Confirm paid',html)


if __name__=='__main__':unittest.main()
