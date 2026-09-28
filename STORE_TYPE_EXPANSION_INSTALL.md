# Cart and Food trailer store types

Extract this ZIP into the project root, preserving its paths. Run `alembic
current` against the intended database before `alembic upgrade head`. Revision
`59d6e1c8a403` follows the initial store type revision `3c9f0a7e5b21`.

The migration expands the `organization_stores.store_type` check constraint.
It does not alter existing stores or their configured types. The Store Settings
menu reads `STORE_TYPES`, so Cart and Food trailer appear automatically.

Downgrading requires reclassifying any Cart or Food trailer stores first.
