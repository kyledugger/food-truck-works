"""Read the complete business product list without catalog or status filters."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


class CheckError(Exception):
    pass


def fetch_products(http, business_id):
    products, seen_ids, seen_pages, offset = [], {}, set(), 0
    for _ in range(1000):
        response = http.get("https://services.poynt.net/businesses/" + quote(business_id, safe="") + "/products",
            params={"limit": 100, "startOffset": offset})
        if not response.is_success:
            raise CheckError(f"Poynt returned HTTP {response.status_code}; provider body omitted")
        try:
            body = response.json()
        except ValueError:
            raise CheckError("Poynt returned invalid JSON") from None
        rows = body if isinstance(body, list) else body.get("products", body.get("content")) if isinstance(body, dict) else None
        links = body.get("links", []) if isinstance(body, dict) else []
        if not isinstance(rows, list) or not isinstance(links, list):
            raise CheckError("Unexpected product page shape")
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise CheckError(f"Product has no valid ID at offset {offset}, row {index}; no complete report saved")
        signature = tuple(row["id"] for row in rows)
        if rows and signature in seen_pages:
            raise CheckError(f"Poynt repeated an entire page at offset {offset}; pagination did not progress. No complete report saved")
        seen_pages.add(signature)
        duplicates = 0
        for row in rows:
            # Keep identity and pricing evidence, not arbitrary provider fields.
            product = {k: row.get(k) for k in (
                "id", "businessId", "storeId", "sku", "name", "shortCode", "price",
                "status", "type", "source", "externalId", "createdAt", "updatedAt")}
            previous = seen_ids.get(row["id"])
            if previous is not None:
                if previous != product:
                    raise CheckError(f"Repeated ID has conflicting product data at offset {offset}; rerun for a consistent snapshot. No complete report saved")
                duplicates += 1
                continue
            seen_ids[row["id"]] = product
            products.append(product)
        print(f"Page offset {offset}: {len(rows)} records, {duplicates} identical repeats, {len(products)} unique products so far")
        following = next((l for l in links if isinstance(l, dict) and l.get("rel") == "next"), None)
        if not rows:
            if following:
                raise CheckError("Empty page advertised a next page")
            return products
        if following:
            href = following.get("href")
            cursor = parse_qs(urlsplit(href).query).get("startOffset") if isinstance(href, str) else None
            try:
                next_offset = int(cursor[0]) if cursor else offset + len(rows)
            except (ValueError, TypeError):
                next_offset = offset + len(rows)
            if next_offset <= offset:
                raise CheckError("Product cursor did not advance")
            offset = next_offset
        elif len(rows) < 100:
            return products
        else:
            offset += len(rows)
    raise CheckError("Product pagination exceeded page limit")


def compare_catalog(products, catalog):
    comparisons = []
    for store in catalog.get("stores", []):
        check = store.get("checks", {}).get("graphql", {})
        if check.get("status") != "ok":
            continue
        sid = store["store_id"]
        entries = []
        for sku in check["products"]:
            code = sku.get("sku") or ""
            exact = [p for p in products if code and p.get("sku") == code]
            case_matches = [p for p in products if code and isinstance(p.get("sku"), str) and p["sku"].casefold() == code.casefold()]
            name_matches = [p for p in products if sku.get("name") and isinstance(p.get("name"), str) and p["name"].casefold() == sku["name"].casefold()]
            candidates = exact or case_matches or name_matches
            entries.append({"graphql_sku_id": sku["id"], "sku": code, "name": sku.get("name"),
                "graphql_prices": sku.get("prices", []),
                "candidate_basis": "exact_sku" if exact else "case_insensitive_sku" if case_matches else "exact_name" if name_matches else "none",
                "candidates": [{"id": p["id"], "store_id": p.get("storeId"), "sku": p.get("sku"),
                    "name": p.get("name"), "price": p.get("price"), "status": p.get("status"),
                    "store_id_matches": str(p.get("storeId") or "").lower() == sid.lower()} for p in candidates]})
        comparisons.append({"name": store.get("name"), "store_id": sid, "products": entries,
            "no_candidates": sum(not e["candidates"] for e in entries),
            "with_same_store_candidates": sum(any(c["store_id_matches"] for c in e["candidates"]) for e in entries)})
    return comparisons


def read_connection(database_url, org_id):
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            row = conn.execute(text("SELECT business_id, access_token, token_type, expires_at "
                "FROM poynt_connections WHERE organization_id = :org"), {"org": org_id}).mappings().first()
            return dict(row) if row else None
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotenv", required=True, help="Database environment to read; no writes")
    parser.add_argument("--organization-id", required=True, type=int)
    parser.add_argument("--compare", help="Optional godaddy_catalog_check.json")
    parser.add_argument("--output", default="poynt_unfiltered_products.json")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if not Path(args.dotenv).is_file():
        parser.error("Environment file does not exist")
    # Explicit file is authoritative over an inherited DATABASE_URL.
    load_dotenv(args.dotenv, override=True)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        parser.error("DATABASE_URL is missing")
    try:
        credentials = read_connection(db_url, args.organization_id)
        if not credentials or not credentials.get("access_token"):
            raise CheckError("No saved Poynt connection for this organization")
        expiration = credentials.get("expires_at")
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        if expiration and expiration.replace(tzinfo=expiration.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
            raise CheckError("Saved token has expired. Let FTW refresh its connection, then rerun. This script does not refresh or write tokens.")
        headers = {"Accept": "application/json", "api-version": "1.2",
            "Authorization": (credentials.get("token_type") or "BEARER") + " " + credentials["access_token"]}
        with httpx.Client(headers=headers, timeout=30, follow_redirects=False) as http:
            products = fetch_products(http, credentials["business_id"])
        report = {"business_id": credentials["business_id"], "organization_id": args.organization_id,
            "complete": True, "catalog_filtered": False, "products": products,
            "store_counts": dict(Counter(str(p.get("storeId") or "UNASSIGNED") for p in products)),
            "status_counts": dict(Counter(str(p.get("status") or "UNSPECIFIED") for p in products))}
        if args.compare:
            report["graphql_comparison"] = compare_catalog(products, json.loads(Path(args.compare).read_text(encoding="utf-8-sig")))
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Fetched {len(products)} unfiltered products")
        for sid, count in report["store_counts"].items():
            print(f"Store {sid}: {count} records")
        print("Status counts:", report["status_counts"])
        for store in report.get("graphql_comparison", []):
            print(f"{store['name']}: {store['with_same_store_candidates']} GraphQL items have same-store candidates; {store['no_candidates']} have no candidates")
        print(f"Saved {args.output}; no tokens included")
        print("Comparison candidates are evidence only, not confirmed identity matches.")
    except CheckError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:
        parser.exit(1, "Check failed (database, network, file, or response error). Details omitted to protect credentials. No complete report saved.\n")


if __name__ == "__main__":
    main()
