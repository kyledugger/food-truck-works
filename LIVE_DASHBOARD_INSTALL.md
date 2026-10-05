# Live store dashboard

This patch replaces the dashboard's connection notice with a live overview of
each active configured Poynt store. Existing navigation, orders reporting,
payroll and Square integration code are preserved.

The latest update makes all dashboard sales figures include tax, with tips
still separate. If the date-picker migration `c03e5d9182a7` is already applied,
this calculation/label update requires no additional migration. Replace the
included files and restart/deploy to recalculate from the existing order cache.

## Install

1. Extract this ZIP into your Food Truck Works project root, replacing the
   included files. The ZIP contains only changed/new files, not a full source
   snapshot. Commit these changes through your usual workflow.
2. Back up the database and check the exact database target before running the
   migration. This package does not run a migration for you. The live revision
   `b92d7a10e643` follows `a61f0d8c3b92` (the Square integration foundation).
   The permanent date picker adds `c03e5d9182a7` after `b92d7a10e643`.
   If you installed the first dashboard patch, `upgrade head` applies only
   this additional historical-date migration.
3. Apply the migration in each environment you intend to use:

   ```powershell
   .\.venv\Scripts\alembic.exe upgrade head
   ```

   On the deployed service, the equivalent command is `alembic upgrade head`
   using that service's environment. `alembic/env.py` reads `.env`, not
   `DOTENV_FILE`; an explicitly set `DATABASE_URL` takes precedence. Confirm
   which database it points to before executing. Do not assume the local
   production launcher selects the migration database automatically.
4. Restart/deploy the application. No dependency changes are required. The
   background worker starts through FastAPI's lifespan; launch normally with
   Uvicorn, without disabling lifespan.
5. In **Store Settings**, ensure each tracked store is active and has its IANA
   timezone configured. Open the dashboard to initialize its persistent sync
   state. The first full collection can take longer than subsequent updates.

## Choose a date, including yesterday for testing

The date picker is permanent. No testing environment variable or code change
is needed. Choose October 4 directly in the calendar field to test yesterday's
sales. Previous/next arrows move one calendar day. Every date change and Today
click immediately refreshes the view. A past date is pinned in the URL for bookmarks.
The normal status banner is hidden; loading, setup and sync warnings still appear.
The top-right badge has a red dot during live operation. Items counts item quantities,
excluding returned items, beside Orders on each store card.

- **Today** uses each store's local day, updates automatically every fifteen
  seconds, and includes five-minute activity and current pace.
- **Past dates** show the complete day's totals, categories and all hourly
  buckets. They are labeled Historical view. The five-minute activity,
  current pace, recent-activity chart, and live-webhook enabling button are
  hidden. Historical API responses also return no current activity metrics.
- Historical days are fetched on demand. A small durable queue tracks whether
  the chosen date has loaded, so an empty cache is not mistaken for zero sales.
  The page polls while loading, then stops automatic polling. **Refresh**
  checks for updates; full historical fetches are cached for five minutes.
- Choose from the last 90 local calendar days, including today. Date navigation
  uses the stores' reported dates rather than guessing from the browser's
  timezone. Explicit dates apply to all stores. If that date is still today
  for one store and already a past day for another, only the current-day
  store keeps live activity.
- Clicking **Today** restores the live view. The worker continues collecting
  today's webhook updates while you review past sales. Historical date choice
  does not change the orders report or payroll date ranges.

The app defaults to Today when no date is in the URL. To start directly with
your testing day, use `/dashboard?date=2026-10-04`.

## Enable Poynt order webhooks

Add the following environment variables to the public server receiving
notifications:

```text
POYNT_WEBHOOK_URL=https://foodtruckworks.com/webhooks/poynt/orders
POYNT_WEBHOOK_SECRET=<a new random secret of at least 32 characters>
```

Generate your own secret locally, for example:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep the secret private and stable. `POYNT_APP_ID` must match the existing
Poynt application. Restart the service after setting these variables.

Sign in as an owner/manager and click **Enable order webhooks** on the
dashboard. This registers the callback for the currently selected
organization's Poynt business. Repeat for each organization. If an initial
sync is in progress, the button can ask you to try again shortly. The public
callback must be reachable by Poynt without an interactive login or browser
challenge. Local-only servers cannot receive these public notifications.

The dashboard works with five-minute reconciliation before webhooks are
enabled. Once enabled, notification processing normally begins within five
seconds and the visible page refreshes every fifteen seconds. Large initial
syncs and provider delays can increase latency. Webhook registration status
indicates registration, not a guarantee of delivery; old syncs and delayed
notifications are shown explicitly.

If changing the callback URL/secret or recreating a Poynt hook later, update
the registration in Poynt as well. This first version has an enable action,
not a hook rotation/removal UI. It assumes a Poynt business belongs to exactly
one organization; ambiguous ownership is rejected rather than shared.

## What the numbers mean

- **Sales including tax:** original order `netTotal`, reduced by recorded
  refund `orderAmount` including refunded tax. Tips are separate. Fees/shipping
  already included in `netTotal` remain in sales. The same calculation applies
  to daily totals, hourly charts, average sale, categories and five-minute sales.
- **Comparison with Orders Report:** both start with `netTotal`, including
  tax. This patch does not change that report; dashboard sales additionally
  subtract recorded refunds from their original sale.
- **Tips:** captured order tips minus refunded tips; multiple tenders are
  represented by aggregate order totals and do not create extra orders.
- **Categories:** existing SKU fixes/category mapping are reused. Unmapped
  items are Uncategorized. Tax, order discounts, fees and refund adjustments are
  allocated by item value so category sales reconcile to store sales. These
  are allocated totals, not provider-certified item-level refund accounting.
  Returned item lines are excluded from quantity; remaining quantity reflects
  the order's latest item state, not a separate refund quantity ledger.
