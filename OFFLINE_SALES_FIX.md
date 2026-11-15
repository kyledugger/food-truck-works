# Offline completed sales and consistent report rules

Apply to source_package_1(8).zip. Copy the included files into your FTW project,
preserving their paths, then restart/redeploy FTW. No migration is required.

Changed application files:
- live_dashboard_metrics.py
- live_dashboard_service.py
- routers/poynt.py

Regression tests: tests/test_offline_sales.py

## Diagnosis

The supplied November 14 diagnostic contains 18 Ice Cream Crush Store orders.
Five have completed payment summaries and total $38.25. The other 13 have
order status COMPLETED, payment summary NONE, no attached transactions, and
total $119.99. All 18 sum to the GoDaddy report's $158.24.

The Orders report formerly required a transaction-level store ID and a
COMPLETED payment summary. The dashboard used the order-level store ID but
also rejected these completed orders because their payment summary was NONE.
Different exclusions happened to produce identical revenue totals.

## Behavior

Both paths now use the same store resolver: order context first, otherwise
one unambiguous transaction store. Completed sales include finalized orders
without attached transactions when the payment summary is absent or NONE.
This does not claim that a payment was captured. Open/cancelled orders and
explicit pending/declined payment summaries remain excluded. Captured tips
are never inferred from missing payment data.

Orders report revenue, tips, and revenue chart calculations now use the same
integer-cent money helper as the dashboard, including refund subtraction and
the fallback from null netTotal to orderAmount. The dashboard cache retains
only a boolean indicating transaction presence for this decision, without payment IDs or
card details. No schema changes are needed.

## Verification

- Replayed all 329 records from the supplied diagnostic through the actual
  report filtering/calculation functions and dashboard summarizer: both
  return 18 store orders and $158.24.
- Added regression checks for this distribution, open/cancelled/failed orders,
  unconfirmed attached payments, conflicting stores, refunds, and null totals.

After deployment, rerun Orders for November 14 and Ice Cream Crush Store.
The dashboard uses a cache; an already-loaded yesterday may wait for its
next historical refresh (normally hourly). No database edits are required.

## Timestamp limitation

The supplied API records show creation times around 2:22-2:23 PM, including
the missing orders. This patch does not invent the original morning sale
times or map the API order numbers to GoDaddy's printed numbers. Hourly
charts, event-window reporting, and tip windows still follow those API times.
Recovering original sale times requires additional provider evidence.
