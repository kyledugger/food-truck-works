Kitchen refresh and fallback update
==================================

Superseded by KITCHEN_SESSIONS_INSTALL.md: the current cumulative patch requires
the kitchen-session migration and uses fast polling only for connected active
kitchens. The immediate Refresh behavior below remains in place.

Copy the patch files over the corresponding project files and deploy/restart.
No database migration or environment change is needed.

Live fallback reconciliation now runs 30 seconds after the previous successful
check completes, rather than five minutes. It shares the existing organization
lease across stores, displays and server processes. Each scan retrieves the
current local day plus the comparison window; busy days may require multiple
paginated API requests. Provider failures retain a 60-second retry delay.

Refresh on the dedicated kitchen display and store performance page now makes
a CSRF-protected POST that checks Poynt, saves the results and returns the queue.
It waits for an existing check when another worker owns the lease. Errors and
timeouts are shown rather than reporting a successful refresh of stale data.
The test queue remains local and never invokes Poynt.

Verify with a paid order: wait for the fallback or press Refresh; check that its
ticket appears. This cannot fetch an order before Poynt makes it available through
the Orders API. The missing truck webhook remains a separate provider question.
