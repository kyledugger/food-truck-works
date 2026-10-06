Product discovery response parsing fix

Replace product_discovery.py and routers/pricing.py, then restart the app.
The tests file is optional. No migration is needed. Keep your corrected
f32a91c07e64 migration pointing to a47c9e25d136.

Accept plain JSON arrays, named products/catalogs lists, and content envelopes.
Previously the code rejected valid plain arrays after an HTTP 200 response.
Pagination safeguards and store scoping remain in place. Logs now include
controlled parser diagnostics without printing provider response bodies.

21 focused tests passed, including HTTP 200 plain-array handling and pagination.
The reported logs do not include the response body, so its exact format has not
been confirmed. Retry discovery; if it fails, share the new Product discovery
 diagnostic and Poynt collection shape rejected lines. Do not send tokens.
