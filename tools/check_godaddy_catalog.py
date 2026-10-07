"""Read-only Commerce access diagnostic. No database access or provider writes."""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import load_dotenv

STORES = {
    "Truck": "bddd89b0-c3cc-4135-befa-476c89fdf4f1",
    "Popup": "6440e3cd-757f-4e2d-8076-d4895722f044",
}
QUERY = """query CatalogCheck($after: String) {
  skus(first: 100, after: $after) {
    edges { node {
      id code label status skuGroup { id }
      prices(first: 100) { edges { node { id value { currencyCode value } } }
        pageInfo { hasNextPage } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""


class DiagnosticError(Exception):
    pass


def checked_json(response):
    if not response.is_success:
        raise DiagnosticError(f"HTTP {response.status_code}; no provider body logged")
    try:
        body = response.json()
    except ValueError:
        raise DiagnosticError("Response was not valid JSON") from None
    if not isinstance(body, dict):
        raise DiagnosticError("Unexpected response shape")
    if body.get("errors"):
        # Only known error codes are emitted; messages can contain sensitive data.
        allowed = {"UNAUTHENTICATED", "FORBIDDEN", "BAD_REQUEST", "GRAPHQL_VALIDATION_FAILED"}
        codes = {e.get("extensions", {}).get("code") for e in body["errors"] if isinstance(e, dict)}
        raise DiagnosticError("GraphQL error: " + ", ".join(sorted(c for c in codes if c in allowed) or ["UNCLASSIFIED"]))
    return body


def graphql_products(http, store):
    rows, seen_ids, seen_cursors, cursor = [], set(), set(), None
    for _ in range(1000):
        body = checked_json(http.post(
            f"https://api.godaddy.com/v2/commerce/stores/{store}/catalog-subgraph",
            headers={"x-store-id": store}, json={"query": QUERY, "variables": {"after": cursor}}))
        connection = (body.get("data") or {}).get("skus")
        if not isinstance(connection, dict) or not isinstance(connection.get("edges"), list):
            raise DiagnosticError("Missing SKU connection")
        for edge in connection["edges"]:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict) or not node.get("id") or node["id"] in seen_ids:
                raise DiagnosticError("Missing or repeated SKU ID")
            seen_ids.add(node["id"])
            prices = node.get("prices") or {}
            if (prices.get("pageInfo") or {}).get("hasNextPage"):
                raise DiagnosticError("SKU has more than 100 prices; additional pagination needed")
            rows.append({"id": node["id"], "sku": node.get("code"), "name": node.get("label"),
                "status": node.get("status"), "group_id": (node.get("skuGroup") or {}).get("id"),
                "prices": [e["node"] for e in prices.get("edges", [])]})
        page = connection.get("pageInfo") or {}
        if not isinstance(page.get("hasNextPage"), bool):
            raise DiagnosticError("Missing pagination completion flag")
        if not page["hasNextPage"]:
            return rows
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors or not connection["edges"]:
            raise DiagnosticError("Pagination cursor did not advance")
        seen_cursors.add(cursor)
    raise DiagnosticError("Page limit exceeded")


def legacy_products(http, store):
    rows, seen_ids, seen_tokens, token = [], set(), set(), None
    for _ in range(1000):
        params = {"pageSize": 100, "tokenDirection": "FORWARD"}
        if token:
            params["pageToken"] = token
        body = checked_json(http.get(f"https://api.godaddy.com/v1/commerce/stores/{store}/products",
            headers={"x-store-id": store}, params=params))
        products, pagination = body.get("products"), body.get("pagination")
        if not isinstance(products, list) or not isinstance(pagination, dict):
            raise DiagnosticError("Unexpected legacy products/pagination shape")
        for p in products:
            if not isinstance(p, dict) or str(p.get("storeId", "")).lower() != store.lower():
                raise DiagnosticError("Product store ID did not match requested store")
            pid = p.get("productId")
            if not pid or pid in seen_ids:
                raise DiagnosticError("Missing or repeated product ID")
            seen_ids.add(pid)
            rows.append({"id": pid, "sku": p.get("sku"), "name": p.get("name"),
                "active": p.get("active"), "price": p.get("price")})
        token = pagination.get("nextToken")
        if not token:
            return rows
        if not isinstance(token, str) or token in seen_tokens or not products:
            raise DiagnosticError("Legacy pagination token did not advance")
        seen_tokens.add(token)
    raise DiagnosticError("Page limit exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotenv", help="Optional local environment file")
    parser.add_argument("--output", default="godaddy_catalog_check.json")
    parser.add_argument("--store-id", action="append", help="Override the two ICC test stores")
    args = parser.parse_args()
    if args.dotenv:
        load_dotenv(args.dotenv, override=False)
    token = os.getenv("GODADDY_PERSONAL_ACCESS_TOKEN", "").strip()
    if not token:
        parser.error("Set GODADDY_PERSONAL_ACCESS_TOKEN in this shell, or supply --dotenv .env")
    stores = {str(UUID(s)): str(UUID(s)) for s in args.store_id} if args.store_id else STORES
    output = {"stores": [], "read_only": True}
    with httpx.Client(headers={"Authorization": "Bearer " + token}, timeout=30, follow_redirects=False) as http:
        for name, store in stores.items():
            result = {"name": name, "store_id": store, "checks": {}}
            for api, reader in (("graphql", graphql_products), ("legacy_rest", legacy_products)):
                try:
                    products = reader(http, store)
                    result["checks"][api] = {"status": "ok", "count": len(products), "products": products}
                    print(f"{name}: {api}: OK, {len(products)} SKU/product records")
                except (DiagnosticError, httpx.RequestError) as exc:
                    error = str(exc) if isinstance(exc, DiagnosticError) else "Network request failed"
                    result["checks"][api] = {"status": "failed", "error": error}
                    print(f"{name}: {api}: {error}")
            output["stores"].append(result)
    # No authentication headers, cookies, tokens, raw responses, or external IDs.
    Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Saved catalog results to {args.output}")


if __name__ == "__main__":
    main()
