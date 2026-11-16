import os
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['DOTENV_FILE']='/nonexistent-ftw-trigger-env'
import unittest
from unittest.mock import AsyncMock
import httpx
from poynt.client import PoyntClient, PoyntSignalAPIError


class SignalClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self,response):
        client=PoyntClient.__new__(PoyntClient)
        client.business_id='business';client.token_type='BEARER';client.access_token='test-only'
        client._refresh_if_needed=AsyncMock()
        class HTTP:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            get=AsyncMock(return_value=response)
        client._api_client=lambda:HTTP()
        return client,HTTP

    async def test_retry_after_propagates_and_does_not_retry_inline(self):
        client,http=self.make_client(httpx.Response(429,headers={'Retry-After':'137'}))
        with self.assertRaises(PoyntSignalAPIError) as raised:
            await client.signal_get('transactions','tx')
        self.assertEqual(raised.exception.retry_after,137)
        self.assertEqual(http.get.await_count,1)

    async def test_identifiers_are_quoted_and_response_checked(self):
        client,http=self.make_client(httpx.Response(200,json={'id':'order'}))
        self.assertEqual(await client.signal_get('orders','order/id'),{'id':'order'})
        self.assertTrue(http.get.call_args.args[0].endswith('/orders/order%2Fid'))
        with self.assertRaises(ValueError):await client.signal_get('https://bad.invalid')


if __name__=='__main__':unittest.main()