- **Today:** local midnight to the next local midnight in each store's saved
  timezone. The current hour is partial. Repeated DST hours have separate
  offset-aware buckets and timezone abbreviation labels.
- **Activity time:** order creation time, consistent with the existing orders
  report. Updating a tip does not make an old order a new sale. Long-running
  orders appear in their original creation window when completed.
- **Last five minutes:** a rolling order count and sales total including tax. At
  midnight it can include the preceding day's last few minutes.
- **Pace:** last-five-minute order count divided by the preceding 60-minute
  order count / 12, including zero-order periods. A sale at or before the
  comparison hour is required to establish a history. Until then, show
  Building baseline. A zero baseline shows Activity starting. Ratios at
  least 1.5 are Busy; below .75 are Slower; otherwise Steady. No recent orders
  show Quiet right now. This is relative activity, not staffing utilization.
- **Currency:** this version displays USD. Other currencies are excluded with
  a warning instead of being added together.

The order cache retains recent reporting data for approximately ninety days;
processed notification IDs are retained for approximately three days.
No customer names, addresses, contact details, payment card data or tokens
are added to the order cache. Notifications store IDs and processing state.

## Reliability and access

Signature validation uses Poynt's raw-body HMAC-SHA1 signature. The receiver
stores notifications before acknowledging and deduplicates repeated IDs.
The worker fetches current order state using the authenticated Poynt client,
never a URL supplied by a notification. Unique order IDs and provider update
timestamps prevent duplicate counts and older snapshots overwriting newer
ones. Failed jobs remain queued for retry. A five-minute full reconciliation
recovers missed notifications and updates today's cached orders.

Sync leases in PostgreSQL serialize work for each organization across
Uvicorn processes, recover after interrupted workers, and fence expired
workers from committing. The worker runs inside the web service, so updates
pause while that service is stopped/asleep; queued jobs resume when it wakes.
Dashboard reads are organization-scoped and authenticated. Webhook enabling
requires owner/manager permissions and a same-origin AJAX request.

In today's view, browser polling updates existing card/chart objects, preserves expanded
panels, pauses in hidden tabs, resumes on return and marks stale data on
errors. Empty stores show No sales today. Missing store timezone shows setup
instructions. Missing migration returns a dashboard-specific setup error
without preventing the rest of the application from starting.

## Validation

Run from the project root, against an isolated test environment:

```powershell
python -m unittest discover -s tests -p test_live_dashboard.py
python -m unittest discover -s tests -p test_store_time.py
python -m unittest discover -s tests -p test_tip_report_totals.py
```

The new tests explicitly use in-memory SQLite and mock provider requests;
they do not contact Poynt or a production database. They cover monetary
calculations, zero sales, category allocation, refunds, five-minute boundaries,
local midnight, multi-timezone dates, 23/25-hour DST days, timestamp validation,
tenant isolation, signed webhook validation, duplicate notifications,
durable retry/processing, stale order updates, lease exclusion, authorization,
full reconciliation, missing schema, and isolated migration upgrade/downgrade.

Live Poynt delivery and PostgreSQL multi-process locking still need checking
in your deployment. Complete a test sale, verify one order appears, change
the tip, and verify tips update without increasing the order count. Check
sales against the POS's total including tax (excluding tips) and verify a second store stays separate.

Validation completed for this patch: 38 dashboard tests, 4 existing store
time tests, 6 existing tip report tests, and 9 existing integration tests
passed. The existing integration migration test was excluded because it
resolves its migration relative to the parent of the project directory;
that unrelated test has not been changed. Application import, Python syntax
and JavaScript syntax checks passed. Browser checks with synthetic orders
passed for real Chart.js rendering, desktop/phone layouts, live refreshes,
preserved panels, webhook enabling, stale-data warnings, historical selection,
date persistence on reload, hiding live metrics for past dates, and returning
to Today. The source ZIP
does not include logo PNG assets, so branding images could not be checked.

Latest presentation update: removed dashboard headings, combined date controls and
right-aligned status in one toolbar, shortened Sales/Average sale/Hourly Sales labels,
and clarified chart gridlines. The shared base template forces Bulma light mode and
all logo templates use their normal artwork, regardless of device theme.
No additional database migration is needed.

## Live store display

Click a store name on the organization dashboard to open `/dashboard/stores/{id}`.
This view always shows today in that store's timezone and uses the same 15-second
refresh and webhook-backed cache. Use Full screen for an on-location display.
The newest 20 orders appear first, including open, completed, refunded and cancelled
orders; the day totals continue counting completed USD sales. The feed includes
order number/time/amount/tips/status and item name/quantity/SKU/status. Customer,
contact, payment details and free-text notes are not copied into the dashboard cache.
Item names and order numbers populate on the next ordinary sync (normally within
five minutes); older cache entries fall back to SKU/order ID until refreshed.
Category totals' product button opens a modal with today's SKU quantity chart/table,
excluding returned items. Escape or Close dismisses it. On desktop the dashboard
fits the viewport, with internal scrolling for long order/category lists; smaller
screens stack the panels. Processing-time metrics are deferred; order creation
intervals are available but do not represent measured kitchen/preparation duration.

Access follows existing organization dashboard membership permissions; this is not
yet a separate restricted kiosk session or role. Both page and data endpoints check
store ownership and active status. No additional migration or webhook registration
is needed. Browser checks cover the feed, refresh, SKU modal, Escape dismissal,
desktop viewport fit, mobile layout and empty stores. 38 dashboard tests pass,
including tenant boundaries, feed limits, item fields and SKU eligibility.
