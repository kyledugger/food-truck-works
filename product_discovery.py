"""Read-only Poynt discovery and exact SKU comparison. No historical aliases."""
import os, json
from collections import Counter
from urllib.parse import quote, urlsplit, parse_qs
import httpx
import logging
from poynt.client import PoyntAPIError

logger = logging.getLogger(__name__)


async def read_json(client, path, params=None):
    await client.refresh()
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(client.BASE_URL + path, headers=client._headers(), params=params)
    if not response.is_success:
        raise PoyntAPIError(f"Poynt product discovery returned HTTP {response.status_code}.")
    try:
        result = response.json()
    except ValueError:
        raise PoyntAPIError("Poynt returned invalid product JSON.") from None
    if not isinstance(result, (dict, list)):
        raise PoyntAPIError("Poynt returned product JSON that was neither an object nor a list.")
    return result


async def collection(client, path, key):
    result, offset, seen = [], 0, set()
    for _ in range(1000):
        body = await read_json(client, path, {"limit": 100, "startOffset": offset})
        # Some Poynt deployments return a plain array; others use a named
        # collection or the standard content envelope. Accept all three.
        rows = body if isinstance(body, list) else body.get(key, body.get("content"))
        links = body.get("links", []) if isinstance(body, dict) else []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            logger.warning("Poynt collection shape rejected: collection=%s; named_field=%s; content_field=%s; rows_type=%s",
                key, isinstance(body, dict) and key in body,
                isinstance(body, dict) and "content" in body, type(rows).__name__)
            raise PoyntAPIError("Poynt collection did not contain a list of product/catalog objects.")
        if not isinstance(links, list):
            raise PoyntAPIError("Poynt collection links were not a list.")
        signature = tuple(str(row.get("id")) for row in rows)
        if rows and signature in seen:
            raise PoyntAPIError("Poynt product pagination repeated a page. Refresh again.")
        seen.add(signature)
        result.extend(rows)
        following = next((link for link in links
                          if isinstance(link, dict) and link.get("rel") == "next"), None)
        if not rows:
            if following:
                raise PoyntAPIError("Poynt product pagination was incomplete.")
            return result
        if following:
            # Use only the numeric cursor, never a provider-supplied URL.
            href = following.get("href")
            cursor = parse_qs(urlsplit(href).query).get("startOffset") if isinstance(href, str) else None
            if cursor:
                try:
                    next_offset = int(cursor[0])
                except (TypeError, ValueError):
                    # Some Poynt links contain a template instead of a number.
                    next_offset = offset + len(rows)
            else:
                next_offset = offset + len(rows)
            if next_offset <= offset:
                raise PoyntAPIError("Poynt product page cursor did not advance.")
            offset = next_offset
        elif len(rows) < 100:
            return result
        else:
            offset += len(rows)
    raise PoyntAPIError("Poynt product discovery exceeded the page limit.")


