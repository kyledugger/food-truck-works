# Pay Link and invoice diagnostic

Extract both Python files into the FTW project root and run in PowerShell:

    .\.venv\Scripts\python.exe .\trace_external_payments.py

Defaults: organization 1 and .env.local-prod-db. Uses the project's existing
httpx, psycopg, and python-dotenv dependencies. If the saved token has expired,
open FTW and let it refresh the connection before rerunning the diagnostic.

Upload external_payments_trace.json for analysis.

Examples (both Ice Cream Crush Truck):
- Pay Link: November 2, 2026; $367.54; displayed order #25665.
- Paid invoice #000060: September 12, 2026; $243.11.

Reads the saved connection and store mapping in a read-only database
transaction. Makes GET requests for orders and transactions for each Arizona
calendar day across the business, so unexpected store assignments remain
visible. Bounded to 5000 records per collection. Does not start the application,
refresh tokens, change the database, or change provider records.

Output includes allowlisted order/product, amount, timestamp, status, store,
and reference evidence. Excludes credentials, customer/contact details, card
data, funding sources, and processor responses. Treat it as business data.

Candidate matches use amount only and require review; they are not proof of
identity. The attachment flag only checks orders within that same day. A false
flag does not establish that a transaction lacks an older associated order.
This script diagnoses API coverage; it does not change dashboard revenue.

Validation: syntax compilation and mocked checks for pagination, sensitive
field exclusion, and denied-access responses. Not run against your account.
