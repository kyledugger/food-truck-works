"""Inspect hooks and explicitly create/delete one journaled overlap probe.

Reads saved credentials without refreshing them or writing the database.
Never logs secrets, authorization headers, raw provider bodies or order data.
"""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
from uuid import uuid4
import httpx
from dotenv import load_dotenv
from check_poynt_products import CheckError, read_connection

BASE = "https://services.poynt.net"
EVENTS = ["ORDER_OPENED", "ORDER_COMPLETED", "ORDER_CANCELLED", "ORDER_UPDATED"]


def request(http, method, path, **kwargs):
    response = http.request(method, BASE+path, headers={"Poynt-Request-Id": str(uuid4())}, **kwargs)
    if not response.is_success:
        raise CheckError(f"Poynt {method} returned HTTP {response.status_code}; response body omitted")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        raise CheckError("Provider returned invalid JSON") from None


def pages(http, path, key, params):
    rows, seen, offset = [], set(), 0
    for _ in range(100):
        body = request(http, "GET", path, params={**params, "limit": 100, "startOffset": offset})
        batch = body.get(key) if isinstance(body, dict) else None
        if not isinstance(batch, list) or any(not isinstance(r, dict) or not r.get("id") for r in batch):
            raise CheckError(f"Unexpected {key} response")
        signature = tuple(r["id"] for r in batch)
        if batch and signature in seen:
            raise CheckError("Provider repeated a page; stopped")
        seen.add(signature);rows.extend(batch)
        following = any(l.get("rel") == "next" for l in body.get("links", []) if isinstance(l, dict))
        if not following and len(batch) < 100:
            return rows
        if not batch:
            raise CheckError("Empty page advertised another page")
        offset += len(batch)
    raise CheckError("Pagination limit reached; narrow the delivery range")


def hooks(http, business):
    return pages(http, "/hooks", "hooks", {"businessId": business})


def safe_hook(row):
    # Existing callback URLs may contain credentials or secret query parameters.
    url = urlsplit(row.get("deliveryUrl") or "")
    safe_url = urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
    return {**{k: row.get(k) for k in ["id", "applicationId", "businessId", "eventTypes", "active"]},
            "deliveryUrl": safe_url}


def write_journal(path, data):
    path.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")


def resolve_probe(http, journal):
    current = hooks(http, journal["business_id"])
    candidates = [r for r in current if r.get("deliveryUrl") == journal["delivery_url"]
        and r.get("applicationId") == journal["application_id"]
        and r.get("businessId") == journal["business_id"] and r["id"] not in journal["before_ids"]]
    if len(candidates) > 1:
        raise CheckError("Multiple probe matches; do not create again or delete automatically")
    probe = candidates[0] if candidates else None
    if probe and journal.get("hook_id") not in (None, probe["id"]):
        raise CheckError("Probe identity changed; refusing automatic cleanup")
    return current, probe


