Tip history and split approval
==============================

Copy patch files into your project, run `alembic upgrade head` in the intended
database environment, then restart/deploy. Migration f9c4ea60b238 follows
e8b3d95fa127. No production database changes were made during preparation.

Tip History now chooses its view from the authenticated account:

* Employee: only that employee's allocations and the split ranges they took
  part in. Co-participant names/amounts are visible for those ranges, while
  other participants' payment methods/statuses are private. Any date range is
  allowed. Totals include only the viewing employee; rejected tips are excluded.
* Store Display: assigned store only, today's shifts only, read-only. Store
  Home includes Tip History. Store/date are labels rather than editable fields;
  requests for another store or day are rejected by the server.
* Owner/manager/payroll: All stores by default, optional per-store filter,
  payment/status filters affecting both totals and list. Store managers see only
  their assigned managed stores. Employee aggregates combine amounts across
  selected stores. Paid/reject actions retain the selected scope and filters.

Dates select the submission's shift/window starting date in each store's own
timezone, rather than its submission date. Overnight windows belong to their
starting day. Historical payout methods are recorded choices, not the employee's
current preference. Dates/times in mixed-store rows use their respective zones.

Employee and Store Display history have no Paid or Reject controls. The backend
also denies those mutations. Employee self-confirmation is no longer supported
through history, even if an older store setting said `self`.

Submission always shows a review before saving. Tip Settings offers Require
authorized approval before submitting a tip split, disabled by default. Enable
it per store and optionally choose a designated split approver. Approvers use
their FTW email/password: an active owner/manager, that store's manager, or its
designated approver with current organization membership. Their password is
verified for this exact submission, never stored, and attempts are throttled.
The record retains approver ID/time and a digest of the verified split.

Approval does not imply payment. If cash has actually been handed out, an
authorized person can explicitly confirm this in the review. This marks all
cash allocations in that split Paid; paycheck allocations remain Pending.
Otherwise cash stays Pending for payroll/management confirmation. Individual
employee receipt acknowledgment is not required. No remote approval inbox or
separate PIN system is included in this version.

Verification covers role/store isolation, employee totals/privacy, all-store
aggregates, date restrictions, filter behavior, password/approver checks,
throttling, approval without payment, explicit cash payment, migration/data
preservation, existing claim flows, and browser review-before-submit behavior.
