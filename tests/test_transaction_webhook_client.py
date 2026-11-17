import os
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['DOTENV_FILE']='/nonexistent-ftw-test-env'
import unittest
import json
from unittest.mock import AsyncMock
import httpx
from poynt.client import PoyntClient, PoyntAPIError


class HookClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self,handler):
        client=PoyntClient.__new__(PoyntClient)
        client.business_id='business';client.token_type='BEARER';client.access_token='test-only'
        client._refresh_if_needed=AsyncMock()
        client._api_client=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client

    async def test_registration_uses_documented_hook_endpoint_and_events(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'hooks':[]}) if request.method=='GET' else httpx.Response(201,json={'id':'hook'})
        client=self.client(handler)
        self.assertEqual(await client.list_transaction_hooks(),[])
        self.assertEqual(await client.create_transaction_hook('https://ftw.test/callback','secret','app'),{'id':'hook'})
        self.assertEqual(requests[0].url.params['businessId'],'business')
        self.assertEqual(requests[0].headers['api-version'],'1.2')
        body=json.loads(requests[1].content)
        self.assertEqual(body['eventTypes'],['TRANSACTION_CAPTURED','TRANSACTION_UPDATED'])
        self.assertEqual(requests[1].url.path,'/hooks')
        self.assertTrue(requests[1].headers['Poynt-Request-Id'])

    async def test_repeated_page_and_provider_failure_do_not_create(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'hooks':[{'id':str(i)} for i in range(100)]})
        client=self.client(handler)
        with self.assertRaises(PoyntAPIError):await client.list_transaction_hooks()
        self.assertEqual(len(requests),2)
        self.assertTrue(all(request.method=='GET' for request in requests))
        client=self.client(lambda request:httpx.Response(403,json={'secret':'private'}))
        with self.assertRaises(PoyntAPIError) as exc:await client.list_transaction_hooks()
        self.assertNotIn('private',str(exc.exception))
