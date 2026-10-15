# Trace the POS customer-name field

This standalone diagnostic uses existing saved Poynt credentials through `tools/check_poynt_products.py`. It performs only API GETs and a read-only database connection to retrieve the selected organization's credentials. It does not refresh tokens or modify FTW, Poynt, or the database. No migration or application deployment is required.

From the repository root, using your virtual environment, list the latest up to 20 orders from the last 24 hours:

```powershell
python tools/trace_poynt_customer.py --dotenv .env.local-prod-db --organization-id 1
```

Identify the test order by its number, timestamp, and store. Then replace YOUR_ORDER_ID below with the ID from that list:

```powershell
python tools/trace_poynt_customer.py --dotenv .env.local-prod-db --organization-id 1 --order-id YOUR_ORDER_ID
```

Alternatively, use `--order-number NUMBER` if that number uniquely identifies an order among the most recent 20. `--hours 48` widens the list window; `--expected-name "Other Test Name"` changes the search string. The default is `Kyle DuggerTest`.

The script writes `poynt_customer_trace.json` and refuses to overwrite existing output. Use `--output second_trace.json` to capture a later snapshot. If the saved token has expired, let FTW refresh it and rerun.

The report includes customer references and name fields on the full order, individually fetched transactions, and customer-by-ID records. Card details, addresses, email, and phone objects are omitted. The output still contains customer names and identifiers; review it before sharing. Only the selected order's referenced customers are fetched; the entire customer database is not downloaded.

Interpretation:

- A customer reference whose lookup returns the test name supports the hypothesis that FTW can resolve the name through that reference.
- If the name appears in the order itself, we may only need to map the existing field.
- A name in a current customer record does not establish that this particular order updated it. Comparing a later controlled before/after test would establish that behavior more clearly.
- HTTP 401/403 indicates a credentials/permissions limitation, not proof that the name is absent. A 404 could indicate a different customer-ID namespace.
- No customer reference or match leaves open the possibility that GoDaddy Commerce stores the name outside this Poynt response.
- First and last names may be separate fields, so inspect `name_fields` even when the full expected string has no literal match.

Poynt reference: https://docs.poynt.com/api-reference/index.html (Orders, Transactions, Customers / Get By Id).

Four mocked diagnostic tests passed. No live customer lookup was performed while preparing this script.
