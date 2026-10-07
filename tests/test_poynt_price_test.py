import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_poynt_price as price


class PriceTestTests(unittest.TestCase):
    def test_rejection_saved_without_sensitive_body_and_no_retry(self):
        writes=[]
        def respond(r):
            name=next(n for n,s in price.PRODUCTS.items() if str(r.url).endswith(s['id']))
            spec=price.PRODUCTS[name]
            if r.method=='PATCH':
                writes.append(name)
                return httpx.Response(401, headers={'Poynt-Request-Id':'12345678-1234-1234-1234-123456789abc'},
                    json={'code':'UNAUTHORIZED','message':'Bearer secret-token', 'access_token':'secret-token',
                          'errors':[{'errorCode':'secret-token'}]})
            return httpx.Response(200,json={'id':spec['id'],'businessId':'biz','storeId':spec['store_id'],
                'name':'Pretty in Pink','sku':'CODE','price':{'currency':'USD','amount':spec['original']}})
        with tempfile.TemporaryDirectory() as tmp,httpx.Client(transport=httpx.MockTransport(respond),
                headers={'Authorization':'Bearer secret-token'}) as http:
            journal=Path(tmp)/'backup.json'
            with self.assertRaises(price.CheckError):price.run_test(http,'biz','popup','apply',journal)
            saved=journal.read_text()
            self.assertNotIn('secret-token',saved)
            diagnostic=json.loads(saved)['last_patch_response']
            self.assertEqual(diagnostic['http_status'],401)
            self.assertEqual(diagnostic['error_codes'],['UNAUTHORIZED'])
            self.assertEqual(diagnostic['poynt_request_id'],'12345678-1234-1234-1234-123456789abc')
        self.assertEqual(writes,['popup'])

    def test_non_json_and_untrusted_request_id_omitted(self):
        response=httpx.Response(502, text='secret',headers={'Poynt-Request-Id':'secret'},
            request=httpx.Request('PATCH','https://example.com'))
        self.assertEqual(price.patch_diagnostic(response),
            {'http_status':502,'error_codes':[],'body_format':'non_json'})

    def test_apply_and_restore_only_selected_price(self):
        amounts={'truck':700,'popup':900};writes=[]
        def respond(r):
            name=next(n for n,s in price.PRODUCTS.items() if str(r.url).endswith(s['id']))
            spec=price.PRODUCTS[name]
            if r.method=='PATCH':
                patch=json.loads(r.content);writes.append(name)
                self.assertEqual(patch[1]['value'],amounts[name])
                self.assertEqual(patch[2]['path'],'/price/amount')
                amounts[name]=patch[2]['value']
                return httpx.Response(204)
            return httpx.Response(200,json={'id':spec['id'],'businessId':'biz','storeId':spec['store_id'],
                'name':'Pretty in Pink','sku':'CODE','price':{'currency':'USD','amount':amounts[name]}})
        with tempfile.TemporaryDirectory() as tmp,httpx.Client(transport=httpx.MockTransport(respond)) as http:
            journal=Path(tmp)/'backup.json'
            price.run_test(http,'biz','truck','status',journal)
            self.assertEqual(writes,[])
            price.run_test(http,'biz','truck','apply',journal)
            self.assertEqual(amounts,{'truck':701,'popup':900})
            with self.assertRaises(price.CheckError):price.run_test(http,'biz','truck','apply',journal)
            price.run_test(http,'biz','truck','restore',journal)
            self.assertEqual(amounts,{'truck':700,'popup':900})
            self.assertEqual(writes,['truck','truck'])

    def test_wrong_store_blocks_write(self):
        writes=[]
        def respond(r):
            if r.method=='PATCH':writes.append(True)
            return httpx.Response(200,json={'id':price.PRODUCTS['truck']['id'],'businessId':'biz','storeId':'wrong'})
        with tempfile.TemporaryDirectory() as tmp,httpx.Client(transport=httpx.MockTransport(respond)) as http:
            with self.assertRaises(price.CheckError):price.run_test(http,'biz','truck','apply',Path(tmp)/'b.json')
        self.assertEqual(writes,[])

if __name__=='__main__':unittest.main()
