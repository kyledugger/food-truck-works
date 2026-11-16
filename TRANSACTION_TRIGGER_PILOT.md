# Transaction-triggered kitchen orders and active-kitchen fallback

This package turns the demonstrated transaction signal into real order-cache
and kitchen updates for explicitly enabled stores. It preserves the existing
order webhook path, gives those notifications processing priority, and keeps
the normal polling/manual refresh safety net.

The matching order is saved through live_dashboard_service.save_order and
kitchen_service.sync_ticket. No payment-only record is converted to a kitchen
ticket. Order/item/revenue rules and archived/completed ticket behavior stay
in that existing pipeline. Pay Link/invoice dashboard reporting is not part
of this package.

## Database impact and deployment order

There are three NEW application tables: transaction_order_signals (durable
jobs), transaction_signal_receipts (delivery deduplication), and
transaction_signal_budgets (shared request/backoff state). No existing table
is altered by the new migration. Its revision f74c28a610be follows the current
revision a04d61e72c39 directly. Older branches af017c82d641 and c69e1a47f358
are already ancestors of a04d61e72c39 through earlier merge migrations;
they must not be required as separate current database revisions.

The author has not connected to or changed your production database, deployed
the code, or changed the provider webhook registration. Apply the migration
to your intended environment before enabling the trigger.

1. Extract this package into the FTW project, preserving paths. It updates
   backend modules and adds the migration, worker module, tests, and helper.
   It leaves the dashboard layout and daily revenue card files untouched.
2. From your local project root, inspect the intended production migration
   target with the default READ-ONLY preflight:

       .\.venv\Scripts\python.exe .\apply_transaction_trigger_migration.py --dotenv .env.local-prod-db

   It displays the target host/database (no credentials) and current revision
   IDs. For your deployment it expects Current revisions: a04d61e72c39.
   It refuses to apply if the source heads or database parent differ
   from this reviewed package. If it refuses, share the output for review;
   do not stamp versions or bypass the check. This prevents accidentally
   applying unrelated pending migrations.
3. If preflight passes for the intended database, execute the reviewed change:

       .\.venv\Scripts\python.exe .\apply_transaction_trigger_migration.py --dotenv .env.local-prod-db --apply

   --apply is the explicit schema-writing action. For a local database, select
   that environment's file instead. The helper normalizes PostgreSQL URLs to
   the installed psycopg driver without modifying any environment files.
4. Configure the hosted FTW service and deploy/restart:

       POYNT_TRANSACTION_TRIGGER_ENABLED=1
       POYNT_TRANSACTION_TRIGGER_STORE_IDS=bddd89b0-c3cc-4135-befa-476c89fdf4f1
       POYNT_TRANSACTION_TRIGGER_REQUESTS_PER_MINUTE=30
       POYNT_TRANSACTION_LOOKUP_ENABLED=0

   The store ID is the tested Ice Cream Crush Truck. Use comma-separated IDs
   to add other confirmed newer-POS stores later. An empty allowlist enables
   no stores. The existing POYNT_TRANSACTION_PROBE_ENABLED=1, organization ID,
   signing secret, hook ID, and router registration must remain in place.
   No re-registration is required. The existing callback path is reused, even
   though its name still contains transaction-probe. This remains a configured
   single-organization pilot, not automatic subscriber onboarding.
5. Reload the kitchen tablet and Resume service if prompted. An intentionally
   ended/stale service is not silently restarted. Keep the existing 30-second
   active-kitchen polling setting for this first live run.

## Trigger behavior

- The callback first verifies its signature, application, hook, organization,
  and current business connection, then commits an inbox record before ACK.
  It makes no Poynt API requests inline. If inbox persistence is unavailable,
  it returns 503 so the provider can retry instead of silently losing work.
- Capture and update deliveries coalesce into one transaction job. Unique
  receipt records deduplicate exact delivery retries across workers/restarts.
- Only allowlisted stores with a fresh active kitchen session are queued.
  Source event timestamps must be offset-aware and within the freshness
  window (-30 to +300 seconds of event age). Late/offline uploads still have
  normal reconciliation as the backstop.
- The existing organization lease allows only one worker or manual refresh
  owner to process that organization's orders at a time. New signal jobs are
  picked up by the existing one-second worker loop. They survive process
  restarts in PostgreSQL, rather than a background HTTP task or temporary file.
