# Products discovery

Built against source_package_1(3).zip. This patch adds Pricing > Products for
owners and managers. Employee, payroll, and store display accounts cannot
access it. This phase does not add price profiles or pricing pushes.

## Install

1. Back up your source and database as usual. Copy the new files into the
   corresponding directories. The patch includes updated main.py, models.py,
   and templates/dashboard.html. If you edited those since the source snapshot,
   merge only the additions from INTEGRATION.diff instead of overwriting them.
   Keep your current poynt/client.py, poynt/token.py, and poynt/connection.py.
2. Identify the target database before applying the migration. No development
   or production database was migrated while preparing this patch. In your
   intended environment, verify DATABASE_URL and run `alembic current`. The
   migration follows e25a7c03b914 and adds two tables; it does not alter existing
   products, orders, tip data, or historical SKU mapping.
3. In that same intended environment, apply `alembic upgrade head` using your
   normal migration/deployment workflow. Alembic's env.py loads .env directly;
   DOTENV_FILE alone does not select a different migration database. A known
   DATABASE_URL supplied by your chosen environment takes precedence.
4. Restart/deploy. Open Dashboard > Pricing > Products.

## First use

- Configure active stores in Store Settings and connect Poynt if needed.
- Choose the store with your correct current SKUs, click View Store, then
  Discover Products. Only active products are included; retired products stay
  out of the authoritative list. Discovery paginates products/catalogs and
  associates products using store ownership and store/terminal catalog IDs.
- Review SKU, product name, category, and price. Select eligible rows (or use
  Select all eligible visible products), then Add Selected SKUs to FTW.
- Choose the other store and discover it. Review unknown, missing, blank,
  duplicate, whitespace/length, and ownership-conflict flags. Exact SKU matching
  preserves case and leading zeros. No legacy SKU aliases or name-based guesses.
- Correct inconsistent SKUs/catalog assignments in Poynt, then Refresh Products.
  New unique SKUs can be explicitly admitted to FTW. Discovery never silently
  expands or renames your authoritative list.
- Expand Authoritative SKU list to inspect or remove accidental definitions.
  Removal affects FTW only. Reference names/categories are copied on admission;
  current store names remain visible separately and do not affect identity.

Discovery uses only GET requests for Poynt product/catalog/store data. Existing
credential refresh may still call the token endpoint. No product prices, taxes,
SKUs, catalogs, or names are written to Poynt. FTW stores only each store's latest
successful snapshot and the authoritative SKU definitions, not a history.
A failed discovery preserves previous results. A refreshed snapshot invalidates
older add-selection forms. Switching Poynt businesses invalidates old snapshots.
Shared catalog/product references are flagged for review before the later
pricing-push feature. If no products can be reliably associated with a store,
the page reports that and does not assume every business product belongs there.

## Validation

18 focused tests passed, covering exact/invalid/duplicate SKUs, pagination,
store and terminal catalog membership, shared products, retired products,
permissions, CSRF, organization scoping, stale selections, failed discovery,
changed Poynt business, persistence, removal, and isolated migration round-trip.
49 existing dashboard tests passed. Rendered Jinja template and JavaScript syntax
checks passed. No live Poynt API, live PostgreSQL, or browser visual test was run.

Run focused tests from the project root:
`python -m unittest discover -s tests -p test_product_discovery.py`
Tests use an isolated SQLite database and mocked Poynt. They never load your
production credentials. Price Profiles and Apply Pricing are labeled coming next.