def run(http, args, credentials, app_id):
    business = credentials["business_id"];path = Path(args.journal)
    if args.action == "list":
        print(json.dumps([safe_hook(r) for r in hooks(http, business)], indent=2));return
    if args.action == "create":
        if path.exists():
            raise CheckError("Journal already exists. Use status/delete; never retry create with a new journal after an uncertain response")
        callback = args.callback_url or os.getenv("POYNT_WEBHOOK_URL", "")
        url = urlsplit(callback)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise CheckError("Provide an HTTPS callback without credentials or fragment")
        secret = os.getenv("POYNT_WEBHOOK_SECRET", "")
        if len(secret) < 32:
            raise CheckError("POYNT_WEBHOOK_SECRET must be configured; it is never saved to the journal")
        if not args.confirm_create:
            raise CheckError("Create requires --confirm-create; this registers a temporary hook at Poynt")
        before = hooks(http, business)
        marker = uuid4().hex
        delivery_url = urlunsplit((url.scheme,url.netloc,url.path,urlencode(parse_qsl(url.query)+[("ftw_probe",marker)]),""))
        journal = {"business_id": business,"organization_id": args.organization_id,"application_id": app_id,
            "delivery_url": delivery_url,"before_ids": [r["id"] for r in before],
            "before_hooks": [safe_hook(r) for r in before],
            "started_at": datetime.now(timezone.utc).isoformat(),"hook_id": None}
        # Save intent before POST. A timeout must not provoke a duplicate create.
        write_journal(path,journal)
        row = request(http,"POST","/hooks",json={"applicationId":app_id,"businessId":business,
            "deliveryUrl":delivery_url,"secret":secret,"eventTypes":EVENTS})
        if not isinstance(row,dict) or not row.get("id"):
            raise CheckError("Create response lacked a hook ID. Run status, not create")
        if row["id"] in journal["before_ids"]:
            raise CheckError("Poynt returned an existing ID; no cleanup permitted. Inspect existing registration")
        journal["hook_id"]=row["id"];write_journal(path,journal)
        print("Temporary hook created. Run status to verify the original hooks remain intact.")
        print(json.dumps(safe_hook(row),indent=2));return
    journal = json.loads(path.read_text(encoding="utf-8"))
    if (journal["business_id"],journal["organization_id"],journal["application_id"]) != (business,args.organization_id,app_id):
        raise CheckError("Journal does not match this organization, business and application")
    current, probe = resolve_probe(http,journal)
    missing = sorted(set(journal["before_ids"])-{r["id"] for r in current})
    print("Original hook IDs missing:",json.dumps(missing))
    now_by_id={r["id"]:safe_hook(r) for r in current}
    changed=[r["id"] for r in journal.get("before_hooks",[]) if r["id"] in now_by_id and r!=now_by_id[r["id"]]]
    print("Original hook metadata changed:",json.dumps(changed))
    if args.action == "status":
        print("Probe:",json.dumps(safe_hook(probe) if probe else None,indent=2));return
    if args.action == "delete":
        if not args.confirm_delete:
            raise CheckError("Delete requires --confirm-delete and a matching temporary probe")
        if not probe:
            print("No matching probe remains; no deletion performed.");return
        request(http,"DELETE","/hooks/"+quote(probe["id"],safe=""))
        _, remaining = resolve_probe(http,journal)
        if remaining:
            raise CheckError("Probe still listed; inspect status before retrying")
        print("Temporary probe removed. Original hooks were not targeted.");return
    deliveries = pages(http,"/businesses/"+quote(business,safe="")+"/deliveries","deliveries",
        {"startAt":journal["started_at"]})
    print(json.dumps([{k:r.get(k) for k in ["id","hookId","resource","resourceId","eventType","createdAt","status","attempt"]}
        for r in deliveries if r.get("resource")=="/orders"],indent=2))
    print("Compare the same resourceId/eventType under both original and probe hook IDs. Delivery records alone do not prove successful FTW processing.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotenv",required=True)
    parser.add_argument("--organization-id",required=True,type=int)
    parser.add_argument("--action",choices=["list","create","status","deliveries","delete"],default="list")
    parser.add_argument("--journal",default="poynt_hook_probe.json")
    parser.add_argument("--callback-url")
    parser.add_argument("--confirm-create",action="store_true")
    parser.add_argument("--confirm-delete",action="store_true")
    args=parser.parse_args();logging.disable(logging.CRITICAL)
    try:
        if not Path(args.dotenv).is_file():raise CheckError("Environment file not found")
        load_dotenv(args.dotenv,override=True)
        credentials=read_connection(os.environ["DATABASE_URL"],args.organization_id)
        if not credentials or not credentials.get("access_token"):raise CheckError("No saved Poynt connection")
        expiry=credentials.get("expires_at")
        if isinstance(expiry,str):expiry=datetime.fromisoformat(expiry.replace("Z","+00:00"))
        if expiry and (expiry.tzinfo is None or expiry<=datetime.now(timezone.utc)):
            raise CheckError("Saved token expired or has no timezone. Let FTW refresh it, then rerun")
        app_id=os.environ["POYNT_APP_ID"]
        with httpx.Client(timeout=30,follow_redirects=False,headers={"api-version":"1.2",
            "Authorization":f"{credentials.get('token_type') or 'BEARER'} {credentials['access_token']}"}) as http:
            run(http,args,credentials,app_id)
    except (CheckError,KeyError,ValueError,OSError,httpx.HTTPError) as error:
        print(str(error) if isinstance(error,CheckError) else f"Stopped: {type(error).__name__}. No automatic mutation retry.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
