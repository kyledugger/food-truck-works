# Poynt Event Reports Application

run like this ```uvicorn main:app --reload```

Render Dashboard
https://dashboard.render.com/


Cloudflare Dashboard
https://dash.cloudflare.com/

app url
https://codelian-poynt.onrender.com/database-test

app url 
https://poynteventreports.onrender.com/database-test

Port 5432

## Account email configuration

Set these environment variables in Render:

- `POSTMARK_SERVER_TOKEN`: the Postmark server API token
- `EMAIL_FROM`: a sender address on a Postmark-verified domain
- `APP_BASE_URL`: the public origin, for example `https://foodtruckworks.com`

Before deploying account security changes, run `alembic upgrade head` against
the intended database. Revision `d4a1f6c82b30` creates the security-token table
and marks the accounts that exist at migration time as verified. Revision
`e7b2c4d91a60` adds the pending-email field used by the verified email-change
workflow. Revision `f1c3a8d72b40` adds account-level first and last names and
backfills users already linked to employee records. Review the target database
before running migrations. Do not store the Postmark token in this repository.

## Private organization registration

Production organization registration is closed to the general public. Set
`ORG_REGISTRATION_ACCESS_CODE` in Render to a randomly generated value of at
least 32 characters. Authorized production testing begins at
`/organization-registration-access`; successful entry opens `/register` for
that signed browser session for 30 minutes. Do not put the access code in a URL,
source code, logs, or this repository. Local development registration remains
available without the access code. Generate a suitable value with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.


Concurrent Poynt refresh protection

Copy poynt/client.py and poynt/connection.py into your application, replacing
those two files, then restart/redeploy. No migration or environment change.
The tests folder is optional.

Refresh checks reread the organization's current credentials under a database
row lock, hold that lock through the provider request and commit, and adopt the
result only after commit. PostgreSQL coordinates across workers/instances;
bounded thread locks additionally coordinate same-process development calls.
Blocking database waits and refresh requests run in a worker thread.

Four isolated concurrency/failure tests passed. No live Poynt or PostgreSQL
connection was used. PostgreSQL cross-process locking should be verified in
staging. SQLite does not provide cross-process SELECT FOR UPDATE protection.

This patch does not start the background refresher, change expired-token
behavior, or change the OAuth callback. Keep the lifespan change discussed
separately. A provider success followed by a failed database commit still
requires investigation because the rotated token may not have been saved.

Test from project root:
python -m unittest discover -s tests -p test_poynt_refresh_concurrency.py
