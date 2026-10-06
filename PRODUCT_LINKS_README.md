# Store product associations

This patch adds persistent associations to the existing product discovery feature.
It does not add Commerce authentication, Commerce catalog readers, price profiles,
or price pushes. Existing Poynt discovery source selection is unchanged.

## Install

Back up your current source. Replace product_discovery.py, routers/pricing.py,
and templates/pricing_products.html. Merge the new PricingProductLink class from
models.py into your current models.py if it has newer changes; preserve all other
models. Copy only the NEW migration b81e742d9c30_pricing_product_links.py into
alembic/versions. Do not replace any older migrations.

The new revision follows f32a91c07e64. Check `alembic heads` in your current checkout
before upgrading your local development database with `alembic upgrade head`.
This patch does not run migrations or change production data.

Fetch Latest Products establishes associations for unique exact SKUs that are
not already associated. Adding selected FTW products also saves their associations.
Use Link Product to explicitly select an existing FTW product when SKUs differ.
Each store may link its own provider product ID to the same FTW product.

Associations are scoped to organization, Poynt business, and store. A provider ID
and an FTW product may each be linked once within that scope. Existing associations
take precedence over SKU matching. SKU changes remain visible but do not interrupt
the association. Missing linked IDs stay missing, even if a new ID has the old SKU.
Removing an FTW definition removes its associations but changes no provider data.

IDs saved here belong to the current Poynt product discovery. Future Commerce
connectors must use a distinct provider identity namespace and explicitly migrate
or confirm associations; do not assume Poynt, Commerce REST, and GraphQL SKU IDs
are interchangeable. GraphQL price pushes also require the appropriate SKU/price
record IDs, not just a SKU group ID.

Validation: 27 focused tests, including SKU drift, missing/recreated IDs, manual
linking, duplicate protection, organization scoping, stale snapshots, CSRF,
and migration upgrade/downgrade against isolated SQLite databases.
