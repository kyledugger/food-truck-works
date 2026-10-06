# Store kitchen queue

Built against the current source_package_1(3).zip supplied in this conversation.
This is a focused patch, not a full source export. Copy its application files
to matching paths. Existing token/client changes, Hide Performance, Claim Tips,
store home, sales metrics and role restrictions are preserved.

## Installation

1. Stop the app and order worker while deploying this patch.
2. Copy files to the matching locations in your application.
3. Check the intended database environment and its Alembic revision. This
   migration follows e25a7c03b914. In that selected environment, apply
   `python -m alembic upgrade a47c9e25d136` before restarting workers.
4. Restart the app. No new dependency or environment variable is required.
5. Open Store Performance from the store home screen and test with two displays.

No production database operation was performed while preparing this patch.
The migration adds kitchen_tickets and kitchen_actions. It does not change POS
orders, sales, tips, users, existing roles or employee identities.

## Operation

- Anonymous row states: Available (white / circle), In progress (blue / half
  circle), Done (gray / checkmark and strikethrough). Labels supplement color.
- Claim covers every unit on a row. Claim available on an order leaves already
  claimed/done rows alone. Release returns a claim to Available.
- Done completes a row. The final Done makes the order Ready and removes it
  from the active queue. Complete order completes all rows; the browser asks
  for confirmation if unclaimed rows remain. Ready does not mean Delivered.
- Recently completed shows the latest 20 Ready orders from the preceding two
  hours. Undo restores their done rows to Available. Event history is retained
  beyond that recovery display window.
- Active orders are oldest first, with no 20-order limit, inside the existing
  scroll area. Unfinished tickets carry over midnight; daily sales stay Today.
- Initial activation admits today's cached completed orders. Historical report
  loads never create old tickets; previously admitted old tickets remain active.
- Open/draft and cancelled orders are not added. Returned/cancelled item rows
  and zero quantities are omitted. Fully cancelled tickets leave the queue.
- Names, SKUs and quantities use the existing safe order cache. This does not
  add modifier/guest-note collection or write fulfillment state back to Poynt.
- Provider item IDs identify rows when available. Legacy rows use SKU/name plus
  occurrence; a unique match upgrades to provider IDs without losing progress.
  Ambiguous edits may reset a row conservatively. Quantity/product changes
  return that row to Available. Other unchanged rows retain their states.
- Original UTC order creation timestamps drive elapsed timers; timers tick
  locally every second, anchored to server time. Ready durations are frozen.
- Store Display accounts can access only their assigned store after choosing
  Store Performance. Owners/managers can use their organization's stores.
  Member/payroll personal accounts are denied. Mutations require a session
  CSRF token, checked store access and an expected ticket revision.
- Claims are anonymous. Actions retain the authenticated account for security
  auditing, but do not identify the individual employee using a shared tablet.

## Live updates

An SSE connection checks a compact database queue signature every second,
then tells the display to fetch a changed queue. This works across Uvicorn
processes without an in-memory message bus or another service. Connections
revalidate account/store access each check and reconnect every 60 seconds.
Sessions release their DB connections between checks. Buffering is disabled
with X-Accel-Buffering: no. The queue also refreshes every 5 seconds as fallback;
its actions pause on refresh failure. Sales keep their 15-second fallback and
refresh on kitchen notifications. The existing order worker now wakes every
second.

New-order visibility still depends on Poynt delivering its webhook and the
worker fetching/saving that order. This does not eliminate provider latency.
At large deployment scale, a dedicated PostgreSQL notification or pub/sub
transport can replace the per-screen signature checks without changing the UI.

## Validation

Isolated SQLite tests cover row and order actions, revision conflicts, atomic
compare-and-swap, preserved claims, changed quantities, cancellation, cache-ID
upgrades, more than 20 tickets, midnight carryover, historical exclusion,
role/store/CSRF checks, store-home privacy, revoked display access, durable
SSE change detection and migration upgrade/downgrade. Existing dashboard,
store-time and order-pace tests pass. PostgreSQL production was not accessed.
Browser checks cover two screens, conflict recovery, automatic Ready, Undo,
local timers, 25 tickets, responsive layouts, preserved privacy/tip controls,
outage recovery, notification fallback and clearing revoked data. Browser
notifications use a deterministic EventSource fixture; the real SSE generator
is covered separately in backend tests.

Run from the application directory:
`python -m unittest discover -s tests -p test_kitchen.py`
`python -m unittest discover -s tests -p test_live_dashboard.py`

## Kitchen flow comparison

The store display replaces the orders-per-five-minutes chart with paired Kitchen
intake and Kitchen completion rates. Both use the same rolling 15-minute window,
including quiet time, and update with every kitchen queue refresh. Intake counts
arriving completed POS order quantities. Completion counts item quantities marked
Done, including partially prepared orders; Undo removes those quantities. Rates
span midnight and exclude cancelled tickets and completions outside the window.
The completion query is independent of the recent-orders display limit. Completion
means ready, not delivered, and the rate comparison does not measure backlog.
The flow comparison itself needs no migration; the customer-callout notes update below does.

## Modifiers and customer callouts

Selected modifiers are preserved from Poynt and shown below each item in the
kitchen queue and orders report. Multiple selected values are supported; display
labels replace underscores with spaces. Modifiers remain part of their parent
item and do not inflate item flow counts. A modifier change resets only the
affected row to Available, including reopening a Ready order. Order-level notes
are labeled Order note and displayed prominently for customer callouts on active
and recent tickets. Name/note edits preserve preparation progress.

Apply the additional a47c9e25d136 migration, which follows f36b8d14c025 and adds
a nullable notes column to kitchen_tickets. If the kitchen migration is already
applied, only this additional migration is needed. Modifiers use existing JSON
storage. Previously cached orders omitted these fields and must be fetched again
from Poynt; historical reports can fetch them without admitting historical tickets
to the active kitchen queue. No customer/payment objects are newly collected.
