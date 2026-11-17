# Additional payments display update

Extract into the existing FTW project root, preserving directories, and deploy the three changed files. No migration or environment changes are needed. Dashboard asset version markers are updated to refresh cached JavaScript/CSS.

- While loading, the additional-payments section shows only “Loading additional payments…”.
- With no additional payments, it shows “No Additional sales on this date.” in the existing empty-state style and retains the checked time/status. No day total or redundant heading/count is shown.
- With additional payments, the existing breakdown remains, with “Day total: $…” on its first line and “excluding tips” as lighter subtext underneath. Actual payment records determine visibility, so offsetting payments/refunds can still be reviewed even when their net total is zero.

Order metrics, payment collection and revenue calculations are unchanged. Verified JavaScript syntax and empty/loading/populated DOM states, including a store with ordinary sales but no additional payments.
