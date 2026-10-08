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
   `python -m alembic upgrade c69e1a47f358` before restarting workers.
4. Restart the app. No new dependency or environment variable is required.
5. Open Kitchen Display from Store Home, or click Kitchen orders in Store Performance. Test with two displays.

No production database operation was performed while preparing this patch.
The migration adds kitchen_tickets and kitchen_actions. It does not change POS
orders, sales, tips, users, existing roles or employee identities.

## Operation

- Anonymous row states: Available (white / circle), In progress (blue / half
  circle), Done (gray / checkmark and strikethrough). Labels supplement color.
- Claim covers every unit on a row. Release returns a claim to Available.
- Done completes a row. The final Done makes the order Ready and removes it
  from the active queue. Tapping the card header completes all rows without
  confirmation. Ready does not mean Delivered.
- Recently completed shows the latest 20 Ready orders from the preceding two
  hours. Undo restores their done rows to Available. Event history is retained
  beyond that recovery display window.
- Active orders are oldest first, with no 20-order limit, inside the existing
  scroll area. Unfinished tickets carry over midnight; daily sales stay Today.
- Initial activation admits today's cached completed orders. Historical report
  loads never create old tickets; previously admitted old tickets remain active.
- Open/draft and cancelled orders are not added. Returned/cancelled item rows
  and zero quantities are omitted. Fully cancelled tickets leave the queue.
- Names, SKUs and quantities use the existing safe order cache. Modifiers and notes are retained as described below. This does not
  write fulfillment state back to Poynt.
- Provider item IDs identify rows when available. Legacy rows use SKU/name plus
  occurrence; a unique match upgrades to provider IDs without losing progress.
  Ambiguous edits may reset a row conservatively. Quantity/product changes
  return that row to Available. Other unchanged rows retain their states.
- Original UTC order creation timestamps drive elapsed timers; timers tick
  locally every second, anchored to server time. Ready durations are frozen.
- Store Display accounts can access only their assigned store; kitchen access does not require showing
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
to the active kitchen queue. Only the embedded customer name and customerUserId reference are retained; full customer/contact/payment objects are not added to the cache.

## Compact kitchen tickets

Kitchen tickets show elapsed time, order note, quantity beside the
item name, selected modifiers, and preparation controls. SKU, POS status, prices,
and calendar timestamps are omitted. Item text is approximately 18px, modifier
text 17px, and callout notes 20px at the default browser font size. Preparation
state labels remain visible to coordinate claims and completions. No additional
migration is required for this layout update.

## Landscape tablet layout

Landscape displays at least 850 CSS pixels wide use a compact two-column layout.
Sales/tips/order/item totals share one row; hourly sales and category totals sit
side by side, followed by intake/completion and order pace. Browser checks at
1280x625 and 960x650 confirm both flow metrics and all three pace groups fit
without scrolling the page. Long category tables and kitchen queues scroll in
their own panels. Shorter viewports retain independent left-panel scrolling.
Kitchen item/modifier/callout font sizes and touch controls remain legible.
Portrait/mobile layouts remain stacked. No database migration for this change.

## Responsive tactical-first layout

Hourly sales and category totals now appear below the tactical summary and
kitchen queue. Category rows grow naturally with the page, without a nested
scrollbar. Tablet landscape stays compact; tablet portrait stacks the summary
and queue while preserving paired flow cards and three pace groups; roomy PC
viewports use larger metrics and charts. The kitchen queue retains an independent
scroll area. Validation covers 1280x625, 960x650, 800x1100, 1920x1080 and mobile,
with 18 categories and 25 kitchen tickets, including access revocation cleanup
of the relocated sales breakdown. No additional migration is required.

## Dedicated kitchen display

Kitchen Display opens from Store Home without showing sales. The Kitchen orders
heading in Store Performance links to the same screen. Cards scroll horizontally
with the oldest order on the left; touch scrolling and a Full screen
button are available. Existing claims, modifiers, notes, elapsed timers, Ready,
Undo and live updates use the shared kitchen queue. Recent completions remain
collapsible. The assigned-store restriction applies to this page and its data.

## Kitchen practice mode

Open Kitchen Display and click Test kitchen. The yellow TEST MODE banner marks
the separate, shared test queue for this store. Use Add order, Add 10 orders,
or Add large order (12 rows) to exercise layout, modifiers, notes and timers.
Mixed orders range from one to five rows, with varying quantities. A batch has
staggered elapsed times. Names and menu items are synthetic examples.

Start stream adds an order immediately and then every 10, 25 or 60 seconds.
Stop stream stops further additions from this browser. Navigating away or
backgrounding this browser also stops its stream. Run one stream on one device;
other devices opening Test kitchen for the same store share its orders, claims
and completions. Refreshing the page never starts a stream automatically.

