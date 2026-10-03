# First event reservations

Extract this archive into the project root, preserving paths. Confirm the
intended database and revision with `alembic current` before running
`alembic upgrade head`. Revision `84ac2e7b91d0` follows `7a2d9f10c6e4`.
This migration adds the empty `events` table. It does not modify existing
orders, tips, resources, or store settings.

After deployment, a manager can open **Event Reservations** from the dashboard,
choose an active booking resource, enter an event title, service dates and
times in the venue's IANA timezone, and enter setup/cleanup minutes. Saving
creates a confirmed reservation immediately. A confirmed reservation counts
against the resource's concurrent capacity over a half-open UTC interval from
setup start to cleanup end. Adjacent reservations are allowed; overlapping
reservations above capacity are refused. Cancellation frees capacity while
retaining the event record. Venue local times and timezone are saved alongside
UTC instants, so later store timezone changes do not alter the event.

The database stores timestamps as `timestamptz` and the venue-local intent as
`timestamp without time zone` plus an IANA timezone name. The service form
rejects nonexistent and repeated DST times and accepts overnight events when
the end date is the next day. All reservation creation paths must lock the
booking resource row before counting overlaps, as in `reserve_event()`.

This is an internal, manager-only reservation flow. It does not yet include
customer records, inquiries/quotes, editing, public booking, availability
hours, staff assignments, or automated event orders. Setup and cleanup are
elapsed minutes on the UTC timeline. Resource capacity is a count of
simultaneous confirmed events, not a staffing guarantee. Resource capacity
and store type cannot be reduced/changed while future confirmed events exist.

Run the focused tests:

```powershell
python -m unittest discover -s tests -p test_event_reservations.py -v
python -m unittest discover -s tests -p test_booking_resources.py -v
```
