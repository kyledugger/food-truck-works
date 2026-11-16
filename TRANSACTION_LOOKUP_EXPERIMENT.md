# Stage two: transaction-triggered order availability

This extends the installed transaction_signal_probe. It measures when a
matching order becomes readable after a transaction notification. It DOES NOT
save the order, create a kitchen ticket, change revenue, or alter polling.
The kitchen may still take 30 seconds to display it; compare API evidence in
the logs to the video. This is measurement before enabling a real trigger.

## Install and arm

Extract into the existing FTW project, preserving paths. Replaces only:

- routers/transaction_signal_probe.py

Adds:

- transaction_order_probe.py
- tests/test_transaction_order_probe.py
- updated tests/test_transaction_signal_probe.py

The main.py router registration from stage one is reused. No webhook
registration changes, new secrets, migrations, or dependency changes are
required. Keep all existing POYNT_TRANSACTION_PROBE_* variables and hook ID.

Set these additional variables on the deployed FTW service:

    POYNT_TRANSACTION_LOOKUP_ENABLED=1
    POYNT_TRANSACTION_LOOKUP_STORE_ID=bddd89b0-c3cc-4135-befa-476c89fdf4f1
    POYNT_TRANSACTION_LOOKUP_RUN_ID=truck-availability-01

The store ID above is Ice Cream Crush Truck, matching the tested newer POS.
Restart/redeploy with these files and settings. Keep the normal kitchen
session and current polling active. Open FTW first so its saved access token
is current; the experiment does not refresh tokens or write the FTW database.

Run this controlled experiment on ONE hosting instance. Multiple Uvicorn
workers on that instance share the local diagnostic journal. Separate hosting
instances do not share the budget. Do not enable it fleet-wide.

## Controlled test

Once armed, the FIRST TWO new captured transactions for this store consume
the run's two test slots. Plan one card sale and one cash sale on the newer
POS; an unrelated captured payment can consume a slot too.

Use the synchronized Time.is display in the video. Record the order number,
payment type, date, and absolute times for Done, spinner disappearance, and
the kitchen ticket. Do not adjust Time.is displayed time by its device-clock
offset message.

For each transaction, a capture starts at most one lookup series. Updated
events and duplicate captures remain visible in the passive logs but do not
start another series for the same transaction. Events older than 60 seconds
or more than 10 seconds in the future are not used to start probes.

Lookups are scheduled approximately 0, 2, 4, 6, 9, and 14 seconds after the
background series starts. API latency can shift these times; the logs show
actual request start/end timestamps. The whole series has a 35-second ceiling.

Each attempt:

1. Reads the transaction to find an explicit POYNT_ORDER reference, unless a
   reference was already obtained on a previous attempt.
2. Reads that order directly, or searches a single bounded page of recent
   orders when no direct reference exists.
3. Requires the correct store and a transaction-ID match or explicit order
   reference. Never matches on amount, proximity in time, or a guessed number.
4. Logs whether the order has the completed-sale status and positive,
   non-cancelled/non-returned item content used by the kitchen's eligibility
   checks. This is not a guarantee that every kitchen session would import it.

The recent-order search covers from 15 minutes before signal receipt through
the current time and requests at most 100 records. It does not paginate or
download the full business day. A full/partial page is explicitly labeled;
not finding a match in this bounded search is not proof that no order exists.
Older linked orders can be found by a direct reference if Poynt supplies one.

The series stops as soon as eligible matching content is observed. An HTTP
error, including 429, stops the series rather than retrying it. No rate-limit
quota is assumed. At most TWO GET calls per attempt, SIX attempts per
transaction, TWO transactions per local run: at most 24 EXTRA Poynt GET calls
for the controlled test, usually fewer. Existing polling is additional and
unchanged. Two test series may overlap if two payments occur together.

## Collect results

Send the server logs covering both tests, including:

    TRANSACTION_PROBE
    TRANSACTION_ORDER_PROBE

Useful events:

- lookup_started: confirms this transaction consumed a slot.
- order_lookup_attempt: actual request times, request count, match status,
  safe order ID/number/status, item-row counts, and eligible_order_content.
- order_lookup_summary: first matching and first eligible observation times.
- lookup_skipped: duplicate, stale event, or exhausted local run budget.
- lookup_stopped: expired saved token, rate limiting, response error, or timeout.

Logs exclude customer information, item descriptions, card data, credentials,
and raw provider bodies. IDs and order numbers remain business-sensitive.

Compare the first eligible observation with video Done/spinner/ticket times.
That timestamp is an UPPER BOUND on actual API availability, because checks
are discrete. A matching order may be readable earlier but still unfinished.
We do not infer readiness solely from a payment being captured. This stage
does not update the local cache, so its observations cannot create the ticket
being measured. Registration/delivery timing has already been demonstrated;
this stage tests correlation and order-readability timing.

## Stop or repeat

After the two tests, set:

    POYNT_TRANSACTION_LOOKUP_ENABLED=0

Restart/redeploy. The original passive listener remains active and existing
order hooks, polling, and kitchen refresh continue unchanged.

To repeat a controlled test, change RUN_ID to a new short identifier such as
truck-availability-02 and enable again. Do not reuse an exhausted run ID.

The budget/deduplication journal is a permission-restricted SQLite file under
the server's temporary directory, scoped by organization/business/hook/run.
It is diagnostic state, NOT a new application database or schema. A crashed
series keeps its slot consumed if that journal survives. If ephemeral disk is
replaced, the budget resets even with the same run ID. Disable the experiment
after testing; this is not a durable production job queue.

## Validation

24 mocked/unit tests cover the extended and existing probe and its manager:
callback acknowledgement before background API work, signed/tenant-scoped
events, direct references, empty-count fallback searches, exact request caps,
429 stopping, expired credentials, ID/store mismatches, result truncation,
duplicate reservations across workers, timestamp handling, and safe logs.
Five offline-sale regression tests also passed. No live stage-two lookup has
been run by the author, and no production or provider records were changed.
