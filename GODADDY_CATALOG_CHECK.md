# Read-only GoDaddy catalog test

Copy tools/check_godaddy_catalog.py into your project. It uses existing httpx
and python-dotenv dependencies. It does not load application code, access the
database, update prices, or replace saved discovery results.

From your project folder in the same PowerShell session where the environment
variable is available:

```powershell
python tools/check_godaddy_catalog.py
```

If you put GODADDY_PERSONAL_ACCESS_TOKEN in your local .env instead:

```powershell
python tools/check_godaddy_catalog.py --dotenv .env
```

The script checks both known ICC stores, independently calling the documented
v2 GraphQL catalog and the observed legacy v1 REST products endpoint. It uses
the same store ID in the path and x-store-id header. REST responses must also
identify that store on each product. It never follows redirects or pagination
URLs supplied by the provider. REST token and GraphQL cursor pagination stay
separate. An endpoint failure is a diagnostic result, not a reason to switch
the application automatically.

Send the four console result lines and godaddy_catalog_check.json back for review.
The JSON contains product IDs, names, SKUs, statuses and prices, but no token,
request headers, cookies or raw provider responses. Product/SKU counts can differ
from UI product-group counts, especially when products have multiple variants.
Use ENE-PRETTYINPINK and a product with different prices across stores as checks.

The GraphQL query may reveal a schema difference; it reports controlled error
codes rather than printing provider messages. A 401/403 does not by itself prove
an endpoint is obsolete. It can indicate credentials, permission, eligibility,
or store access problems. Do not change OAuth configuration based on that alone.

This PAT is a local diagnostic credential. It is not a subscriber authorization
solution and is not automatically refreshed. Do not commit credentials or the
generated catalog report. Keep the current FTW discovery code unchanged until
the correct catalog source and supported authorization have been verified.
