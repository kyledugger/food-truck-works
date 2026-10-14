"""Isolated kitchen marker authorization, storage, and migration checks."""
import importlib.util
from sqlalchemy import select, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_kitchen
from kitchen_models import KitchenMarkers
from models import OrganizationMember


class MarkerTests(test_kitchen.KitchenTests):
    def save_rules(self, rules, revision=0, store=1):
        return self.client.post(f'/dashboard/stores/{store}/kitchen-markers',
            headers={'X-Kitchen-CSRF':self.csrf},json={'rules':rules,'revision':revision})

    def test_marker_rules_save_queue_watch_and_revision(self):
        rules=[{'target':'item','text':'banana','marker':'🍌'},
               {'target':'modifier','text':'caffeine free','marker':'⚠️'}]
        self.assertEqual(self.queue()['markers'],{'rules':[],'revision':0})
        saved=self.save_rules(rules)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(self.queue()['markers']['rules'],rules)
        self.assertEqual(self.client.get('/dashboard/stores/1/kitchen-watch').json()['markers']['rules'],rules)
        self.assertEqual(self.client.get('/dashboard/stores/1/kitchen-test').json()['markers']['rules'],rules)
        self.assertEqual(self.save_rules([],0).status_code,409)
        self.assertEqual(self.save_rules([],1).status_code,200)

    def test_marker_rules_roles_scope_csrf_and_validation(self):
        rule={'target':'item','text':'banana','marker':'🍌'}
        self.assertEqual(self.client.post('/dashboard/stores/1/kitchen-markers',json={'rules':[],'revision':0}).status_code,403)
        self.assertEqual(self.save_rules([rule],store=2).status_code,404)
        self.assertEqual(self.save_rules([{**rule,'text':'   '}]).status_code,422)
        self.assertEqual(self.save_rules([{**rule,'target':'notes'}]).status_code,422)
        for role in ('member','payroll'):
            with self.factory() as session:
                session.scalar(select(OrganizationMember).where(OrganizationMember.user_id==1)).role=role
                session.commit()
            self.assertEqual(self.save_rules([rule]).status_code,403)

    def test_marker_migration_roundtrip(self):
        spec=importlib.util.spec_from_file_location('markers_migration','alembic/versions/a04d61e72c39_kitchen_markers.py')
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        KitchenMarkers.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn('kitchen_markers',inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn('kitchen_markers',inspect(connection).get_table_names())
                migration.upgrade()
