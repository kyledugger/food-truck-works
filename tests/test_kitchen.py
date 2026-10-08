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

    def test_hold_resume_blocks_preparation_and_preserves_progress(self):
        self.seed()
        ticket=self.queue()["active"][0]
        self.assertEqual(self.action(ticket,"claim",ticket["items"][0]["key"]).status_code,200)
        ticket=self.queue()["active"][0]
        original=ticket["created_at"]
        self.assertEqual(self.action(ticket,"hold").status_code,200)
        held=self.queue()["active"][0]
        self.assertEqual(held["state"],"held")
        self.assertEqual(held["created_at"],original)
        self.assertEqual(held["items"][0]["state"],"claimed")
        for action,key in [("done",None),("claim",held["items"][1]["key"]),("release",held["items"][0]["key"]),("undo",None)]:
            self.assertEqual(self.action(held,action,key).status_code,409)
        self.seed(notes="New instruction")
        held=self.queue()["active"][0]
        self.assertEqual(held["state"],"held")
        self.assertEqual(self.action(held,"resume").status_code,200)
        resumed=self.queue()["active"][0]
        self.assertEqual(resumed["state"],"active")
        self.assertEqual(resumed["items"][0]["state"],"claimed")
        self.assertEqual(resumed["created_at"],original)
        self.assertEqual(self.action(held,"resume").status_code,409)
        self.assertEqual(self.action(resumed,"done").status_code,200)
        self.assertEqual(self.action(self.queue()["recent"][0],"hold").status_code,409)

    def test_timer_profile_shared_validated_and_owner_manager_only(self):
        self.assertEqual(self.queue()["timer_profile"],{"green_seconds":0,"yellow_seconds":300,"red_seconds":600})
        path="/dashboard/stores/1/kitchen-profile"
        profile={"green_seconds":0,"yellow_seconds":120,"red_seconds":240}
        self.assertEqual(self.client.post(path,json=profile).status_code,403)
        headers={"X-Kitchen-CSRF":self.csrf}
        request=SimpleNamespace(session={"user_id":1,"organization_id":1})
        before=kitchen.stamp(request,1)
        self.assertEqual(self.client.post(path,headers=headers,json=profile).status_code,200)
        self.assertNotEqual(kitchen.stamp(request,1),before)
        self.assertEqual(self.queue()["timer_profile"],profile)
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen-test").json()["timer_profile"],profile)
        self.assertEqual(self.client.post(path,headers=headers,json={**profile,"yellow_seconds":300,"red_seconds":200}).status_code,422)
        self.assertEqual(self.client.post("/dashboard/stores/2/kitchen-profile",headers=headers,json=profile).status_code,404)
        with self.factory() as session:
            session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1)).role="manager";session.commit()
        self.assertEqual(self.client.post(path,headers=headers,json=profile).status_code,200)
        DatabaseAndRoutesTests.provision_display(self)
        self.client.post("/logout")
        self.client.post("/login/store-display",data={"organization_code":"ftw-1","username":"truck-screen","password":"screen-password-123"})
        page=self.client.get("/dashboard/stores/1/kitchen-display")
        self.assertNotIn('id="timer-settings"',page.text)
        token=re.search(r'data-csrf="([^"]+)"',page.text).group(1)
        self.assertEqual(self.client.post(path,headers={"X-Kitchen-CSRF":token},json=profile).status_code,403)

    def test_timer_profile_migration_roundtrip(self):
        from kitchen_models import KitchenTimerProfile
        KitchenTimerProfile.__table__.drop(self.engine)
        spec=importlib.util.spec_from_file_location("timer_migration","alembic/versions/c69e1a47f358_kitchen_timer_profile.py")
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn("kitchen_timer_profiles",inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn("kitchen_timer_profiles",inspect(connection).get_table_names())

    def test_simulation_isolated_actions_clear_and_signature(self):
        self.seed()
        live=self.queue()["active"][0]
        path="/dashboard/stores/1/kitchen-test"
        headers={"X-Kitchen-CSRF":self.csrf}
        response=self.client.post(path+"/orders",headers=headers,json={"action":"add","count":10})
        self.assertEqual(response.status_code,200,response.text)
        data=self.client.get(path).json()
        self.assertEqual(len(data["active"]),10)
        self.assertEqual(data["flow"]["intake"]["recent_items"],0)
        self.assertTrue(all(t["number"].startswith("TEST-") for t in data["active"]))
        times=[t["created_at"] for t in data["active"]]
        self.assertEqual(times,sorted(times))
        ticket=data["active"][0]
        self.assertEqual(self.client.post(path+f"/{live['id']}",headers=headers,json={"action":"done","revision":live["revision"]}).status_code,404)
        self.assertEqual(self.client.post(f"/dashboard/stores/1/kitchen/{ticket['id']}",headers=headers,json={"action":"done","revision":ticket["revision"]}).status_code,404)
        for action in ["claim","release","done","undo"]:
            current=(self.client.get(path).json()["recent"] if action=="undo" else self.client.get(path).json()["active"])
            ticket=next(t for t in current if t["id"]==ticket["id"])
            self.assertEqual(self.client.post(path+f"/{ticket['id']}",headers=headers,json={"action":action,"revision":ticket["revision"]}).status_code,200)
        self.assertEqual(len(self.queue()["active"]),1)
        self.assertEqual(self.queue()["flow"]["completion"]["completed_items"],0)
        self.assertEqual(self.client.post(path+"/orders",headers=headers,json={"action":"clear"}).status_code,200)
        self.assertEqual(self.client.get(path).json()["active"],[])
        self.assertEqual(len(self.queue()["active"]),1)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count(DashboardOrder.id))),1)

    def test_simulation_permissions_limits_and_large_order(self):
        path="/dashboard/stores/1/kitchen-test"
        headers={"X-Kitchen-CSRF":self.csrf}
        self.assertEqual(self.client.post(path+"/orders",json={"action":"add"}).status_code,403)
        self.assertEqual(self.client.post(path+"/orders",headers=headers,json={"action":"add","count":26}).status_code,422)
        self.assertEqual(self.client.post(path+"/orders",headers=headers,json={"action":"add","scenario":"large"}).status_code,200)
        self.assertEqual(len(self.client.get(path).json()["active"][0]["items"]),12)
        page=self.client.get("/dashboard/stores/1/kitchen-display?test=true")
        self.assertIn("TEST MODE",page.text)
        self.assertIn('data-test="true"',page.text)
        with self.factory() as session:
            session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1)).role="member"
            session.commit()
        self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post(path+"/orders",headers=headers,json={"action":"clear"}).status_code,403)

    def test_simulation_display_scope_and_durable_updates(self):
        DatabaseAndRoutesTests.provision_display(self)
        self.client.post("/logout")
        self.client.post("/login/store-display",data={"organization_code":"ftw-1","username":"truck-screen","password":"screen-password-123"})
        page=self.client.get("/dashboard/stores/1/kitchen-display?test=true")
        token=re.search(r'data-csrf="([^"]+)"',page.text).group(1)
        headers={"X-Kitchen-CSRF":token}
        path="/dashboard/stores/1/kitchen-test"
        self.assertEqual(self.client.post(path+"/orders",headers=headers,json={"action":"add"}).status_code,200)
        self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.post("/dashboard/stores/2/kitchen-test/orders",headers=headers,json={"action":"clear"}).status_code,403)
        self.assertEqual(self.client.get("/dashboard/stores/2/kitchen-test").status_code,403)
        request=SimpleNamespace(session={"user_id":1,"organization_id":1},url=SimpleNamespace(path=path))
        before=kitchen.stamp(request,1)
        self.client.post(path+"/orders",headers=headers,json={"action":"add"})
        self.assertNotEqual(kitchen.stamp(request,1),before)

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

    def test_modifiers_notes_roundtrip_and_changed_preparation_reopens(self):
        choices=[{"selectableVariations":[{"attribute":"Drink_Flavors", "values":[{"name":"Blue_Raspberry"},{"name":"Coconut"}]}]}]
        self.seed(items=[{"id":"bar","name":"Custom Energy Drink","sku":"N-CSTM","quantity":1,"selectedVariants":choices}],notes="victoria")
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["notes"],"victoria")
        self.assertEqual(ticket["items"][0]["modifiers"],[{"attribute":"Drink_Flavors","values":["Blue_Raspberry","Coconut"]}])
        self.action(ticket,"done")
        self.seed(items=[{"id":"bar","name":"Custom Energy Drink","sku":"N-CSTM","quantity":1,"selectedVariants":choices}],notes="Victoria Smith")
        data=self.queue()
        self.assertEqual(data["recent"][0]["notes"],"Victoria Smith")
        self.assertEqual(data["recent"][0]["items"][0]["state"],"done")
        choices[0]["selectableVariations"][0]["values"]=[{"name":"Caffeine_Free"}]
        self.seed(items=[{"id":"bar","name":"Custom Energy Drink","sku":"N-CSTM","quantity":1,"selectedVariants":choices}],notes="Victoria Smith")
        data=self.queue()
        self.assertEqual(data["active"][0]["items"][0]["state"],"available")
        self.assertEqual(data["active"][0]["items"][0]["done_at"],None)
        self.assertEqual(data["flow"]["completion"]["completed_items"],0)

    def test_notes_migration_roundtrip(self):
        spec=importlib.util.spec_from_file_location("notes_migration","alembic/versions/a47c9e25d136_kitchen_notes.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
                self.assertNotIn("notes",[c["name"] for c in inspect(connection).get_columns("kitchen_tickets")])
                module.upgrade()
                self.assertIn("notes",[c["name"] for c in inspect(connection).get_columns("kitchen_tickets")])

    def test_customer_name_separate_from_notes_and_no_payment_name(self):
        self.seed(customer={"firstName":"Sarah","lastName":"Test","emails":["private@example.test"]},notes="No coconut")
        ticket=self.queue()["active"][0]
        self.assertEqual(ticket["customer_name"],"Sarah Test")
        self.assertEqual(ticket["notes"],"No coconut")
        with self.factory() as session:
            cached=session.scalar(select(DashboardOrder)).payload
            self.assertNotIn("customer",cached)
            self.assertNotIn("transactions",cached)
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen-display").status_code,200)
        with self.factory() as session:
            session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1)).role="member";session.commit()
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen-display").status_code,403)

    def test_customer_name_migration_roundtrip(self):
        spec=importlib.util.spec_from_file_location("customer_migration","alembic/versions/b58d0f36e247_kitchen_customer_name.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade();module.upgrade()
                self.assertIn("customer_name",[c["name"] for c in inspect(connection).get_columns("kitchen_tickets")])

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
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,200)
        home=self.client.get("/dashboard/stores/1/home")
        token=re.search(r'name="csrf_token" value="([^"]+)"',home.text).group(1)
        self.client.post("/dashboard/stores/1/show",data={"csrf_token":token})
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,200)
        self.assertEqual(self.client.get("/dashboard/stores/2/kitchen").status_code,403)
        self.client.get("/dashboard/stores/1/home")
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen").status_code,200)
        self.assertEqual(self.client.get("/dashboard/stores/1/kitchen-display").status_code,200)
        self.assertEqual(self.client.get("/dashboard/data?store_id=1").status_code,403)
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
