"""Run with DATABASE_URL=sqlite:///:memory: python -m unittest discover -s tests -p test_live_dashboard.py."""
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from alembic.migration import MigrationContext
from alembic.operations import Operations
from database import Base
from models import User, Organization, OrganizationMember, OrganizationStore, PoyntConnection
from live_dashboard_models import DashboardOrder, DashboardNotification, DashboardSync, DashboardDay
from live_dashboard_metrics import summarize, money, instant, order_store, categories, store_details
from live_dashboard_service import save_order, acquire, release, process_organization
from routers import live_dashboard as routes
from store_time import local_day_bounds, utc_now
from zoneinfo import ZoneInfo

UTC = timezone.utc


def sale(at, order_id="a", amount=1083, store="truck", **changes):
    order = {"id": order_id, "createdAt": at.isoformat(), "updatedAt": at.isoformat(),
        "context": {"storeId": store}, "statuses": {"transactionStatusSummary": "COMPLETED"},
        "amounts": {"netTotal": amount, "taxTotal": 83, "currency": "USD", "capturedTotals": {"orderAmount": amount, "tipAmount": 200}},
        "items": [{"sku": "BAR-CUSTOM", "quantity": 1, "unitPrice": 1000, "status": "FULFILLED"}]}
    order.update(changes)
    return order


def store(zone="America/Phoenix"):
    return SimpleNamespace(id=1, store_id="truck", display_name="Our Truck", poynt_name="Truck", timezone_name=zone)