Clear test orders removes this store's sample tickets and their sample action
history only. Stop streams on other devices before clearing; another running
stream can add new sample orders afterwards. Live kitchen returns to real work.
Test tickets persist across reloads until cleared. Active test orders are capped
at 100; generation pauses on errors or a full queue.

Test orders use a dedicated business namespace in existing kitchen tables. They
never enter DashboardOrder, POS, sales, tips, or real intake/completion metrics.
Claim/Done/Release/Undo, revision conflicts, SSE and polling use the same code as
real tickets, with the same role, assigned-store and CSRF checks. A Poynt
connection is not required for the simulation. No additional migration is needed.
This tests local preparation and display behavior, not Poynt webhook delivery.

## Separate customer names

Apply b58d0f36e247 after a47c9e25d136; it adds a nullable customer_name column.
Embedded Poynt customer firstName/lastName (or nickName) display above the order
number, with order notes separate underneath. Without an embedded name, the
order note remains the header callout. Name changes preserve preparation state.

The POS Customer Name button has not yet been verified against a raw order
created with that field filled in. Poynt documents customerUserId as well as an
embedded customer object, but a reference alone is not a name. This patch does
not add customer API requests. Previously cached orders must be fetched again
to acquire newly retained name fields.

To inspect an exported raw order locally, run:
`python tools/inspect_order_customer.py path/to/order.json`
The diagnostic prints relevant customer/name references and notes without
printing payment or full contact data. If only a customerUserId is returned, a
separate customer lookup will need verification before relying on it.

## Fast kitchen interaction and timer profile

Claim turns the row blue immediately on this display, with a small Saving
indicator while the request is outstanding. Other orders remain actionable;
item actions on the same order queue while saves proceed in sequence.
Periodic updates do not erase a pending claim. Conflict or save failure restores
the server state, with an error in the status line. If the queue cannot be read,
actions pause until it reconnects. Other displays change after the server saves
the action; optimistic feedback does not bypass revision checks or coordination.

Active card headers have two separate targets: tap the name area to complete all items
without confirmation. The separate Complete order and Claim available buttons,
and the visible order number, are removed. Recently completed retains Undo.
Order identifiers remain in accessible labels and the backend for troubleshooting.
Item buttons are removed: tap anywhere on an active item row to cycle Available
to In progress, then Done, then back to Available. Enter or Space does the same
with keyboard focus. Each tap shows its new state immediately with a Saving
indicator; further item taps queue while those changes save.
Completed orders leave the active queue, so use Recently completed / Undo to
restore an order whose final item was completed accidentally. Inset item dividers
do not increase row height. Elapsed timers omit their title and use a larger badge.

An owner/manager can open Timer profile on Kitchen Display. Each store has one
shared profile, also used in Test kitchen and the store dashboard's queue.
Green defaults to starting at 0 minutes, yellow at 5, and red at 10. Thresholds
must increase and may use half-minute increments, up to 1440 minutes. Before the
green threshold the badge is neutral. A circle, triangle and exclamation mark
supplement the green/yellow/red colors. Ready timers freeze at completion time.
Store Display accounts consume the shared profile but cannot edit it.

## Compact cards and Hold

Kitchen-only cards are 290px wide on tablet and desktop (narrower on very small
screens). Timer badges use 1.0rem and share a line with the name. The page
background is #cccccc; normal headers are white and completing/Ready headers
are gray. Item/preparation headings
and visible item state labels are removed, so items use the full card width.
Blue/double-border claimed rows and gray/struck-through done rows preserve
visual distinction. Accessible labels still describe the next item action.

Tap the timer to Hold an active order, then tap it again to Resume. This is
preparation hold, not a stopped clock: order age and timer colors keep advancing.
Held cards show Held and a dashed outline, and their item rows and completion
header are disabled. Existing item progress is preserved. The server also
rejects preparation/completion while held, including stale-screen attempts.
Hold is shared across displays through the same revision checks and live
updates, and applies in Test kitchen too. Clicking the timer never triggers
header completion. Cancellation can still remove a held POS ticket.

Hold uses the existing ticket state field; this update requires no additional
migration beyond the previously supplied timer profile migration. Keep your
locally generated merge migrations when copying this cumulative patch.

## Rapid item taps

Quantities omit the multiplication sign and use weight 800. Modifiers use
#222222 with lighter font weight. Older/Newer controls and their navigation row
are removed; swipe or horizontally scroll the card area to browse.