- Fetch the transaction to obtain an explicit POYNT_ORDER reference, then
  fetch that order. A cached reference reduces later attempts to one GET.
  Without a reference, inspect one recent 15-minute store-order page, matching
  transaction IDs exactly. Never guess by amount or sale time. Full pages
  are labeled truncated; broader fallback reconciliation remains responsible
  for anything outside that search.
- Save matching orders through the standard pipeline. Unfinished content is
  retried; existing tickets are updated without duplicating or resurrecting
  archived tickets. Checkout-link references are ignored for kitchen work.
- There are at most six attempts per cycle, with retry waits of roughly
  2, 2, 2, 3, and 5 seconds after attempts (plus worker/API scheduling time).
  Updates arriving during an in-flight fetch are not lost. A later distinct
  update may reopen a completed job to obtain its latest order snapshot.
  Exhausted jobs remain for diagnosis; ordinary polling continues.

## Request budget and errors

The default budget is 30 additional order/transaction GET requests per
60-second organization budget window, shared across stores and processes.
It is a LOCAL safeguard, not a claim about GoDaddy's allowed quota. It does
not count existing reconciliation/order-webhook requests or OAuth refreshes.
Processing batches are bounded to four jobs; requests are not evenly paced.
Lower the setting if needed (supported range 2..120). Budget exhaustion defers
jobs without issuing another API request.

429 responses persist a shared backoff based on Retry-After (seconds or HTTP
date), defaulting to 60 seconds if absent/unusable. The signal worker and
normal reconciliation both honor this signal-detected backoff. Heartbeats
cannot override an error/backoff deadline. Other lookup failures are bounded
by the per-cycle retry limit. Short requests use a five-second HTTP timeout.

Because this initial pilot RETAINS the existing 30-second fallback, total API
traffic may initially INCREASE. Measure reliability and request counts before
reducing fallback polling. Nothing in this package promises instantaneous
provider notification delivery or sub-second ticket display.

## Active-kitchen polling fix

Previously, authenticated kitchen requests could update last_seen_at while
leaving an already scheduled idle five-minute reconciliation deadline intact.
Now a valid, active, non-simulated, writable kitchen heartbeat brings the next
deadline forward to last successful reconciliation + POYNT_RECONCILE_SECONDS
(default 30 seconds), or now if that is overdue. It never pushes an earlier
deadline out and never shortens an error backoff. No Poynt call happens in
the heartbeat request.

Read-only watchers and services requiring Resume do not force rapid polling.
The observed five-minute log spacing demonstrates the slower schedule; logs
alone do not prove whether an idle deadline or stale service state caused
every prior missed ticket. Resume remains an explicit employee action.

## Verify the pilot

Record routine card and cash sales on the newer Truck POS with Time.is in
view. Record Done, spinner completion, and ticket appearance. Unlike stage
two, successful trigger lookups now save orders and CAN create real tickets.

Send logs for the test window containing:

    TRANSACTION_PROBE
    TRANSACTION_TRIGGER
    KITCHEN_SYNC_WAKE

Look for queued -> order_saved -> state=done. Request counts are logged per
attempt. Rate limits, local budget deferral, truncated searches, inactive
kitchen rejection, and inbox errors are explicit. Queue admission still
depends on service state and its existing start-time rules. Leave the normal
manual refresh available. A delayed provider event still imposes that delay.

## Rollback and scope

Set POYNT_TRANSACTION_TRIGGER_ENABLED=0 and keep
POYNT_TRANSACTION_LOOKUP_ENABLED=0 to return the callback to passive logging.
The fallback polling fix stays active. Keep the new tables/migration so
pending diagnostic state is retained; disabling does not require destructive
downgrade or removal of the working order hook. No new signing secret is
needed. Do not remove the reused provider hook while using this trigger.

## Validation

Isolated SQLite/mocked tests cover signed callback durability, direct and cash
lookups, ticket creation, delivery deduplication, updates during a fetch,
cross-store rejection, old order-hook priority, archived ticket preservation,
retry exhaustion, shared budget deferral, persisted 429 backoff, expired
service handling, heartbeat scheduling, missing-migration fallback, migration
upgrade/downgrade, and migration preflight guards. Existing kitchen and probe
regression suites were also exercised. PostgreSQL row-lock behavior and live
end-to-end timing must still be validated on the deployment.
