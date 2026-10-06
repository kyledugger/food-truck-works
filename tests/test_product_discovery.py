"""Isolated database, mocked Poynt; never uses production credentials."""
import os
os.environ['DATABASE_URL'] = 'sqlite://'
import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, select, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from alembic.migration import MigrationContext
from alembic.operations import Operations
from database import Base
from models import Organization, OrganizationStore, PricingProduct, ProductDiscovery
from poynt.connection import PoyntCredentials
from poynt.client import PoyntAPIError
from product_discovery import compare_products, collection, discover_products, read_json
from routers import pricing


def product(pid='p1', sku='001', **kwargs):
    return dict(id=pid, sku=sku, name='Bar', category='Bars', amount=750,
                currency='USD', shared=False, scope_conflict=False, **kwargs)


class ComparisonTests(unittest.TestCase):
    def test_exact_skus_and_leading_zeros(self):
        rows, missing = compare_products([product(sku='001'), product('p2','bar')],
            [SimpleNamespace(sku='001'), SimpleNamespace(sku='BAR')])
        self.assertFalse(rows[0]['issues'])
        self.assertTrue(rows[1]['can_add'])
        self.assertEqual([p.sku for p in missing], ['BAR'])

    def test_invalid_and_duplicate_skus_cannot_be_added(self):
        values = [product('p1',''),product('p2',' '), product('p3','001'),
                  product('p4','001'),product('p5',' 01'),product('p6','x'*201)]
        rows, _ = compare_products(values, [])
        self.assertFalse(any(row['can_add'] for row in rows))
        self.assertTrue(all(row['issues'] for row in rows))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_link_without_numeric_cursor_uses_returned_count(self):
        for link in ({'rel':'next'}, {'rel':'next','href':'/products'},
                     {'rel':'next','href':'/products?startOffset={startOffset}'}):
            provider = AsyncMock(side_effect=[{'products':[{'id':'p1'},{'id':'p2'}], 'links':[link]},
                                             {'products':[{'id':'p3'}]}])
            with patch('product_discovery.read_json',provider):
                rows=await collection(None,'/safe','products')
            self.assertEqual(len(rows),3)
            self.assertEqual(provider.await_args_list[1].args[2]['startOffset'],2)

    async def test_http_200_plain_array_is_accepted(self):
        import httpx
        http = AsyncMock()
        http.get.return_value = httpx.Response(200, json=[{'id':'p1'}])
        client = SimpleNamespace(refresh=AsyncMock(), BASE_URL='https://provider.invalid', _headers=lambda: {})
        with patch('product_discovery.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = http
            rows = await read_json(client,'/products')
        self.assertEqual(rows,[{'id':'p1'}])

    async def test_plain_array_and_content_envelope(self):
        for body in ([{'id':'p1'}], {'content':[{'id':'p1'}]}):
            with patch('product_discovery.read_json', AsyncMock(return_value=body)):
                rows = await collection(None,'/safe','products')
            self.assertEqual(rows,[{'id':'p1'}])

    async def test_plain_array_paginates_at_page_limit(self):
        first = [{'id':str(i)} for i in range(100)]
        provider = AsyncMock(side_effect=[first,[{'id':'100'}]])
        with patch('product_discovery.read_json',provider):
            rows = await collection(None,'/safe','products')
        self.assertEqual(len(rows),101)
        self.assertEqual(provider.await_args_list[1].args[2]['startOffset'],100)

    async def test_terminal_assigned_catalog_without_store_id(self):
        client = SimpleNamespace(business_id='biz', get_stores=AsyncMock(return_value={
            'stores':[{'id':'a','storeDevices':[{'catalogId':'c1','status':'ACTIVATED'}]},
                      {'id':'b','catalogId':'c1'}]}))
        async def collections(client,path,key):
            return [{'id':'p1','sku':'001','name':'Bar'}] if key == 'products' else [{'id':'c1'}]
        with patch('product_discovery.collection',collections), patch('product_discovery.read_json',
                AsyncMock(return_value={'products':[{'id':'p1'}]})):
            rows = await discover_products(client,'a')
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['shared'])

    async def test_pagination_uses_cursor_without_following_untrusted_url(self):
        provider = AsyncMock(side_effect=[{'products':[{'id':'p1'}],
            'links':[{'rel':'next','href':'https://evil.test/path?startOffset=1'}]},
            {'products':[{'id':'p2'}]}])
        with patch('product_discovery.read_json', provider):
            rows = await collection(None,'/safe','products')
        self.assertEqual(len(rows),2)
        self.assertEqual(provider.await_args_list[1].args[1],'/safe')
        self.assertEqual(provider.await_args_list[1].args[2]['startOffset'],1)

    async def test_repeated_page_fails(self):
        page={'products':[{'id':'p1'}], 'links':[{'rel':'next','href':'?startOffset=1'}]}
        with patch('product_discovery.read_json', AsyncMock(return_value=page)):
            with self.assertRaises(PoyntAPIError): await collection(None,'/safe','products')

    async def test_store_scope_deduplication_categories_retired_and_shared(self):
        products=[dict(id='p1',sku='001',name='Bar',status='ACTIVE',price={'amount':750,'currency':'USD'}),
            dict(id='p2',sku='002',name='Other',storeId='b'),
            dict(id='p3',sku='003',name='Retired',storeId='a',status='RETIRED'),
            dict(id='p4',sku='004',name='Uncataloged',storeId='a'),
            dict(id='p5',sku='005',name='Other catalog only',storeId='a')]
        catalogs=[{'id':'c1'},{'id':'c2'},{'id':'old'}]
        async def collections(client,path,key): return products if key=='products' else catalogs
        full=AsyncMock(side_effect=[{'storeId':'a','products':[{'id':'p1'}],
            'categories':[{'name':'Bars','products':[{'product':{'id':'p1'}},{'id':'p3'}]}]},
            {'storeId':'b','products':[{'id':'p1'},{'id':'p2'},{'id':'p5'}]},
            {'storeId':'a','products':[{'id':'p4'},{'id':'p5'}]}])
        with patch('product_discovery.collection',collections),patch('product_discovery.read_json',full):
            rows=await discover_products(SimpleNamespace(business_id='biz', get_stores=AsyncMock(return_value={'stores':[{'id':'a','catalogId':'c1'},{'id':'b','catalogId':'c2'}]})), 'a')
        self.assertEqual([p['id'] for p in rows],['p1'])
        self.assertTrue(rows[0]['shared']);self.assertEqual(rows[0]['category'],'Bars')

    async def test_missing_assignment_does_not_guess_from_catalog_ownership(self):
        client=SimpleNamespace(business_id='biz', get_stores=AsyncMock(return_value=[{'id':'a'}]))
        with patch('product_discovery.collection',AsyncMock()) as provider:
            with self.assertRaisesRegex(PoyntAPIError,'No assigned'):
                await discover_products(client,'a')
            provider.assert_not_awaited()

    async def test_deactivated_terminal_catalog_is_excluded(self):
        client=SimpleNamespace(business_id='biz',get_stores=AsyncMock(return_value=[{
            'id':'a','storeDevices':[{'catalogId':'old','status':'DEACTIVATED'}]}]))
        with self.assertRaisesRegex(PoyntAPIError,'No assigned'):
            await discover_products(client,'a')


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        # Only new tables and their organization/store parents are needed.
        for table in (Organization.__table__, OrganizationStore.__table__, PricingProduct.__table__, ProductDiscovery.__table__):
            table.create(self.engine)
        self.factory=sessionmaker(self.engine)
        with self.factory() as s:
            s.add(Organization(id=1,name='One'));s.add(Organization(id=2,name='Two'))
            s.add_all([OrganizationStore(id=1,organization_id=1,store_id='a',poynt_name='Truck',is_active=True),
                       OrganizationStore(id=2,organization_id=2,store_id='b',poynt_name='Other',is_active=True)])
            s.commit()
        self.patches=[patch('routers.pricing.SessionLocal',self.factory),
            patch('routers.pricing.get_current_organization_id',return_value=1),
            patch('routers.pricing.get_organization_role',return_value='owner'),
            patch('routers.pricing.get_poynt_credentials',return_value=PoyntCredentials('biz','access','refresh','BEARER',None))]
        for p in self.patches:p.start()
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test')
        @app.get('/test-login')
        def login(request: __import__('fastapi').Request):
            request.session['user_id']=1;return {}
        app.include_router(pricing.router)
        self.client=TestClient(app);self.client.get('/test-login')
        response=self.client.get('/pricing/products?store_id=a')
        import re
        self.csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):p.stop()
        self.engine.dispose()

    def snapshot(self, rows=None):
        with self.factory() as s:
            s.add(ProductDiscovery(organization_id=1,store_id='a',business_id='biz',token='snap',
                payload={'discovered_at':'2026-10-06T06:00:00+00:00','products':rows or [product()]}));s.commit()

    def add(self, **kwargs):
        data=dict(store_id='a',csrf=self.csrf,snapshot_token='snap',product_ids=['p1']);data.update(kwargs)
        return self.client.post('/pricing/products/add',data=data,follow_redirects=False)

    def test_import_and_comparison(self):
        self.snapshot();self.assertEqual(self.add().status_code,303)
        response=self.client.get('/pricing/products?store_id=a')
        self.assertIn('Matched',response.text)
        with self.factory() as s:self.assertEqual(s.scalar(select(PricingProduct)).sku,'001')
        self.assertEqual(self.add().status_code,400)

    def test_manager_only(self):
        for role in ('member','payroll','store_display',None):
            with patch('routers.pricing.get_organization_role',return_value=role):
                self.assertEqual(self.client.get('/pricing/products').status_code,403)
                self.assertEqual(self.add().status_code,403)

    def test_anonymous_access_denied(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/pricing/products').status_code,403)

    def test_changed_business_blocks_import(self):
        self.snapshot()
        with patch('routers.pricing.get_poynt_credentials', return_value=PoyntCredentials('new-business','access','refresh','BEARER',None)):
            self.assertEqual(self.add().status_code,409)
            self.assertIn('connection changed',self.client.get('/pricing/products?store_id=a').text)

    def test_csrf_and_cross_org_store(self):
        self.snapshot()
        self.assertEqual(self.add(csrf='bad').status_code,403)
        self.assertEqual(self.add(store_id='b').status_code,404)
        self.assertEqual(self.client.get('/pricing/products?store_id=b').status_code,404)

    def test_stale_or_tampered_selection(self):
        self.snapshot()
        self.assertEqual(self.add(snapshot_token='old').status_code,409)
        self.assertEqual(self.add(product_ids=['other']).status_code,400)

    def test_invalid_duplicates_rejected_server_side(self):
        self.snapshot([product('p1','001'),product('p2','001')])
        self.assertEqual(self.add().status_code,400)

    def test_missing_and_unknown_flags(self):
        self.snapshot()
        with self.factory() as s:s.add(PricingProduct(organization_id=1,sku='002',name='Banana'));s.commit()
        text=self.client.get('/pricing/products?store_id=a').text
        self.assertIn('Not in authoritative list',text);self.assertIn('Missing from this store',text)

    def test_discovery_failure_preserves_snapshot(self):
        self.snapshot()
        with patch('routers.pricing.discover_products',AsyncMock(side_effect=PoyntAPIError('failed'))):
            response=self.client.post('/pricing/products/discover',data={'store_id':'a','csrf':self.csrf})
        self.assertEqual(response.status_code,502)
        self.assertIn('Previous results were kept',response.text)
        with self.factory() as s:self.assertEqual(s.scalar(select(ProductDiscovery)).token,'snap')

    def test_refresh_replaces_snapshot_and_invalidates_old_selection(self):
        self.snapshot()
        with patch('routers.pricing.discover_products',AsyncMock(return_value=[product()])):
            self.assertEqual(self.client.post('/pricing/products/discover',data={'store_id':'a','csrf':self.csrf},follow_redirects=False).status_code,303)
        self.assertEqual(self.add().status_code,409)

    def test_remove_is_scoped_and_does_not_modify_poynt(self):
        with self.factory() as s:
            s.add_all([PricingProduct(id=1,organization_id=1,sku='001',name='Bar'),PricingProduct(id=2,organization_id=2,sku='002',name='Other')]);s.commit()
        data={'definition_id':2,'csrf':self.csrf}
        self.assertEqual(self.client.post('/pricing/products/remove',data=data).status_code,404)
        data['definition_id']=1
        self.assertEqual(self.client.post('/pricing/products/remove',data=data,follow_redirects=False).status_code,303)
        with self.factory() as s:self.assertEqual(len(s.scalars(select(PricingProduct)).all()),1)


class MigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_in_isolated_database(self):
        engine=create_engine('sqlite://')
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE organizations (id INTEGER PRIMARY KEY)')
            spec=importlib.util.spec_from_file_location('migration','alembic/versions/f32a91c07e64_product_discovery.py')
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            with patch.object(migration,'op',Operations(MigrationContext.configure(conn))):
                migration.upgrade()
                self.assertIn('pricing_products',inspect(conn).get_table_names())
                migration.downgrade()
                self.assertEqual(inspect(conn).get_table_names(),['organizations'])
        engine.dispose()

if __name__ == '__main__':unittest.main()
