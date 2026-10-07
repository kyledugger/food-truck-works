"""Controlled one-cent Pretty in Pink price test using saved Poynt credentials."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from check_poynt_products import CheckError, read_connection

PRODUCTS = {
    "truck": {"store_id": "bddd89b0-c3cc-4135-befa-476c89fdf4f1",
              "id": "d2671333-9e7d-4672-ab6f-ac5672883ee8", "original": 700},
    "popup": {"store_id": "6440e3cd-757f-4e2d-8076-d4895722f044",
              "id": "f6c25917-16c5-44c0-ba18-02be3c7922ed", "original": 900},
}


def product_url(business, product):
    return "https://services.poynt.net/businesses/" + quote(business, safe="") + "/products/" + quote(product, safe="")


def read_product(http, business, spec):
    response = http.get(product_url(business, spec["id"]))
    if not response.is_success:
        raise CheckError(f"Product read returned HTTP {response.status_code}")
    try:
        row = response.json()
    except ValueError:
        raise CheckError("Product read returned invalid JSON") from None
    if (not isinstance(row, dict) or row.get("id") != spec["id"]
            or row.get("storeId") != spec["store_id"] or row.get("businessId") != business):
        raise CheckError("Product identity, business, or store did not match; stopped")
    price = row.get("price") or {}
    if price.get("currency") != "USD" or type(price.get("amount")) is not int:
        raise CheckError("Product does not have an integer USD price")
    return {k: row.get(k) for k in ("id", "storeId", "businessId", "sku", "name", "price", "updatedAt")}


def patch_diagnostic(response):
    """Allowlist diagnostic fields; never retain raw bodies or auth headers."""
    result = {"http_status": response.status_code, "error_codes": []}
    request_id = response.headers.get("Poynt-Request-Id", "")
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", request_id):
        result["poynt_request_id"] = request_id
    try:
        body = response.json()
    except ValueError:
        result["body_format"] = "non_json"
        return result
    result["body_format"] = "json"
    rows = [body] if isinstance(body, dict) else body if isinstance(body, list) else []
    if isinstance(body, dict) and isinstance(body.get("errors"), list):
        rows = rows + body["errors"]
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        for key in ("code", "errorCode", "error", "reasonCode"):
            code = row.get(key)
            if type(code) is int:
                code = str(code)
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
                # Exclude anything resembling a credential from the request.
                authorization = response.request.headers.get("Authorization", "")
                if code in authorization or len(code) > 40:
                    continue
                if code not in result["error_codes"]:
                    result["error_codes"].append(code)
    return result


def patch_price(http, business, spec, expected, target, journal_path=None, journal=None):
    # JSON Patch test guards against a price change between GET and PATCH.
    response = http.patch(product_url(business, spec["id"]), json=[
        {"op": "test", "path": "/price/currency", "value": "USD"},
        {"op": "test", "path": "/price/amount", "value": expected},
        {"op": "replace", "path": "/price/amount", "value": target}],
        headers={"Content-Type": "application/json"})
    diagnostic = patch_diagnostic(response)
    if journal_path is not None and journal is not None:
        journal["last_patch_response"] = diagnostic
        save_journal(journal_path, journal)
    print("PATCH diagnostic: " + json.dumps(diagnostic, sort_keys=True))
    if not response.is_success:
        raise CheckError(f"Price PATCH returned HTTP {response.status_code}; run status before retrying. No automatic retry.")
    print(f"Poynt accepted PATCH: HTTP {response.status_code}")


def save_journal(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run_test(http, business, store, action, journal_path):
    spec = PRODUCTS[store]
    other = "popup" if store == "truck" else "truck"
    before = {name: read_product(http, business, value) for name, value in PRODUCTS.items()}
    for name, row in before.items():
        print(f"{name}: {row['name']} / {row['sku']}: USD {row['price']['amount']/100:.2f}")
    if action == "status":
        return
    original, target = spec["original"], spec["original"] + 1
    current = before[store]["price"]["amount"]
    if action == "apply":
        if journal_path.exists():
            raise CheckError("Journal already exists. Use status/restore; do not overwrite the saved original.")
        if current != original or before[other]["price"]["amount"] != PRODUCTS[other]["original"]:
            raise CheckError("Prices differ from the approved $7.00 truck / $9.00 popup baseline; no write made")
        journal = {"store": store, "business_id": business, "product_id": spec["id"],
            "original_cents": original, "test_cents": target, "before": before,
            "started_at": datetime.now(timezone.utc).isoformat(), "state": "apply_attempted"}
        # Exclusively create before any request that could change the price.
        with journal_path.open("x", encoding="utf-8") as handle:
            json.dump(journal, handle, indent=2)
        expected, destination = original, target
    else:
        if not journal_path.exists():
            raise CheckError("Saved journal is required to restore")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if (journal.get("store") != store or journal.get("business_id") != business
                or journal.get("product_id") != spec["id"] or journal.get("original_cents") != original
                or journal.get("test_cents") != target):
            raise CheckError("Journal does not match this test")
        if current == original:
            print("Original Poynt price is already present. Check the catalog and terminal too.")
            return
        if current != target:
            raise CheckError("Current price is neither the original nor the test price; stopped to preserve a later edit")
        expected, destination = target, original
        journal["state"] = "restore_attempted"
        save_journal(journal_path, journal)
    print(f"Changing only {store} Pretty in Pink: USD {expected/100:.2f} -> {destination/100:.2f}")
    patch_price(http, business, spec, expected, destination, journal_path, journal)
    after = {name: read_product(http, business, value) for name, value in PRODUCTS.items()}
    journal["after_" + action] = after
    journal["state"] = action + "_read_back"
    save_journal(journal_path, journal)
    if after[other]["price"] != before[other]["price"]:
        raise CheckError("OTHER STORE PRICE CHANGED. Stop testing and inspect both stores; journal saved.")
    if after[store]["price"]["amount"] != destination:
        raise CheckError("Immediate Poynt read-back differs from requested price; journal saved. Use status to check again.")
    print(f"Poynt read-back confirmed USD {destination/100:.2f}; other store unchanged in Poynt.")
    print("This does NOT confirm the GoDaddy catalog or terminal. Check both manually.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--organization-id", required=True, type=int)
    parser.add_argument("--store", required=True, choices=PRODUCTS)
    parser.add_argument("--action", choices=("status", "apply", "restore"), default="status")
    parser.add_argument("--journal", help="Default: poynt_price_test_STORE.json")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        if not Path(args.dotenv).is_file():
            raise CheckError("Environment file does not exist")
        load_dotenv(args.dotenv, override=True)
        credentials = read_connection(os.environ["DATABASE_URL"], args.organization_id)
        if not credentials:
            raise CheckError("No saved Poynt connection")
        expiration = credentials.get("expires_at")
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        if expiration and expiration.replace(tzinfo=expiration.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
            raise CheckError("Saved token expired. Restore access through FTW, then rerun. No token refresh performed here.")
        headers = {"Accept": "application/json", "api-version": "1.2",
            "Authorization": (credentials.get("token_type") or "BEARER") + " " + credentials["access_token"]}
        with httpx.Client(headers=headers, timeout=30, follow_redirects=False) as http:
            run_test(http, credentials["business_id"], args.store, args.action,
                Path(args.journal or f"poynt_price_test_{args.store}.json"))
    except CheckError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:
        parser.exit(1, "Test interrupted; details omitted to protect credentials. A PATCH may have completed. Keep the journal, run status, and inspect both stores before retrying.\n")


if __name__ == "__main__":
    main()
