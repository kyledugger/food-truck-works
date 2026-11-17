# Additional payments on the dashboard

Apply this change package over `source_package(7).zip` plus the automatic transaction webhook update (including its APP_BASE_URL correction). It contains only the files changed for this feature. It does not replace the webhook setup package.

## What you will see

Every owner/manager store card gets a separate **Additional payments** section. The expandable payment breakdown shows each payment's source label, time in that store's timezone, short transaction ID, sales, and individual tip. It remains visible even when the store has zero order sales.

- Existing order Sales, Tips, Orders, Items, hourly charts, category breakdowns and Orders reports are unchanged.
- Each store gets **Day total (excluding tips)**, combining its existing order sales and eligible additional payment sales.
- The top **Total revenue for the day** includes those combined store sales. It indicates partial or unverified totals while payments load or need review.
- No additional payment tips are aggregated with order tips or payroll. No employee tip distributions or tip approvals are changed.
- Staff kitchen/store-display endpoints do not receive this new payment data. Existing dashboard permissions continue to apply.

On **November 2, 2026**, the Ice Cream Crush Truck card should show the known **$367.54 Pay link**, with **$0.00 tip**, after collection completes. The payment time is 6:56:54 AM America/Phoenix. On **September 12, 2026**, the known **$243.11** invoice payment should appear as **Additional payment** unless the provider supplies an explicit invoice reference. These examples are acceptance targets from the diagnostic evidence, not hardcoded into application logic.

GoDaddy's transaction record for the invoice did not expose its invoice number/title. A payment without a source identifier is deliberately labeled **Additional payment**. Pay links are identified by `checkoutUrlId`; an explicit invoice reference can produce **Invoice payment**. The application does not guess a customer, event, or invoice number from the amount.

## Collection and request cost

- The existing 15-second browser refresh reads the local cache; it does not make transaction API calls.
- On first viewing a date, the dashboard queues a separate background collection. Small dates normally take two provider requests, one order identity scan and one transaction scan, at least 15 seconds apart. The initial display can therefore say **Loading additional payments** for several refresh cycles.
- Today is checked again 15 minutes after collection completes, while the date remains requested. Historical dates are checked daily while requested, or through the existing dashboard Refresh button.
- Collection stops when the date has not been requested for 30 minutes and resumes on a later visit. Navigating to an old date triggers its first collection; there is no expensive automatic 90-day backfill.
- Collections are business-wide, with per-store local-day filtering. More than 100 orders/transactions requires pagination. Refunds can require a GET for the original payment to establish attribution.
- At most one collection GET runs per worker turn, with at least 15 seconds between collection requests per business (at most four/minute). These reads also use the existing shared transaction-trigger lookup budget and provider rate-limit backoff. Normal order polling is unchanged and is not included in that lookup-budget counter.
- Kitchen notifications and ordinary order collection run first under the existing organization lease. A transaction collection request has the existing five-second signal GET timeout. Under a busy/exhausted lookup budget, payments wait rather than bypassing the limit.
- A partial or failed scan never replaces the last complete snapshot with a misleading empty/partial result. Saved results remain visible with a verification/error message.

## Counting rules

Payments are assigned by transaction `createdAt` in the store's timezone, not event date, invoice date, settlement date, or the time FTW downloaded them. A deposit received November 2 belongs to November 2 even if the event was November 12.

Captured SALE/CAPTURE records are eligible. Pending authorizations, failed payments and voids are excluded. A separate successful refund reduces additional sales and its individual tip on the refund's own date. The original payment is fetched when needed to verify the refund's store/source/order association. Parent/capture families and exact identities prevent counting an authorization and its capture twice.

Each date's orders are scanned for transaction IDs before publishing its payment snapshot. Exact order-payment links are also retained from normal order ingestion across dates. Payments with a `POYNT_ORDER` reference, an indexed order transaction ID, or a linked parent payment are excluded. Amount and timestamp similarity are never used to deduplicate.

This depends on the provider reporting a relationship: an older order absent from FTW's cache and without any provider order reference cannot be proven linked. Unknown standalone payments may therefore appear as **Additional payment**, including manual payments. The source field alone is not used to guess whether a payment is an invoice or belongs to an order. If an order link arrives later, the payment is automatically excluded on the next dashboard read. We should verify a few known payment examples after deployment before treating combined totals as an accounting reconciliation.

Only USD is added. Missing/invalid amounts, conflicting parent/store identity, and unverifiable refunds are excluded with a review count where store attribution is known. Card details, funding sources, customer/contact information, notes and processor responses are never stored in this cache. Order-transaction identity links are retained separately to support cross-day deduplication. Payment day snapshots older than 91 days are pruned during collection.

## Deployment

No new environment variables, webhook registrations, or subscriber configuration are required for additional payments. This feature works for all configured active stores independently of kitchen transaction-trigger store selection.

This patch adds migration `c27a90e51f68`, after automatic webhook settings revision `b13d82e69a04`. It adds three tables: `additional_payment_days`, `additional_payment_controls`, and `dashboard_payment_links`. No production database or live provider records were changed while building this package.

1. Extract the package into your local FTW project, preserving directories. Keep the deployed application on its previous code until the migration is applied.
2. Run the read-only preflight against your explicitly chosen database:

   ```powershell
   .\.venv\Scripts\python.exe .\apply_additional_payments_migration.py --dotenv .env.local-prod-db
   ```

3. If it reports current revision `b13d82e69a04` and passes, explicitly apply the three new tables to that same target:

   ```powershell
   .\.venv\Scripts\python.exe .\apply_additional_payments_migration.py --dotenv .env.local-prod-db --apply
   ```

   If the revision differs, stop and review the output. Do not stamp the database. If the earlier automatic webhook settings migration has not been installed, complete that update first; use its original package/helper before overlaying this new migration.

4. Deploy the changed application files. The dashboard template has updated JS/CSS version markers to avoid retaining the previous assets.
5. Open November 2, expand the Truck card's Additional payments breakdown, and allow collection to finish. Confirm $367.54 sales and $0.00 tip, with existing order sales unchanged. Then check September 12 and a date with both ordinary POS orders and additional payments. Test a known refund if available.

## Validation

Local SQLite and mocked API tests cover the known paylink/invoice amounts, individual tips, order sales preservation, exact same/cross-day deduplication, refund-date handling, voids, non-USD/invalid amounts, parent lookups, pagination, atomic snapshots, throttling, 429 backoff, privacy allowlisting, permissions, local midnight and 23/25-hour timezone boundaries. The actual Alembic preflight and upgrade were exercised on a temporary local database.

The existing 226 kitchen regression tests and transaction-trigger tests passed. JavaScript syntax and DOM checks cover a payment-only store, combined totals, individual tips, timezone formatting, and loading/partial states. A full browser screenshot was unavailable in this environment; no live GoDaddy account or production database was used for testing. Live acceptance checks above are still required.

Provider reference: https://docs.poynt.com/api-reference/index.html
