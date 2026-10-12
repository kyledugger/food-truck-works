Kitchen service sessions
========================

Copy patch files into the project. This patch adds migration e8b3d95fa127 after
d7a2c84e9016. Back up your database using your normal process, then run in the
intended environment:

    alembic upgrade head

Restart/deploy the application after the migration. No production database was
changed while preparing this patch.

Existing and new stores start with kitchen tracking off. Old open tickets are
hidden; Start service archives them without counting completions and begins
admitting orders created from the start time onward. Sales collection remains
independent. Sessions are per store and shared across displays.

Start service does not search earlier orders. Get recent past orders appears
for 60 seconds, then remains under Kitchen options. Clicking it checks Poynt
and offers missing, paid/preparable orders from the 10 minutes before service
started. Staff can choose 30 or 60 minutes and select individual orders. Existing
tickets (including picked up, cancelled and archived tickets) are excluded.
Imports preserve original order age, names, notes and modifiers. Recovery can
cross local midnight; provider failures are shown rather than treated as empty.

Kitchen options includes Clear queue and End service, with bulk-action
confirmation. Both archive open tickets; End service also stops new admission.
Archived tickets cannot reappear through cache refresh or POS updates and can
be reviewed through Review cleared tickets (latest 100). They do not count as
completed items. Completion counts are limited to actions during the current
session. Off/awaiting-resume metrics show tracking status rather than zero.

Visible active displays refresh the shared session heartbeat. The provider
fallback remains 30 seconds after a successful check while at least one active
store display has been seen within three minutes; otherwise it returns to five
minutes for sales collection. Webhooks remain registered for sales collection.
After 15 minutes without a display heartbeat, returning staff choose Resume
previous service or Start fresh service. A brief disconnect does not end service.
Refresh still requests an immediate provider check, including while tracking is
off. Test kitchen remains independent and never calls Poynt.

Validation: isolated route/service tests, migration roundtrip, local-midnight
recovery, disabled admission, archives, scope/CSRF/revision checks, provider
intervals, and mocked browser start/recovery/import/end flow. Verify with real
paid orders after deployment; Poynt must expose them before FTW can fetch them.
