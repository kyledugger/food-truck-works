# Booking resource foundation

Extract this archive in the project root, preserving its paths. Confirm the
target database using `alembic current`, then run `alembic upgrade head`.
Revision `7a2d9f10c6e4` follows `59d6e1c8a403`.

The migration creates `booking_resources` and adds one enabled resource with
capacity 1 for each existing Food truck, Food trailer, Cart, or Pop-up store.
It does not change Poynt stores, orders, tips, or existing booking records.
Shop and Catering stores do not get a resource by default.

On future Store Settings saves, mobile types acquire one resource automatically.
The manager does not have to define it. Shop managers may enable one named
space with capacity 1. Catering managers may enable a named service with a
concurrent-event capacity from 1 to 100. A disabled resource remains stored
so its ID survives future booking configuration changes.

This release defines capacity and identity only. It does not create event
reservations, schedules, availability hours, or public booking controls.
Future availability checks should require both an active store and an enabled
resource, count overlapping reservations against `capacity`, and include
setup and cleanup time in reservation intervals. Store opening hours are
separate from booking availability.

Run the focused test with:

```powershell
python -m unittest discover -s tests -p test_booking_resources.py -v
```