Item taps are now queued per order. The whole sequence appears immediately,
including repeated taps cycling one item, while each order's saves proceed in
sequence with current revisions. Different orders save in parallel. Item rows
stay tappable during saves; whole-order completion and Hold wait for that order's
pending item taps. Saving indicators distinguish unconfirmed changes. A card
remains active while it has queued taps, including a queued Undo after its final
Done, then moves to Recently completed when its confirmed final state is Ready.

Successful action responses include the saved ticket, eliminating the previous
full-queue request between each action. SSE/polling still reconciles other screens
and POS updates. Failure, timeout, changed item details or revision conflict
cancels the remaining queued taps for that order and refreshes its saved state;
uncertain actions are not automatically retried. Queues are in browser memory,
not an offline outbox: keep the page open until Saving indicators clear.
This improves tap responsiveness and avoids an extra network round trip. Actual
save throughput still depends on server/database/network latency. No additional
migration is required for this change.

## Stable saving and completion feedback

The saving text is replaced by a tiny red spinner over the right edge of each
pending item. The item gutter is always reserved, so pending feedback does not
change wrapping or card height. Reduced-motion displays use a stationary dot;
accessible saving labels remain present. Quantities have two nonbreaking spaces
before the item name.

When all items are optimistically Done, the pending card has a gray header,
struck-through customer name and muted items. It remains in the active queue
until its queued saves finish. Queued Undo or failure clears the completing
appearance as the row states return. Normal/in-progress header backgrounds are
white; claimed item rows retain their blue preparation distinction. No migration.

## Names in notes and Store Home

When a separate customer name is present, it wins and the full order note remains
instructions. Otherwise, a leading `Name: Skye Dugger | No coconut` or
`Name: Skye Dugger` followed by a newline and instructions provides an explicit
multi-word name. Without that prefix, the first whitespace-delimited word is
treated as the name and the remaining words become the order note. This is a
display convention, not name recognition: instruction-only notes also follow
that first-word fallback. The original cached POS note is preserved unchanged.
Active, Held and Recently completed tickets use the same display parsing in both
kitchen views. The performance navigation button now reads Store Home, retaining
the existing sales-clearing/privacy behavior. No migration required.

## Performance test queue and Customer Wait

Click Test queue in the Performance screen's Kitchen orders heading. Its queue
uses the same sample tickets as the dedicated Test kitchen display, so actions
sync between both screens. The drilldown link preserves test mode. Expand TEST
QUEUE / Simulation controls to add orders, run a stream or clear samples; collapse
it to preview the compact kitchen layout. Live queue returns to real tickets.
Sales, tips, hourly/category charts and cashier intervals remain real. Test-mode
intake, completion and Customer Wait use samples and are explicitly labeled Test.

Customer Wait is beside intake and completion. It shows the current age of the
oldest unfinished ticket that is not Held, including unfinished tickets carried
over midnight. It advances locally each second using the server clock and switches
immediately when an order is held, resumed or completed. All-held/empty queues
show a dash and No active orders off hold. Unavailable/revoked queues clear the
metric. This is observed oldest-order age, not a predicted time to completion.

Performance test mode retains the existing sales visibility gate and assigned-
store role restrictions. No additional migration or dependency is required.

Apply c69e1a47f358, which follows b58d0f36e247 and creates kitchen_timer_profiles.
No production database was accessed. If you created a merge revision after the
previous patch, this new migration may create a second head next to that merge.
After copying the patch, run `alembic heads`. If two heads are listed, merge
them using `alembic merge -m "merge kitchen timer profile" FIRST_HEAD SECOND_HEAD`,
replacing the placeholders with those listed revision IDs, then run
`alembic upgrade head`. Include the generated merge file in your deployment.
# Editable kitchen notes (October 8)

Tap the small note icon beside the timer on an unfinished order in either kitchen queue. Edit the customer name and preparation note independently. Existing single-word POS note names populate the name field automatically. Saving updates the card immediately and syncs other displays through the existing queue updates. Held orders can also be edited.

These edits are FTW overrides, not POS updates. Original POS notes are retained, and later POS synchronization preserves the kitchen edits. Blank fields deliberately clear the displayed name or note. Concurrent revisions reject a stale edit instead of overwriting another screen's work; reopen the editor after a conflict.

The word `allergy`, case-insensitive, in original notes, kitchen notes, customer names, item names, or modifiers adds a yellow outline and an ALLERGY label. The original-note warning remains even if the displayed note is edited. This highlights written information, not inferred allergens.

Apply the new migration to your intended development database before starting the updated app:

```powershell
alembic upgrade head
```

Revision `d7a2c84e9016` follows your supplied merge `a4fdd72edc57`. It adds two nullable text columns to `kitchen_tickets`. No migration has been run against your database. Claim Tips has been removed from the Store Performance toolbar; Store Home remains unchanged.

