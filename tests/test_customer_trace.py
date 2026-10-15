"""Mocked HTTP only; never reads credentials or connects to a database."""
import sys
from pathlib import Path
import unittest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from trace_poynt_customer import trace, evidence, recent_orders


class CustomerTraceTests(unittest.TestCase):
    def test_follows_order_and_transaction_references_without_writes(self):
        calls=[]
        def handler(request):
            self.assertEqual(request.method,'GET');calls.append(request.url.path)
            bodies={'/businesses/b/orders/o':{'id':'o','customerUserId':123,'transactions':[{'id':'t'}]},
                '/businesses/b/transactions/t':{'customerUserId':123},
                '/businesses/b/customers/123':{'id':123,'firstName':'Kyle','lastName':'DuggerTest','displayName':'Kyle DuggerTest','cards':[{'number':'secret-card'}],'email':'private@example.com'}}
            return httpx.Response(200,json=bodies[request.url.path])
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            result=trace(http,'b','o','Kyle DuggerTest')
        self.assertEqual(calls.count('/businesses/b/customers/123'),1)
        self.assertTrue(result['customers'][0]['evidence']['expected_name_found_at'])
        self.assertNotIn('secret-card',str(result));self.assertNotIn('private@example.com',str(result))

    def test_permission_failure_does_not_become_missing_name(self):
        def handler(request):
            if '/orders/' in request.url.path:return httpx.Response(200,json={'id':'o','customerUserId':123})
            return httpx.Response(403,json={'secret':'not exported'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            result=trace(http,'b','o','Kyle DuggerTest')
        self.assertEqual(result['customers'][0]['http_status'],403)
        self.assertNotIn('evidence',result['customers'][0]);self.assertNotIn('not exported',str(result))

    def test_nested_customer_and_exact_test_string_paths(self):
        result=evidence({'customer':{'id':'abc','name':'Kyle DuggerTest'},'notes':'Kyle DuggerTest'},'Kyle DuggerTest')
        self.assertEqual(result['customer_references'],[{'path':'$.customer.id','id':'abc'}])
        self.assertIn('$.notes',result['expected_name_found_at'])

    def test_recent_list_reads_last_bounded_page(self):
        offsets=[]
        def handler(request):
            self.assertEqual(request.method,'GET');offsets.append(request.url.params.get('startOffset'))
            return httpx.Response(200,json={'count':53,'orders':[] if len(offsets)==1 else [{'id':'latest'}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            rows=recent_orders(http,'b',24)
        self.assertEqual(offsets,[None,'33']);self.assertEqual(rows,[{'id':'latest'}])


if __name__=='__main__':unittest.main()
