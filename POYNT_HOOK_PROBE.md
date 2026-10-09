# Check overlapping Poynt registrations

Copy `tools/test_poynt_hooks.py` into the existing repo. It uses the existing `tools/check_poynt_products.py` credential reader and existing Python dependencies. No deployment, schema change or database write is required. It does not refresh tokens; leave FTW running to refresh them if necessary.

First list registrations (read-only). The environment file identifies the database holding the connection; use `.env.local-prod-db` only when you intend to inspect that connection:

```powershell
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1
```

Send that sanitized output first. Verify an existing active hook has the same application, business and overlapping order event types before testing. If none exists, the test cannot establish overlap.

Then explicitly create one temporary hook. This changes the Poynt business's subscriptions, not FTW database rows:

```powershell
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1 --action create --confirm-create
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1 --action status
```

The callback is taken from POYNT_WEBHOOK_URL, with a unique `ftw_probe` query marker appended; its signing secret is POYNT_WEBHOOK_SECRET from the environment. An optional `--callback-url https://your-host/your-listener` selects another listener. This tests distinct URLs via a query marker; distinct path registration/delivery would still need validation when implementing organization routes.

Keep `poynt_hook_probe.json`. It contains registration identity, original IDs and start time, not tokens or signing secrets. Do not rerun create with a new journal following a timeout; status can locate the uncertain registration by its unique URL. A provider mutation is never automatically retried. If Poynt replaces an existing registration or returns its ID, stop and inspect it; automatic deletion refuses original IDs.

After status shows both registrations, make a small paid POS order, then inspect delivery history:

```powershell
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1 --action deliveries
```

Find the same `resourceId` and `eventType` with two different hook IDs, one original and one temporary. The status and attempt fields distinguish delivery success from retrying. Current FTW may return 409 for both because its business-to-organization routing is still broken; that does not disprove Poynt's ability to send events to both registrations. Confirm callback arrival in server logs too (the temporary request has the query marker). This probe does not fix kitchen delivery.

Remove only the temporary registration afterward:

```powershell
python tools/test_poynt_hooks.py --dotenv .env.local-prod-db --organization-id 1 --action delete --confirm-delete
```

The script verifies business, app, URL and original-ID exclusion before deleting the exact probe. If the journal is lost or edited, do not guess a hook ID to delete. Provider errors print only HTTP status, not raw response bodies. Listing redacts callback credentials and query strings; never share your environment file.
