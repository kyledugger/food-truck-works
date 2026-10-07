# Controlled terminal price propagation test

This script temporarily changes the LIVE Pretty in Pink Energy Drink price
by one cent. Run while neither store is serving customers. It uses the existing
Poynt connection and never uses the personal token. Database access remains
read-only; --action apply/restore changes only the provider price.

Extract both tools scripts into your project. No migration or app-file changes.
The test uses the exact Poynt product IDs from your report, verifies business
and store IDs, and requires a $7.00 truck / $9.00 popup baseline before applying.
Only /price/amount is replaced; JSON Patch tests check the expected currency
and amount first. If unsupported, the script reports the HTTP error; do not
remove these guards or switch to a full-product update as a workaround.

## Truck first

Check the current prices in both terminals and GoDaddy's catalog, then run:

```powershell
python tools/test_poynt_price.py --dotenv .env.local-prod-db --organization-id 1 --store truck
python tools/test_poynt_price.py --dotenv .env.local-prod-db --organization-id 1 --store truck --action apply
```

Expected truck price is $7.01; popup remains $9.00. The script saves
poynt_price_test_truck.json BEFORE attempting the write. Keep that file.

Check the selected store in spa.commerce.godaddy.com and the terminal item
price/new unsent cart. Do not complete a sale. Allow normal sync, then re-open
the item or use your normal terminal refresh. Record whether each view changed,
how long you waited, and whether a manual refresh was needed. Existing cart
prices may be cached. Check the other store remains unchanged. If either view
does not change, that is a result; do not make additional writes to force it.

You can rerun --action status (the default) to read both Poynt prices again.
It does not prove what the terminal is showing.

Restore after observation:

```powershell
python tools/test_poynt_price.py --dotenv .env.local-prod-db --organization-id 1 --store truck --action restore
```

Verify $7.00 has returned in all views before testing popup. Restoration stops
if the current price has been edited to something other than the test value.
If an error or timeout follows a write, do not delete the journal or blindly
rerun apply. Run status and inspect both stores; restore if the test price is
present. If provider restoration fails, use the GoDaddy interface to restore
the known original price and check the terminal as well.

## Popup second

Use the same commands with --store popup. Apply changes $9.00 to $9.01 and
restore returns it to $9.00. Truck should remain $7.00. The backup file is
poynt_price_test_popup.json. Do not test both stores simultaneously.

Send console output, the two journals, and observed catalog/terminal prices.
Journals contain product evidence and prices, not authentication credentials.
An API acceptance or read-back alone is not proof of terminal propagation.
No live update has been run by Codex.
