import importlib.util
from pathlib import Path
import unittest
import httpx

spec = importlib.util.spec_from_file_location("catalog_check", Path(__file__).resolve().parents[1] / "tools/check_godaddy_catalog.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class CatalogCheckTests(unittest.TestCase):
    def test_graphql_pagination_store_headers_and_prices(self):
        calls = []
        def respond(request):
            import json
            variables = json.loads(request.content)["variables"]
            calls.append(variables["after"])
            self.assertEqual(request.headers["x-store-id"], "store")
            self.assertIn("/stores/store/catalog-subgraph", str(request.url))
            second = variables["after"] is not None
            return httpx.Response(200, json={"data": {"skus": {
                "edges": [{"node": {"id": "two" if second else "one", "code": "ENE-PRETTYINPINK",
                    "prices": {"edges": [{"node": {"id": "price", "value": {"currencyCode": "USD", "value": 700}}}], "pageInfo": {"hasNextPage": False}}}}],
                "pageInfo": {"hasNextPage": not second, "endCursor": "opaque"}}}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            rows=check.graphql_products(http, "store")
        self.assertEqual(calls,[None,"opaque"])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["prices"][0]["value"]["value"],700)

    def test_graphql_http_200_error_is_failure(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                json={"errors": [{"message": "secret", "extensions": {"code": "UNAUTHENTICATED"}}]}))) as http:
            with self.assertRaisesRegex(check.DiagnosticError,"UNAUTHENTICATED") as error:
                check.graphql_products(http,"store")
        self.assertNotIn("secret",str(error.exception))

    def test_wrong_store_rejected(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                json={"products": [{"productId":"one","storeId":"other"}], "pagination":{}}))) as http:
            with self.assertRaisesRegex(check.DiagnosticError,"store ID"):
                check.legacy_products(http,"store")

    def test_legacy_cursor_is_opaque(self):
        calls=[]
        def respond(r):
            calls.append(r.url.params.get("pageToken"))
            second=len(calls)==2
            return httpx.Response(200,json={"products":[{"productId":"two" if second else "one","storeId":"store"}],
                "pagination":{} if second else {"nextToken":"eJy+/opaque"}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            self.assertEqual(len(check.legacy_products(http,"store")),2)
        self.assertEqual(calls,[None,"eJy+/opaque"])

if __name__ == "__main__":
    unittest.main()
