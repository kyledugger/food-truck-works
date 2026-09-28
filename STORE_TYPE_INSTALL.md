# Store type patch

Extract this archive at the root of the Food Truck Works project, preserving
its paths. Run `alembic current` and confirm the target database and revision
before running `alembic upgrade head`. The new revision is `3c9f0a7e5b21`.

Existing organization stores retain their names, timezones, active status,
orders, and tip settings. Their `store_type` is initially NULL, shown as
"Not set" in Store Settings. A manager must choose Food truck, Food trailer, Pop-up, 
Cart, Shop, or Catering when saving a store. The store type identifies the operation
represented by its Poynt store ID. It does not grant or limit booking.

No booking resource or availability rules are created by this patch. Future
event management can create one primary bookable resource per store initially,
with room to add other resources later.
