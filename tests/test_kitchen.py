"""Isolated SQLite integration tests; no provider or production credentials."""
import asyncio
import importlib.util
import re
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from sqlalchemy import select, func, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_live_dashboard import DatabaseAndRoutesTests, sale
from kitchen_models import KitchenTicket, KitchenAction
from kitchen_service import sync_ticket, transition, lines
from live_dashboard_models import DashboardOrder
from live_dashboard_service import save_order
from models import User, OrganizationMember
from routers import kitchen
from store_time import utc_now


class KitchenTests(unittest.TestCase):
    def setUp(self):
        DatabaseAndRoutesTests.setUp(self)
        self.kitchen_patch=patch("routers.kitchen.SessionLocal",self.factory)
        self.kitchen_patch.start()
        self.client.get("/test-login")
        page=self.client.get("/dashboard/stores/1")
        self.csrf=re.search(r'data-csrf="([^"]+)"',page.text).group(1)
        self.now=utc_now()

    def tearDown(self):
        self.kitchen_patch.stop()
        DatabaseAndRoutesTests.tearDown(self)

    def seed(self, oid="one", at=None, items=None, **extra):
        payload=sale(at or self.now,order_id=oid,items=items or [
            {"id":"bar","name":"Chocolate bar","sku":"BAR-CUSTOM","quantity":2,"status":"FULFILLED"},
            {"id":"drink","name":"Drink","sku":"DRINK-CUSTOM","quantity":1}],**extra)
        with self.factory() as session:
            save_order(session,1,"business",payload)
            session.commit()
        return payload

    def queue(self):
        response=self.client.get("/dashboard/stores/1/kitchen")
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def action(self,ticket,action,key=None,csrf=None,store=1):
        return self.client.post(f"/dashboard/stores/{store}/kitchen/{ticket['id']}",
            headers={"X-Kitchen-CSRF":self.csrf if csrf is None else csrf},
            json={"revision":ticket["revision"],"action":action,"item_key":key})

    def test_matching_flow_windows_partial_completion_and_undo(self):
        self.seed()
        data=self.queue()
        self.assertEqual(data["flow"]["window_minutes"],15)
        self.assertEqual(data["flow"]["intake"]["recent_items"],3)
        self.assertEqual(data["flow"]["intake"]["items_per_minute"],3/15)
        ticket=data["active"][0]
        self.action(ticket,"done",ticket["items"][0]["key"])
        data=self.queue()
        self.assertEqual(data["flow"]["completion"]["completed_items"],2)
        self.assertEqual(data["flow"]["completion"]["items_per_minute"],2/15)
        ticket=data["active"][0]
        self.action(ticket,"done")
        data=self.queue()
        self.assertEqual(data["flow"]["completion"]["completed_items"],3)
        self.action(data["recent"][0],"undo")
        self.assertEqual(self.queue()["flow"]["completion"]["completed_items"],0)

    def test_completion_window_uses_done_time_and_quantity(self):
        def item(minutes,qty,state="done"):
            return {"state":state,"quantity":qty,"done_at":(self.now-timedelta(minutes=minutes)).isoformat()}
        ticket=SimpleNamespace(state="active",items=[item(14,4),item(16,9),item(-1,8),item(1,5,"claimed")])
        cancelled=SimpleNamespace(state="cancelled",items=[item(1,20)])
        result=kitchen.completion_rate([ticket,cancelled],self.now)
        self.assertEqual(result["completed_items"],4)
        self.assertEqual(result["items_per_minute"],4/15)

    def test_item_claim_complete_release_and_undo(self):
        self.seed()
        ticket=self.queue()["active"][0]
        key=ticket["items"][0]["key"]
        self.assertEqual(self.action(ticket,"claim",key).status_code,200)
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["items"][0]["state"],"claimed")
        self.assertIsNotNone(ticket["items"][0]["claimed_at"])
        self.assertEqual(self.action(ticket,"release",key).status_code,200)
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["items"][0]["state"],"available")
        self.assertEqual(self.action(ticket,"done",key).status_code,200)
        ticket=self.queue()["active"][0]
        self.assertEqual(self.action(ticket,"undo",key).status_code,200)
        self.assertEqual(self.queue()["active"][0]["items"][0]["state"],"available")

    def test_claim_order_preserves_other_claims_and_completion_is_ready(self):
        self.seed();ticket=self.queue()["active"][0]
        self.action(ticket,"claim",ticket["items"][0]["key"])
        ticket=self.queue()["active"][0];claimed_at=ticket["items"][0]["claimed_at"]
        self.assertEqual(self.action(ticket,"claim").status_code,200)
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["items"][0]["claimed_at"],claimed_at)
        self.assertEqual(self.action(ticket,"done").status_code,200)
        data=self.queue();self.assertEqual(data["active"],[])
        ready=data["recent"][0];self.assertEqual(ready["state"],"ready")
        self.assertIsNotNone(ready["ready_at"])
        self.assertEqual(self.action(ready,"undo").status_code,200)
        self.assertTrue(all(i["state"]=="available" for i in self.queue()["active"][0]["items"]))
        with self.factory() as session:
            pos=session.scalar(select(DashboardOrder))
            self.assertEqual(pos.payload["statuses"]["transactionStatusSummary"],"COMPLETED")
            self.assertGreater(session.scalar(select(func.count(KitchenAction.id))),3)

    def test_stale_revision_and_atomic_compare_and_swap(self):
        self.seed();ticket=self.queue()["active"][0]
        self.assertEqual(self.action(ticket,"claim",ticket["items"][0]["key"]).status_code,200)
        self.assertEqual(self.action(ticket,"claim",ticket["items"][0]["key"]).status_code,409)
        # Two independently loaded snapshots: only the first CAS can update.
        with self.factory() as a,self.factory() as b:
            first=a.get(KitchenTicket,ticket["id"]);second=b.get(KitchenTicket,ticket["id"])
            expected=first.revision
            transition(a,first,"done",None,expected,1);a.commit()
            with self.assertRaises(Exception) as error:transition(b,second,"done",None,expected,1)
            self.assertEqual(error.exception.status_code,409)

    def test_pos_quantity_change_and_new_items_reopen_ready_order(self):
        payload=self.seed();ticket=self.queue()["active"][0];self.action(ticket,"done")
        payload["items"][0]["quantity"]=3
        payload["updatedAt"]=(self.now+timedelta(seconds=1)).isoformat()
        with self.factory() as s:save_order(s,1,"business",payload);s.commit()
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["items"][0]["state"],"available")
        self.assertEqual(ticket["items"][1]["state"],"done")
        self.assertEqual(ticket["items"][0]["quantity"],3)
        payload["items"].reverse()
        with self.factory() as s:save_order(s,1,"business",payload);s.commit()
        states={i["sku"]:i["state"] for i in self.queue()["active"][0]["items"]}
        self.assertEqual(states["DRINK-CUSTOM"],"done")

    def test_upgrading_cached_item_ids_preserves_claims(self):
        payload=self.seed(items=[{"name":"Bar","sku":"BAR","quantity":1}])
        ticket=self.queue()["active"][0]
        self.action(ticket,"claim",ticket["items"][0]["key"])
        payload["items"][0]["id"]="provider-line-id"
        with self.factory() as s:save_order(s,1,"business",payload);s.commit()
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["items"][0]["state"],"claimed")
        payload["items"][0]["sku"]="DRINK"
        with self.factory() as s:save_order(s,1,"business",payload);s.commit()
        self.assertEqual(self.queue()["active"][0]["items"][0]["state"],"available")

    def test_cancelled_and_open_orders_and_returned_rows(self):
        self.seed("open",statuses={"transactionStatusSummary":"OPEN"})
        self.assertEqual(self.queue()["active"],[])
        payload=self.seed();ticket=self.queue()["active"][0]
        payload["statuses"]={"status":"CANCELLED","transactionStatusSummary":"COMPLETED"}
        with self.factory() as s:save_order(s,1,"business",payload);s.commit()
        self.assertEqual(self.queue()["active"],[])
        self.assertEqual(self.action(ticket,"done").status_code,409)
        self.assertEqual(lines({"items":[{"quantity":2,"status":"RETURNED"},{"quantity":0}]}),[])

    def test_more_than_twenty_oldest_first_and_prior_day_remains(self):
        for i in range(25):self.seed(str(i),at=self.now-timedelta(seconds=i))
        data=self.queue();self.assertEqual(len(data["active"]),25)
        self.assertEqual(data["active"][0]["number"],"24")
        ticket=data["active"][0]
        with self.factory() as s:
            t=s.get(KitchenTicket,ticket["id"]);t.created_at=self.now-timedelta(days=1);s.commit()
        self.assertEqual(len(self.queue()["active"]),25)
        self.seed("historical",at=self.now-timedelta(days=2))
        self.assertEqual(len(self.queue()["active"]),25)

    def test_csrf_and_store_and_role_authorization(self):
        self.seed();ticket=self.queue()["active"][0]
        self.assertEqual(self.action(ticket,"done",csrf="wrong").status_code,403)
        self.assertEqual(self.action(ticket,"done",store=2).status_code,404)
        for role in ("member","payroll"):
            with self.factory() as s:s.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1)).role=role;s.commit()
            self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,403)
            self.assertEqual(self.action(ticket,"done").status_code,403)
        self.client.post("/logout")
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,401)

    def test_display_home_privacy_and_assignment_enforced(self):
        DatabaseAndRoutesTests.provision_display(self)
        self.client.post("/logout")
        self.client.post("/login/store-display",data={"organization_code":"ftw-1","username":"truck-screen","password":"screen-password-123"})
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,403)
        home=self.client.get("/dashboard/stores/1/home")
        token=re.search(r'name="csrf_token" value="([^"]+)"',home.text).group(1)
        self.client.post("/dashboard/stores/1/show",data={"csrf_token":token})
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,200)
        self.assertEqual(self.client.get("/dashboard/stores/2/kitchen").status_code,403)
        self.client.get("/dashboard/stores/1/home")
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,403)
        with self.factory() as s:
            user=s.scalar(select(User).where(User.account_type=="store_display"));user.is_active=False;s.commit()
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen",follow_redirects=False).status_code,401)

    def test_sse_observes_other_session_commits_and_revocation(self):
        self.seed()
        request=SimpleNamespace(session={"user_id":1,"organization_id":1},is_disconnected=AsyncMock(return_value=False))
        async def run():
            response=await kitchen.events(request,1)
            stream=response.body_iterator
            self.assertIn("retry",await anext(stream))
            self.assertIn("queue-changed",await anext(stream))
            with self.factory() as s:
                t=s.scalar(select(KitchenTicket));transition(s,t,"claim",None,t.revision,1);s.commit()
            self.assertIn("queue-changed",await anext(stream))
            with self.factory() as s:s.get(User,1).is_active=False;s.commit()
            self.assertIn("access-denied",await anext(stream))
            await stream.aclose()
        with patch("routers.kitchen.asyncio.sleep",new=AsyncMock()):asyncio.run(run())

    def test_migration_roundtrip(self):
        module_spec=importlib.util.spec_from_file_location("kitchen_migration","alembic/versions/f36b8d14c025_kitchen_queue.py")
        migration=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(migration)
        KitchenAction.__table__.drop(self.engine);KitchenTicket.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn("kitchen_tickets",inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn("kitchen_tickets",inspect(connection).get_table_names())

if __name__=="__main__":unittest.main()
