# Unfiltered Poynt products diagnostic

Extract tools/check_poynt_products.py into your project. No application files
need replacement and no migration is needed. Existing dependencies are used.

For organization 1 and your production-connected environment file:

```powershell
python tools/check_poynt_products.py --dotenv .env.local-prod-db --organization-id 1 --compare godaddy_catalog_check.json
```

Use your own organization ID if different. The --compare file is optional:

```powershell
python tools/check_poynt_products.py --dotenv .env.local-prod-db --organization-id 1
```

The explicitly selected environment file overrides inherited environment values.
The only database query reads the saved Poynt connection for that organization.
PostgreSQL is queried in a read-only transaction. No connection tokens are
refreshed, saved, or printed, and the application is not imported or started.
If the token expires, use your normal FTW connection flow to restore access
and rerun. This diagnostic does not make that change for you.

All product pages are fetched from the business products endpoint. No store,
catalog, status, or date filters are applied. Reports retain product IDs,
storeId, businessId, SKU, name, shortCode, price, status, source, externalId,
and creation/update times. Retired and unassigned records remain in the report.
Pagination failures abort rather than saving a partial report.

Send poynt_unfiltered_products.json and the console summary back for review.
The optional comparison shows exact SKU, case-insensitive SKU, or exact name
candidates against the earlier GraphQL snapshot. It does not guess vowel-stripped
aliases or establish identity from names. Multiple candidates and mismatched
store IDs are preserved as evidence. Price differences can also reflect the
different capture times. No product writes or price update tests are performed.
