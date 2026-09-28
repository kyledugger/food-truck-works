# Single-store tip payout update

This package changes new tip submissions to one payout row per employee. It
keeps old submissions in their existing format, so historical records remain
readable. It does not backfill historical order claims or employee payouts.

## Install

Copy the modified `routers/poynt.py`, `tip_submission_model.py`,
`templates/orders.html`, and `templates/tip_submissions.html`, and the new
`templates/tip_settings.html` and
`alembic/versions/2b9e51c73a04_add_tip_payout_tracking.py` into the current
project. The revision follows `f1c3a8d72b40`. Apply it with your normal
Alembic workflow to the intended development environment before deploying
the application changes. No manual SQL file is needed.

The uploaded `poynt/client.py` already sends `timeType=createdAt` in both count
and page requests. Keep that behavior.

Verify the target database before applying the schema. Migrate a local
development database, then exercise the workflow below. Deploy the database
change before the application change. Application startup currently calls
`Base.metadata.create_all()`, but Alembic should own this schema change.

## Workflow

- Generate a report containing exactly one store and open Tip Calculator.
- Each employee defaults to paycheck. A manager can set a store to cash only,
  paycheck only, or employee choice at Tip Settings on the report.
- Submit the calculated ranges. The server fetches current Poynt orders,
  validates the range amounts and employee IDs, and claims every tipped order
  by Poynt business ID and order ID. A repeated claim returns HTTP 409.
- Cash and paycheck payouts begin pending. Payroll roles confirm paycheck
  payments. Owners/managers confirm cash payments, or the configured designated
  member does. In self-confirmation mode a linked employee can confirm their
  own cash payout.
- A manager can reject a fully unpaid new submission and release its order
  claims. A paid or partly paid submission cannot be rejected this way.
- The report's payment and status filters operate on employee payout rows for
  new submissions and on submission fields for historical records.

## Local verification

1. Confirm the order report fetches an updated order by `createdAt`.
2. Submit a two-employee range with an odd cent tip and check that both payout
   rows sum to the submitted tip amount.
3. Submit the same orders again and confirm HTTP 409 with no second payout.
4. Confirm that a member cannot mark paycheck paid, and that a designated
   cash confirmer has no payroll permission.
5. Test cash self-confirmation with an employee linked to their user account.
6. Reject a fully unpaid submission, then confirm its orders can be submitted
   again. Confirm rejection is blocked after any payout is paid.
7. Check historical submissions remain visible and their legacy status actions
   still work.

Later changes to a Poynt tip after it has been paid are not automatically
reconciled by this release. A separate adjustment workflow should compare
claimed order amounts against current Poynt amounts without silently moving
an order to another shift.
