# Explicit UTC timestamp storage

This patch follows Alembic revision `6a18b9e4c532` with
`8b7d2e9c4a61`. It converts application instant columns from PostgreSQL
`timestamp without time zone` to `timestamp with time zone`.

The production read-only audit showed populated report times of 07:00 UTC for
midnight Phoenix and tip activation at 2026-09-27 07:00 UTC. Existing ORM
defaults and writes use UTC. The migration explicitly interprets every legacy
value as UTC using `AT TIME ZONE 'UTC'`, independently of the database session
timezone. Nulls remain null. It changes no calendar-only date fields.

Review the migration and make a database backup before applying it to the
intended environment. Apply the migration and deploy the matching Python files
as one release. Never run the application code against a partially migrated
database. After verifying the target connection:

```powershell
alembic current
alembic upgrade head
python -m unittest discover -s tests -p test_store_time.py -v
python -m unittest discover -s tests -p test_account_security.py -v
```

The app no longer needs `Base.metadata.create_all()` on startup. Check the
Alembic configuration's `DATABASE_URL` before each environment. In particular,
`alembic/env.py` currently loads `.env` without overriding an already-set
PowerShell `DATABASE_URL`. Run the read-only timestamp audit again with the
appropriate `--env-file` to confirm the migrated column types and record counts.

Existing `submission_data` JSON already contains UTC ISO strings and is not
rewritten. Poynt orders are fetched live and are not stored by this migration.
