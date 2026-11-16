# Transaction notification experiment for newer POS software

## Purpose and limits

Determine whether a newer GoDaddy POS that does not emit order webhooks still
emits Poynt transaction webhooks. If so, that signal could trigger a timely
order lookup. This package is stage one: passive signal observation only.
It does not yet trigger order fetching or kitchen tickets, change polling,
change revenue calculations, or implement additional-payment reporting.

Existing order hooks remain registered and untouched. No migration is needed.
The listener verifies the signature, application, probe hook, organization,
and current business connection. It logs only correlation metadata and makes
no Poynt API requests or database writes. It performs a database SELECT per
accepted notification. There is no customer, card, payment-body, or secret
logging. Treat transaction/store identifiers as business data.

Registration/removal commands DO change the separate test webhook on Poynt.
They read the saved production connection using a read-only database
transaction, never refresh tokens, and never update the database. No live
registration, deployment, or production access has been performed by the author.

## Install

1. Extract the package into your FTW project, keeping paths. It adds files;
   it does not replace any working reporting or queue modules.
2. Run locally:

       .\.venv\Scripts\python.exe .\install_transaction_probe.py

   This adds two router-registration lines to main.py and backs up the exact
   original as main.py.before_transaction_probe. It is safe to rerun and
   preserves Windows line endings. Review/deploy this small change normally.
3. Generate a separate random signing secret (do not change POYNT_WEBHOOK_SECRET
   used by working order hooks). In PowerShell:

       .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"

   Keep it private. Configure these variables on your FTW hosting service:

       POYNT_TRANSACTION_PROBE_ENABLED=1
       POYNT_TRANSACTION_PROBE_ORGANIZATION_ID=1
       POYNT_TRANSACTION_PROBE_SECRET=<the new signing secret>

   POYNT_APP_ID should already be configured. The receiver fails closed until
   its expected hook ID is configured too. Do not run test sales yet.
4. In your local PowerShell shell, set the SAME secret for registration:

       $env:POYNT_TRANSACTION_PROBE_SECRET = '<the same signing secret>'

   From the project root, register a separate test hook:

       .\.venv\Scripts\python.exe .\manage_transaction_probe.py register --public-url https://foodtruckworks.com

   Use the actual public origin hosting your deployed FTW app if different.
   Defaults: organization 1, .env.local-prod-db for the saved Poynt connection.
   The script also loads .env without overwriting existing environment values.
   If the saved access token has expired, open FTW so its existing refresh
   logic runs, then retry. Never copy access tokens into commands.

   The script lists existing hooks, reuses an exact matching probe if present,
   and otherwise creates only TRANSACTION_CAPTURED / TRANSACTION_UPDATED on:

       /webhooks/poynt/transaction-probe/1

   It does NOT update or remove the existing order webhook. It saves a
   transaction_probe_registration.json journal without credentials or the
   signing secret. Keep the journal for rollback; do not commit it.
5. Copy the printed POYNT_TRANSACTION_PROBE_HOOK_ID value to the FTW hosting
   environment and restart/redeploy. The hook ID is not the signing secret.

The endpoint is disabled by default. This probe intentionally supports one
configured organization/hook at a time; it is not a subscriber-facing feature.

## Controlled live experiment

Keep current polling and the manual kitchen refresh button throughout.

For each test, record the store, POS software version, payment type, order
number, and approximate cashier completion time in Arizona time. Use actual
routine sales where practical; do not create unnecessary charges.

| Control/test | Observation wanted |
| --- | --- |
| Older POS, card sale | Confirm the new listener/registration works at all |
| Newer POS, card sale | Does the same transaction event reach FTW promptly? |
| Newer POS, cash sale | Does cash produce a usable event? Do not assume it does |
| Newer POS, split tender if available | How many notifications correspond to one order? |

In server logs, filter for TRANSACTION_PROBE. Each accepted notification logs
its event type, store ID, transaction ID, notification ID, UTC arrival time,
and provider-event age in milliseconds when an offset-aware timestamp exists.
Provider-event age is NOT cashier-to-kitchen latency: the provider may create
the event later than payment completion. No latency guarantee is implied.

Duplicate labels are best-effort within one worker process only; another
worker or restart may label a retry as new. This is fine for observation;
the real trigger would require durable, cross-worker de-duplication.

Send the relevant log lines plus the test's store/version/payment type and
completion time. If no notification appears, include any callback HTTP status
lines. A lack of events from both POS versions may indicate setup/permissions;
an old-POS control working while new POS remains silent is stronger evidence
of a platform coverage gap. An unsupported hook registration is also useful
evidence; the script reports its HTTP status without exporting provider bodies.

## Stage two, only after signal coverage is demonstrated

Measure order availability after the notification. A signal arriving early
does not guarantee the order has reached the readable API. Use bounded short
retries, coalesce events per store/transaction, match order references where
available, and retain periodic reconciliation. Ignore Pay Link / invoice
transactions for kitchen-ticket creation. Cash and offline sales need their
own measured fallback behavior. Do not reduce polling until this is proven.

This first-stage observer deliberately makes no extra API calls per sale.
The next stage would add measured order lookups, with a request budget, rather
than assume every transaction should trigger a full-day order download.

## Inspect / rollback

List only the matching probe registration and the count of order hooks:

    .\.venv\Scripts\python.exe .\manage_transaction_probe.py list --public-url https://foodtruckworks.com

Remove only the journaled probe (preserve the same origin and organization):

    .\.venv\Scripts\python.exe .\manage_transaction_probe.py remove --public-url https://foodtruckworks.com

The removal command verifies the exact ID, URL, application, business, and
event set before DELETE. It refuses to delete an unexpected hook. Remove
the hook before disabling the endpoint to avoid provoking delivery retries.
Then set POYNT_TRANSACTION_PROBE_ENABLED=0. Router files may remain installed.
To uninstall them, remove the two added main.py lines after reviewing any
subsequent main.py edits; do not blindly overwrite new work with the backup.

## Validation

Twelve mocked/unit tests cover signatures, disabled/misconfigured receivers,
cross-organization/business/hook rejection, event filtering, oversized/invalid
bodies, missing store metadata, timestamp offsets, allowlisted logging,
registration idempotence, and removal preserving the order hook. Installer
checks cover exact backups, repeat runs, and LF/CRLF preservation. No live
POS event or newer-POS notification coverage has been verified yet.