class MetricsTests(unittest.TestCase):
    def test_store_feed_caps_latest_orders_and_sku_counts_only_completed_sales(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        orders=[sale(now-timedelta(minutes=i),order_id=str(i)) for i in range(25)]
        orders.append(sale(now,order_id="open",statuses={"transactionStatusSummary":"OPEN"},items=[{"sku":"BAR-CUSTOM","quantity":100}]))
        orders.append(sale(now-timedelta(days=1),order_id="old"))
        result=store_details(orders,now-timedelta(hours=13),now+timedelta(hours=11),now)
        self.assertEqual(len(result["latest_orders"]),20)
        self.assertEqual(result["latest_orders"][0]["id"],"open")
        self.assertEqual(result["sku_counts"][0]["quantity"],25)

    def test_item_count_uses_quantities_and_excludes_returns_and_other_days(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        order=sale(now,items=[{"quantity":3,"status":"FULFILLED"},
                              {"quantity":2.5,"status":"FULFILLED"},
                              {"quantity":4,"status":"RETURNED"}])
        result=summarize(store(),[order,sale(now-timedelta(days=1))],now)
        self.assertEqual(result["order_count"],1)
        self.assertEqual(result["item_count"],5.5)
        self.assertEqual(summarize(store(),[],now)["item_count"],0)

    def test_money_includes_tax_and_excludes_tips(self):
        self.assertEqual(money(sale(datetime.now(UTC))), (1083, 200))

    def test_tax_included_consistently_in_every_sales_metric(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        o=sale(now);o["amounts"]["refundedTotals"]={"orderAmount":100,"tipAmount":50}
        result=summarize(store(),[o],now)
        self.assertEqual(result["sales_cents"],983)
        self.assertEqual(result["average_sale_cents"],983)
        self.assertEqual(result["recent_sales_cents"],983)
        self.assertEqual(sum(h["sales_cents"] for h in result["hourly"]),983)
        self.assertEqual(sum(c["sales_cents"] for c in result["categories"]),983)
        self.assertEqual(result["tips_cents"],150)

    def test_refund_allocation_and_tips(self):
        o = sale(datetime.now(UTC))
        o["amounts"]["refundedTotals"] = {"orderAmount": 542, "tipAmount": 100}
        self.assertEqual(money(o), (541, 100))
        o["amounts"]["refundedTotals"] = {"orderAmount": 1083, "tipAmount": 200}
        self.assertEqual(money(o), (0, 0))

    def test_category_discount_allocation_reconciles(self):
        o = sale(datetime.now(UTC));o["items"].append({"sku":"unknown","quantity":2,"unitPrice":333})
        rows = categories(o, 1251)
        self.assertEqual(sum(r["sales_cents"] for r in rows.values()),1251)
        self.assertEqual(rows["Uncategorized"]["quantity"],2)
        self.assertIn("Ice Cream Bars",rows)

    def test_empty_items_and_returns(self):
        self.assertEqual(categories({"items":[]},123)["Uncategorized"]["sales_cents"],123)
        o=sale(datetime.now(UTC));o["items"][0]["status"]="RETURNED"
        self.assertEqual(categories(o,0)["Uncategorized"]["quantity"],0)

    def test_no_sales_and_cancelled_orders(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        result=summarize(store(),[sale(now,statuses={"status":"CANCELLED","transactionStatusSummary":"COMPLETED"})],now)
        self.assertEqual(result["order_count"],0);self.assertEqual(result["pace"],"Quiet right now")
        self.assertIsNone(result["last_sale_at"])

    def test_day_boundaries_and_overnight_recent_activity(self):
        now=datetime(2026,10,5,7,2,tzinfo=UTC)
        orders=[sale(datetime(2026,10,5,6,59,tzinfo=UTC),"yesterday"),sale(datetime(2026,10,5,7,tzinfo=UTC),"today"),
                sale(datetime(2026,10,6,7,tzinfo=UTC),"tomorrow")]
        r=summarize(store(),orders,now)
        self.assertEqual(r["order_count"],1);self.assertEqual(r["recent_orders"],2)
        self.assertEqual(r["date"],"2026-10-05");self.assertEqual(r["hourly"][0]["orders"],1)

    def test_recent_boundary_and_full_baseline_zeros(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        orders=[sale(now-timedelta(minutes=70),"old"),sale(now-timedelta(minutes=10),"base"),
                sale(now-timedelta(minutes=5),"edge"),sale(now,"now")]
        r=summarize(store(),orders,now)
        self.assertEqual(r["recent_orders"],2);self.assertEqual(r["baseline_orders"],1)
        self.assertEqual(r["pace_ratio"],24);self.assertEqual(r["pace"],"Busy")

    def test_starting_baseline_and_zero_baseline(self):
        now=datetime(2026,10,5,20,tzinfo=UTC)
        self.assertEqual(summarize(store(),[sale(now)],now)["pace"],"Building baseline")
        self.assertEqual(summarize(store(),[sale(now-timedelta(hours=2),"old"),sale(now)],now)["pace"],"Activity starting")

    def test_five_minute_values_decay_without_new_orders(self):
        now=datetime(2026,10,5,20,tzinfo=UTC);orders=[sale(now)]
        self.assertEqual(summarize(store(),orders,now+timedelta(minutes=6))["recent_orders"],0)

    def test_store_local_dates_differ_for_same_instant(self):
        now=datetime(2026,10,5,6,30,tzinfo=UTC);orders=[sale(now)]
        phoenix=summarize(store(),orders,now);ny=summarize(store("America/New_York"),orders,now)
        self.assertEqual(phoenix["date"],"2026-10-04");self.assertEqual(ny["date"],"2026-10-05")
        self.assertEqual(phoenix["hourly"][-1]["at"],ny["hourly"][-1]["at"])

    def test_dst_23_and_25_hours_and_repeated_hour_labels(self):
        for day,count in [(date(2026,3,8),23),(date(2026,11,1),25)]:
            start,end=local_day_bounds(day,ZoneInfo("America/New_York"));now=end-timedelta(seconds=1)
            r=summarize(store("America/New_York"),[sale(start),sale(end)],now)
            self.assertEqual(len(r["hourly"]),count);self.assertEqual(r["order_count"],1)
            if count==25:
                self.assertEqual([h["label"] for h in r["hourly"]][1:3],["1 AM EDT","1 AM EST"])

    def test_naive_timestamp_rejected_and_offsets_normalized(self):
        with self.assertRaises(ValueError):instant("2026-10-05T12:00:00")
        self.assertEqual(instant("2026-10-05T12:00:00-07:00").hour,19)

    def test_store_resolution_multitender_is_one_store(self):
        self.assertEqual(order_store({"transactions":[{"context":{"storeId":"a"}},{"context":{"storeId":"a"}}]}),"a")
        self.assertIsNone(order_store({"transactions":[{"context":{"storeId":"a"}},{"context":{"storeId":"b"}}]}))

    def test_non_usd_excluded(self):
        now=datetime.now(UTC);o=sale(now);o["amounts"]["currency"]="EUR"
        r=summarize(store(),[o],now);self.assertEqual(r["order_count"],0);self.assertTrue(r["currency_warning"])

    def test_completed_refunded_and_pending_orders(self):
        now=datetime.now(UTC)
        refunded=sale(now,"refunded",statuses={"transactionStatusSummary":"REFUNDED"})
        refunded["amounts"]["refundedTotals"]={"orderAmount":1083,"tipAmount":200}
        pending=sale(now,"pending",statuses={"transactionStatusSummary":"PENDING"})
        r=summarize(store(),[sale(now),refunded,pending],now)
        self.assertEqual(r["order_count"],2);self.assertEqual(r["sales_cents"],1083);self.assertEqual(r["tips_cents"],200)

    def test_historical_day_has_no_live_metrics_and_all_hours_complete(self):
        start,end=local_day_bounds(date(2026,10,4),ZoneInfo("America/Phoenix"))
        result=summarize(store(),[sale(start+timedelta(hours=12)),sale(end)],end-timedelta(microseconds=1),historical=True)
        self.assertEqual(result["date"],"2026-10-04");self.assertEqual(result["order_count"],1)
        self.assertEqual(len(result["hourly"]),24);self.assertFalse(any(h["partial"] for h in result["hourly"]))
        for name in ("recent_orders","recent_sales_cents","pace","pace_ratio","baseline_orders"):
            self.assertIsNone(result[name])
        self.assertEqual(result["activity"],[])


class DatabaseAndRoutesTests(unittest.TestCase):
    def setUp(self):
        # SQLite loses offsets by default. Restore its known UTC storage values
        # at the test driver boundary; production UTCInstant remains strict.
        sqlite3.register_converter("DATETIME",lambda raw:datetime.fromisoformat(raw.decode()).replace(tzinfo=UTC).isoformat())
        sqlite3.register_converter("DATE",lambda raw:raw.decode())
        self.engine=create_engine("sqlite://",connect_args={"check_same_thread":False,"detect_types":sqlite3.PARSE_DECLTYPES},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,autoflush=False)
        self.patches=[patch(name,self.factory) for name in ["routers.live_dashboard.SessionLocal","live_dashboard_service.SessionLocal",
            "poynt.connection.SessionLocal","organization_context.SessionLocal","permissions.SessionLocal"]]
        for p in self.patches:p.start()
        self.env=patch.dict(os.environ,{"POYNT_APP_ID":"app","POYNT_WEBHOOK_SECRET":"s"*40,"POYNT_WEBHOOK_URL":"https://example.test/webhooks/poynt/orders"});self.env.start()
        now=utc_now()
        with self.factory() as s:
            s.add_all([Organization(id=1,name="One"),Organization(id=2,name="Two"),User(id=1,email="one@example.test",password_hash="unused")]);s.flush()
            s.add_all([OrganizationMember(organization_id=1,user_id=1,role="owner"),
                PoyntConnection(organization_id=1,business_id="business",access_token="unused",expires_at=now+timedelta(days=1)),
                OrganizationStore(organization_id=1,store_id="truck",poynt_name="Truck",timezone_name="America/Phoenix"),
                OrganizationStore(organization_id=2,store_id="truck",poynt_name="Other",timezone_name="America/Phoenix"),
                DashboardSync(organization_id=1,business_id="business",last_reconciled_at=now,next_reconcile_at=now+timedelta(minutes=5))]);s.commit()
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key="test-secret");app.include_router(routes.router)
        @app.get("/test-login")
        def login(request:Request):request.session.update(user_id=1,organization_id=1);return {}
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.env.stop()
        for p in self.patches:p.stop()
        self.engine.dispose()

    def test_single_store_page_and_data_enforce_organization_scope(self):
        self.assertEqual(self.client.get("/dashboard/stores/1").status_code,401)
        self.client.get("/test-login")
        self.assertEqual(self.client.get("/dashboard/stores/2").status_code,404)
        self.assertEqual(self.client.get("/dashboard/data?store_id=2").status_code,404)
        response=self.client.get("/dashboard/stores/1")
        self.assertEqual(response.status_code,200)
        self.assertIn('data-store-id="1"',response.text)
        with self.factory() as session:
            o=sale(utc_now(),orderNumber="42",items=[{"name":"Chocolate bar","sku":"BAR-CUSTOM","quantity":2,"status":"FULFILLED"}])
            save_order(session,1,"business",o);session.commit()
        data=self.client.get("/dashboard/data?store_id=1").json()
        self.assertEqual(len(data["stores"]),1)
        self.assertEqual(data["stores"][0]["latest_orders"][0]["number"],"42")
        self.assertEqual(data["stores"][0]["latest_orders"][0]["items"][0]["name"],"Chocolate bar")
        self.assertNotIn("latest_orders",self.client.get("/dashboard/data").json()["stores"][0])

    def test_unauthenticated_and_tenant_scoping(self):
        self.assertEqual(self.client.get("/dashboard/data").status_code,401)
        self.client.get("/test-login")
        with self.factory() as s:
            save_order(s,2,"business",sale(utc_now()));s.commit()
        data=self.client.get("/dashboard/data").json()
        self.assertEqual(len(data["stores"]),1);self.assertEqual(data["stores"][0]["order_count"],0)

    def test_no_connection_setup_and_inactive_store(self):
        self.client.get("/test-login")
        with self.factory() as s:
            st=s.scalar(select(OrganizationStore).where(OrganizationStore.organization_id==1));st.timezone_name=None;s.commit()
        self.assertTrue(self.client.get("/dashboard/data").json()["stores"][0]["setup_required"])
        with self.factory() as s:
            st=s.scalar(select(OrganizationStore).where(OrganizationStore.organization_id==1));st.is_active=False;s.commit()
        self.assertEqual(self.client.get("/dashboard/data").json()["stores"],[])
        with self.factory() as s:
            s.delete(s.scalar(select(PoyntConnection)));s.commit()
        self.assertFalse(self.client.get("/dashboard/data").json()["connected"])

    def test_upsert_stale_replay_and_customer_not_retained(self):
        now=utc_now();o=sale(now);o["customer"]={"email":"private@example.test"}
        with self.factory() as s:
            save_order(s,1,"business",o);save_order(s,1,"business",o);s.commit()
            updated=sale(now,amount=2083);updated["updatedAt"]=(now+timedelta(seconds=1)).isoformat()
            save_order(s,1,"business",updated);save_order(s,1,"business",o);s.commit()
            rows=s.scalars(select(DashboardOrder)).all();self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].payload["amounts"]["netTotal"],2083);self.assertNotIn("customer",rows[0].payload)

    def signed(self,data):
        raw=json.dumps(data).encode();sig=base64.b64encode(hmac.new(b"s"*40,raw,hashlib.sha1).digest()).decode()
        return self.client.post("/webhooks/poynt/orders",content=raw,headers={"Poynt-Webhook-Signature":sig})

    def notification(self,**changes):
        data={"id":"notification","businessId":"business","resourceId":"a","hookId":"hook",
            "applicationId":"app","resource":"/orders","eventType":"ORDER_COMPLETED"};data.update(changes);return data

    def test_webhook_signature_duplicate_and_durable_processing(self):
        self.assertEqual(self.client.post("/webhooks/poynt/orders",json=self.notification()).status_code,401)
        self.assertEqual(self.signed(self.notification()).status_code,200)
        self.assertEqual(self.signed(self.notification()).status_code,200)
        with self.factory() as s:self.assertEqual(s.scalar(select(func.count()).select_from(DashboardNotification)),1)
        business,token=acquire(1)
        with patch("poynt.client.PoyntClient.get_order",new=AsyncMock(return_value=sale(utc_now()))):
            asyncio.run(process_organization(1,business,token))
        release(1,token)
        with self.factory() as s:
            self.assertIsNotNone(s.scalar(select(DashboardNotification)).processed_at)
            self.assertEqual(s.scalar(select(func.count()).select_from(DashboardOrder)),1)

    def test_signed_wrong_app_hook_and_cross_business(self):
        self.assertEqual(self.signed(self.notification(applicationId="other")).status_code,400)
        self.assertEqual(self.signed(self.notification(businessId="unknown")).status_code,409)
        with self.factory() as s:s.get(DashboardSync,1).hook_id="expected";s.commit()
        self.assertEqual(self.signed(self.notification()).status_code,403)

    def test_lease_exclusion_release_and_expired_worker_guard(self):
        business,token=acquire(1);self.assertIsNone(acquire(1));release(1,"wrong");self.assertIsNone(acquire(1))
        release(1,token);self.assertIsNotNone(acquire(1))

    def test_expired_worker_cannot_save(self):
        self.signed(self.notification());business,token=acquire(1)
        with self.factory() as s:s.get(DashboardSync,1).lease_until=utc_now()-timedelta(seconds=1);s.commit()
        with patch("poynt.client.PoyntClient.get_order",new=AsyncMock(return_value=sale(utc_now()))):
            asyncio.run(process_organization(1,business,token))
        with self.factory() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(DashboardOrder)),0)
            self.assertIsNone(s.scalar(select(DashboardNotification)).processed_at)

    def test_failed_notification_is_retained_for_retry(self):
        self.signed(self.notification());business,token=acquire(1)
        with patch("poynt.client.PoyntClient.get_order",new=AsyncMock(side_effect=RuntimeError("failure"))):
            asyncio.run(process_organization(1,business,token))
        release(1,token)
        with self.factory() as s:
            row=s.scalar(select(DashboardNotification));self.assertIsNone(row.processed_at);self.assertGreater(row.retry_at,utc_now())

    def test_reconciliation_corrections_and_bounds(self):
        now=utc_now()
        with self.factory() as s:s.get(DashboardSync,1).next_reconcile_at=now;s.commit()
        business,token=acquire(1);mock=AsyncMock(return_value=[sale(now)])
        with patch("poynt.client.PoyntClient.get_recent_orders",new=mock):asyncio.run(process_organization(1,business,token))
        release(1,token)
        bounds=mock.call_args.kwargs;self.assertTrue(bounds["fetch_all"]);self.assertTrue(bounds["start_at"].endswith("Z"))
        with self.factory() as s:self.assertIsNotNone(s.get(DashboardSync,1).last_reconciled_at)

    def test_enable_manager_auth_and_registration(self):
        self.assertEqual(self.client.post("/dashboard/webhook/enable").status_code,401)
        self.client.get("/test-login")
        self.assertEqual(self.client.post("/dashboard/webhook/enable").status_code,403)
        with patch("poynt.client.PoyntClient.register_order_webhook",new=AsyncMock(return_value={"id":"registered"})) as mock:
            response=self.client.post("/dashboard/webhook/enable",headers={"X-Requested-With":"FoodTruckWorks"})
            self.assertEqual(response.status_code,200)
            self.client.post("/dashboard/webhook/enable",headers={"X-Requested-With":"FoodTruckWorks"})
            self.assertEqual(mock.await_count,1)
        with self.factory() as s:
            s.scalar(select(OrganizationMember)).role="member";s.commit()
        self.assertEqual(self.client.post("/dashboard/webhook/enable",headers={"X-Requested-With":"FoodTruckWorks"}).status_code,403)

    def test_missing_schema_returns_actionable_503(self):
        self.client.get("/test-login");DashboardOrder.__table__.drop(self.engine)
        response=self.client.get("/dashboard/data");self.assertEqual(response.status_code,503);self.assertIn("migration",response.json()["detail"])

    def test_historical_backfill_full_day_no_live_and_cache_reuse(self):
        self.client.get("/test-login")
        day=utc_now().astimezone(ZoneInfo("America/Phoenix")).date()-timedelta(days=1)
        url="/dashboard/data?date="+day.isoformat()
        initial=self.client.get(url).json();self.assertTrue(initial["historical_view"]);self.assertTrue(initial["initializing"])
        start,end=local_day_bounds(day,ZoneInfo("America/Phoenix"))
        business,token=acquire(1);mock=AsyncMock(return_value=[sale(start+timedelta(hours=12)),sale(end,"next_day")])
        with patch("poynt.client.PoyntClient.get_recent_orders",new=mock):asyncio.run(process_organization(1,business,token))
        release(1,token)
        self.assertEqual(mock.call_args.kwargs["start_at"],start.isoformat().replace("+00:00","Z"))
        self.assertEqual(mock.call_args.kwargs["end_at"],end.isoformat().replace("+00:00","Z"))
        result=self.client.get(url).json();self.assertFalse(result["polling_needed"]);self.assertFalse(result["initializing"])
        self.assertEqual(result["stores"][0]["sales_cents"],1083);self.assertIsNone(result["stores"][0]["pace"])
        self.assertEqual(result["stores"][0]["order_count"],1)
        self.client.get(url)
        with self.factory() as s:self.assertEqual(s.scalar(select(func.count()).select_from(DashboardDay)),1)
        live=self.client.get("/dashboard/data").json();self.assertFalse(live["historical_view"]);self.assertTrue(live["polling_needed"])

    def test_history_date_validation_and_yesterday_timezone(self):
        self.client.get("/test-login")
        for value in ("bad","2026-02-30","20261004",(utc_now().date()+timedelta(days=3)).isoformat(),(utc_now().date()-timedelta(days=100)).isoformat()):
            self.assertEqual(self.client.get("/dashboard/data?date="+value).status_code,400)
        result=self.client.get("/dashboard/data?date=yesterday").json()
        expected=utc_now().astimezone(ZoneInfo("America/Phoenix")).date()-timedelta(days=1)
        self.assertEqual(result["stores"][0]["date"],expected.isoformat())
        self.assertTrue(result["historical_view"])

    def test_historical_error_is_separate_from_today(self):
        self.client.get("/test-login");self.client.get("/dashboard/data?date=yesterday")
        business,token=acquire(1)
        with patch("poynt.client.PoyntClient.get_recent_orders",new=AsyncMock(side_effect=RuntimeError("offline"))):
            asyncio.run(process_organization(1,business,token))
        release(1,token)
        history=self.client.get("/dashboard/data?date=yesterday").json();self.assertIn("Historical",history["error"])
        live=self.client.get("/dashboard/data").json();self.assertIsNone(live["error"])

    def test_historical_multi_timezone_fetch_and_per_store_bounds(self):
        self.client.get("/test-login")
        day=utc_now().astimezone(ZoneInfo("America/Phoenix")).date()-timedelta(days=1)
        with self.factory() as s:
            s.add(OrganizationStore(organization_id=1,store_id="east",poynt_name="East",timezone_name="America/New_York"));s.commit()
        self.client.get("/dashboard/data?date="+day.isoformat())
        phoenix=local_day_bounds(day,ZoneInfo("America/Phoenix"));east=local_day_bounds(day,ZoneInfo("America/New_York"))
        orders=[sale(phoenix[0]+timedelta(hours=12),"west",store="truck"),sale(east[0],"east",store="east"),
                sale(east[0]-timedelta(seconds=1),"outside",store="east")]
        business,token=acquire(1);mock=AsyncMock(return_value=orders)
        with patch("poynt.client.PoyntClient.get_recent_orders",new=mock):asyncio.run(process_organization(1,business,token))
        release(1,token)
        self.assertEqual(mock.call_args.kwargs["start_at"],min(phoenix[0],east[0]).isoformat().replace("+00:00","Z"))
        self.assertEqual(mock.call_args.kwargs["end_at"],max(phoenix[1],east[1]).isoformat().replace("+00:00","Z"))
        data=self.client.get("/dashboard/data?date="+day.isoformat()).json()
        self.assertEqual([s["order_count"] for s in data["stores"]],[1,1])
        with self.factory() as s:self.assertEqual(s.scalar(select(func.count()).select_from(DashboardDay)),1)

    def test_historical_cache_survives_live_cleanup_after_three_days(self):
        self.client.get("/test-login")
        day=utc_now().astimezone(ZoneInfo("America/Phoenix")).date()-timedelta(days=7)
        self.client.get("/dashboard/data?date="+day.isoformat())
        start,end=local_day_bounds(day,ZoneInfo("America/Phoenix"))
        business,token=acquire(1)
        with patch("poynt.client.PoyntClient.get_recent_orders",new=AsyncMock(return_value=[sale(start+timedelta(hours=12))])):
            asyncio.run(process_organization(1,business,token))
        release(1,token)
        with self.factory() as s:s.get(DashboardSync,1).next_reconcile_at=utc_now();s.commit()
        business,token=acquire(1)
        with patch("poynt.client.PoyntClient.get_recent_orders",new=AsyncMock(return_value=[])):
            asyncio.run(process_organization(1,business,token))
        release(1,token)
        result=self.client.get("/dashboard/data?date="+day.isoformat()).json()
        self.assertEqual(result["stores"][0]["order_count"],1)


class MigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_isolated_database(self):
        engine=create_engine("sqlite:///:memory:")
        path="alembic/versions/b92d7a10e643_live_dashboard.py"
        spec=importlib.util.spec_from_file_location("dashboard_migration",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE organizations (id INTEGER PRIMARY KEY)")
            operations=Operations(MigrationContext.configure(connection))
            with patch.object(module,"op",operations):module.upgrade();module.downgrade()
        engine.dispose()

    def test_history_upgrade_downgrade_isolated_database(self):
        engine=create_engine("sqlite:///:memory:")
        path="alembic/versions/c03e5d9182a7_dashboard_history.py"
        spec=importlib.util.spec_from_file_location("history_migration",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE organizations (id INTEGER PRIMARY KEY)")
            operations=Operations(MigrationContext.configure(connection))
            with patch.object(module,"op",operations):module.upgrade();module.downgrade()
        engine.dispose()


if __name__=="__main__":unittest.main()
