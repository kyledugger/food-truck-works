# Store timezone reporting patch

Apply the included files to their matching project paths after the organization
store setup patch (`6a18b9e4c532`). No database migration is included here.

Configure a timezone for each active store in Dashboard > Store Settings. The
Orders report now interprets entered times in the selected store timezone. You
may select multiple stores when they share a timezone. For different zones,
select stores in one timezone per report. The tip submission report selects one
store at a time and defines its date range in that store timezone.

The current database `timestamp without time zone` values remain UTC-naive. This
patch adds explicit `Z` to tip timestamps sent to the browser and does not
reinterpret or rewrite existing rows. Converting those columns to PostgreSQL
`timestamptz` requires verification of production values and a separate data
migration. Existing Poynt order timestamps remain live API data, not stored
orders.

Run `python -m unittest discover -s tests -p test_store_time.py` from the project root. Check
Orders Today, a single store in another timezone, a mixed-timezone selection,
and the tip submission report's today/date filters after installing.
