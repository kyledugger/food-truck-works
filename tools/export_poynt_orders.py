"""Fetch diagnostic order data using GET only; never refresh credentials or write DB data."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from check_poynt_products import CheckError, read_connection

DEFAULT_IDS = ["7fa12470-0d4b-4a8a-8f6b-136957c80981",
               "23aa6888-01a1-1000-5018-bfb21f8ffe96"]


def diagnostic_order(order):
    """Keep source and preparation evidence, excluding customer/payment details."""
    context_keys = ("businessId", "storeId", "channelId", "source", "sourceApp",
                    "storeDeviceId", "businessType", "transactionInstruction")
    def context(value):
        return {k: value[k] for k in context_keys if k in value} if isinstance(value, dict) else {}
    result = {k: order[k] for k in ("id", "orderNumber", "createdAt", "updatedAt",
              "owner", "externalId", "statuses", "valid", "accepted") if k in order}
    result["context"] = context(order.get("context"))
    result["transactions"] = [{**{k: t[k] for k in ("id", "createdAt", "updatedAt",
        "status", "action", "voided") if k in t}, "context": context(t.get("context"))}
        for t in order.get("transactions", []) if isinstance(t, dict)]
    result["items"] = [{k: item[k] for k in ("id", "name", "sku", "productId",
        "externalId", "externalProductId", "quantity", "status", "createdAt", "updatedAt",
        "selectedVariants") if k in item} for item in order.get("items", []) if isinstance(item, dict)]
    return result


def fetch_orders(http, business, ids, raw=False):
    orders = []
    for oid in ids:
        response = http.get("https://services.poynt.net/businesses/" + quote(business, safe="")
                            + "/orders/" + quote(oid, safe=""))
        if not response.is_success:
            raise CheckError(f"Order {oid}: HTTP {response.status_code}; response body omitted")
        order = response.json()
        if not isinstance(order, dict) or order.get("id") != oid:
            raise CheckError(f"Unexpected response for order {oid}; no export saved")
        orders.append(order if raw else diagnostic_order(order))
    return orders


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--organization-id", required=True, type=int)
    parser.add_argument("--order-id", action="append", help="Repeat to override the default 688/10817 pair")
    parser.add_argument("--output", default="poynt_order_comparison.json")
    parser.add_argument("--raw", action="store_true", help="Include full provider JSON, including customer/payment data")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        if not Path(args.dotenv).is_file():
            raise CheckError("Environment file not found")
        load_dotenv(args.dotenv, override=True)
        connection = read_connection(os.environ["DATABASE_URL"], args.organization_id)
        if not connection or not connection.get("access_token") or not connection.get("business_id"):
            raise CheckError("No saved Poynt connection")
        expiry = connection.get("expires_at")
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if expiry and (expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
            raise CheckError("Saved token expired or lacks timezone. Let FTW refresh it, then rerun")
        with httpx.Client(timeout=30, follow_redirects=False, headers={"api-version": "1.2",
            "Authorization": f"{connection.get('token_type') or 'BEARER'} {connection['access_token']}"}) as http:
            orders = fetch_orders(http, connection["business_id"], args.order_id or DEFAULT_IDS, args.raw)
        report = {"organization_id": args.organization_id, "business_id": connection["business_id"],
                  "export_mode": "raw" if args.raw else "diagnostic", "orders": orders}
        with Path(args.output).open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"Exported {len(orders)} orders to {args.output}")
        for order in orders:
            print(f"Order {order.get('orderNumber')}: {order.get('id')}")
    except (CheckError, KeyError, ValueError, OSError, httpx.HTTPError) as error:
        print(str(error) if isinstance(error, CheckError) else f"Stopped: {type(error).__name__}. No credentials or provider body printed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
