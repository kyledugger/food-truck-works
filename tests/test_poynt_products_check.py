import importlib.util
from pathlib import Path
import unittest
import httpx

spec=importlib.util.spec_from_file_location('raw_check',Path(__file__).resolve().parents[1]/'tools/check_poynt_products.py')
check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)


class RawCheckTests(unittest.TestCase):
    def test_all_pages_and_retired_records_kept(self):
        calls=[]
        def respond(r):
            offset=int(r.url.params['startOffset']);calls.append(offset)
            body={'products':[{'id':str(offset),'storeId':'a' if not offset else 'b','status':'RETIRED','sku':'X','secret':'omit'}]}
            if not offset:body['links']=[{'rel':'next','href':'https://untrusted.invalid/products?startOffset=1'}]
            self.assertEqual(r.url.host,'services.poynt.net')
            return httpx.Response(200,json=body)
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            rows=check.fetch_products(http,'business')
        self.assertEqual(calls,[0,1]);self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['status'],'RETIRED');self.assertNotIn('secret',rows[0])

    def test_repeated_ids_fail(self):
        def respond(r):
            offset=int(r.url.params['startOffset'])
            return httpx.Response(200,json={'products':[{'id':'same'}],
                'links':[{'rel':'next','href':f'/?startOffset={offset+1}'}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            with self.assertRaises(check.CheckError):check.fetch_products(http,'biz')

    def test_overlap_is_deduplicated(self):
        def respond(r):
            second=int(r.url.params['startOffset'])>0
            return httpx.Response(200,json={'products':[{'id':'same'},{'id':'second'}] if second else [{'id':'same'}],
                'links':[] if second else [{'rel':'next','href':'/?startOffset=1'}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            rows=check.fetch_products(http,'biz')
        self.assertEqual([r['id'] for r in rows],['same','second'])

    def test_conflicting_duplicate_fails(self):
        def respond(r):
            second=int(r.url.params['startOffset'])>0
            return httpx.Response(200,json={'products':[{'id':'same','sku':'changed'},{'id':'second'}] if second else [{'id':'same','sku':'original'}],
                'links':[] if second else [{'rel':'next','href':'/?startOffset=1'}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            with self.assertRaisesRegex(check.CheckError,'conflicting'):check.fetch_products(http,'biz')

    def test_missing_id_is_specific(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'products':[{'sku':'X'}]}))) as http:
            with self.assertRaisesRegex(check.CheckError,'offset 0, row 0'):check.fetch_products(http,'biz')

    def test_comparison_preserves_wrong_store_candidates(self):
        catalog={'stores':[{'name':'Popup','store_id':'popup','checks':{'graphql':{'status':'ok','products':[
            {'id':'g','sku':'ENE-PrettyInPink','name':'Drink','prices':[]}]}}}]}
        rows=[{'id':'p','storeId':'truck','sku':'ENE-PRETTYINPINK','name':'Drink','price':{'amount':700}}]
        result=check.compare_catalog(rows,catalog)[0]
        self.assertEqual(result['with_same_store_candidates'],0)
        self.assertEqual(result['products'][0]['candidate_basis'],'case_insensitive_sku')
        self.assertFalse(result['products'][0]['candidates'][0]['store_id_matches'])

if __name__=='__main__':unittest.main()