async def discover_products(client, store_id):
    business = quote(client.business_id, safe="")
    prefix = f"/businesses/{business}"
    store_body = await client.get_stores()
    stores = store_body if isinstance(store_body, list) else store_body.get("stores", store_body.get("content", [])) if isinstance(store_body, dict) else None
    ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
    if ENVIRONMENT == "local-prod-db":
        with open("get_stores_response.json", "w", encoding="utf-8") as file:
            json.dump(stores, file, indent=4)  # indent=4 makes it pretty and human-readable     

    if not isinstance(stores, list) or any(not isinstance(store, dict) for store in stores):
        raise PoyntAPIError("Poynt returned an unexpected store list.")
    if not any(str(store.get("id") or "").lower() == store_id.lower() for store in stores):
        raise PoyntAPIError("The configured store is not part of this Poynt business.")
    catalog_stores = {}
    for store in stores:
        sid = str(store.get("id") or "").lower()
        if store.get("catalogId"):
            catalog_stores.setdefault(str(store["catalogId"]), set()).add(sid)
        for device in store.get("storeDevices") or []:
            if device.get("catalogId") and device.get("status") not in ("REMOVED", "DEACTIVATED"):
                catalog_stores.setdefault(str(device["catalogId"]), set()).add(sid)
    assigned_catalogs = {cid for cid, stores in catalog_stores.items()
                         if store_id.lower() in stores}
    if not assigned_catalogs:
        raise PoyntAPIError("No assigned store or terminal catalog was found. Discovery cannot safely select this store's products.")
    products = await collection(client, prefix + "/products", "products")
    by_id = {str(p["id"]): p for p in products if p.get("id")}
    catalogs = await collection(client, prefix + "/catalogs", "catalogs")
    selected, categories, references = set(), {}, {}
    for catalog in catalogs:
        if not catalog.get("id"):
            raise PoyntAPIError("Poynt returned a catalog without an ID.")
        full = await read_json(client, prefix + "/catalogs/" + quote(str(catalog["id"]), safe="") + "/full")
        if not isinstance(full, dict):
            raise PoyntAPIError("Poynt full catalog response was not an object.")
        scoped = set(catalog_stores.get(str(catalog["id"]), set()))
        owner = str(full.get("storeId") or catalog.get("storeId") or "").lower()
        if owner:
            scoped.add(owner)
        def visit(items, category=""):
            if not isinstance(items, list):
                raise PoyntAPIError("Poynt returned an unexpected catalog product list.")
            for item in items:
                if not isinstance(item, dict):
                    raise PoyntAPIError("Poynt returned an unexpected catalog item.")
                embedded = item.get("product")
                pid = str(embedded.get("id") if isinstance(embedded, dict) else item.get("id") or "")
                if not pid:
                    raise PoyntAPIError("Poynt returned a catalog item without a product ID.")
                if pid not in by_id:
                    raise PoyntAPIError("A catalog product was missing from discovery. Refresh again.")
                references.setdefault(pid, set()).update(scoped or {"unassigned"})
                # Ownership does not mean this catalog is currently in use.
                if str(catalog["id"]) in assigned_catalogs:
                    selected.add(pid)
                    if category:
                        categories.setdefault(pid, set()).add(category)
        visit(full.get("products", []))
        for category in full.get("categories", []):
            visit(category.get("products", []), str(category.get("name") or ""))
    # Catalog membership is required. Store ownership alone can include old
    # products that are no longer offered on the store's menu.
    rows = []
    for pid in selected:
        p = by_id[pid]
        if p.get("status") == "RETIRED":
            continue
        sku = p.get("sku")
        price = p.get("price") or {}
        amount = price.get("amount") if isinstance(price, dict) else None
        currency = price.get("currency") if isinstance(price, dict) else None
        rows.append({"id": pid, "sku": sku if isinstance(sku, str) else "",
            "name": str(p.get("name") or "Unnamed product"),
            "category": ", ".join(sorted(categories.get(pid, []))),
            "amount": amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
            "currency": currency if isinstance(currency, str) else None,
            "shared": bool(references.get(pid, set()) - {store_id.lower()}),
            "scope_conflict": bool(p.get("storeId") and str(p["storeId"]).lower() != store_id.lower())})
    return sorted(rows, key=lambda p: (p["sku"], p["name"], p["id"]))


def compare_products(products, definitions):
    counts = Counter(p["sku"] for p in products if p["sku"])
    known = {p.sku for p in definitions}
    rows = []
    for p in products:
        sku, issues = p["sku"], []
        if not sku or not sku.strip():
            issues.append("Blank SKU")
        elif len(sku) > 200 or sku != sku.strip():
            issues.append("Invalid SKU: correct whitespace or length in Poynt")
        if sku and counts[sku] > 1:
            issues.append("Duplicate SKU in this store")
        if sku and sku not in known:
            issues.append("Not in authoritative list")
        if p.get("scope_conflict"):
            issues.append("Product ownership conflicts with selected store")
        can_add = bool(sku.strip() and len(sku) <= 200 and sku == sku.strip()
                       and counts[sku] == 1 and sku not in known and not p.get("scope_conflict"))
        rows.append(dict(p, issues=issues, can_add=can_add, matched=sku in known))
    present = {p["sku"] for p in products}
    missing = [p for p in definitions if p.sku not in present]
    return rows, missing
